from fastapi import APIRouter, Header
from fastapi import Request, Depends, HTTPException
from fastapi.responses import JSONResponse
import logging
from icici_breeze_backend.app.services.processor import processor
import icici_breeze_backend.app.core.config as cfg
import json
from icici_breeze_backend.app.auth.context import (
    get_request_context,
    get_request_context_or_redirect,
    RequestContext,
)
from icici_breeze_backend.app.domain.order import (
    BreakOrderChunkRequest,
    BreakOrderFinalizeRequest,
    OrderFormRequest,
)
from icici_breeze_backend.app.services.user_rate_limit_prefs import (
    get_icici_rate_limit_pause_seconds,
)
from icici_breeze_backend.app.domain.responses import IciciApiResponse, OrderDetailResponse
from icici_breeze_backend.audit.logger import AuditLogger
from icici_breeze_backend.concurrency.idempotency import idempotency_store, IdempotencyResult
from icici_breeze_backend.app.api.error_utils import raise_route_errors
from icici_breeze_backend.app.api.frontend_redirect import redirect_to_frontend, json_redirect, map_legacy_html_path_to_spa


logger = logging.getLogger(__name__)
router = APIRouter()
breeze = processor()


def _order_buy_sell_gate_errors(user_id: str) -> list[dict]:
    """Pre-flight checks for placing orders (matches legacy /order POST gate)."""
    errors: list[dict] = []
    customer = breeze.get_customer_details(user_id)
    if customer is None:
        errors.append(
            {
                "location": "route_order gate get_customer_details",
                "contents": "get_customer_details() returned None",
            }
        )
    elif customer["Status"] != 200:
        errors.append(
            {
                "location": "route_order gate get_customer_details",
                "contents": "customer['status'] = "
                + str(customer["Status"])
                + " and customer['error'] = "
                + customer.get("Error", ""),
            }
        )

    margin = breeze.get_margin_situation(user_id, target_margin_ute=100)
    if margin["Status"] != 200:
        errors.append(
            {
                "location": "route_order gate get_margin_situation",
                "contents": "margin['status'] = "
                + str(margin["Status"])
                + " and margin['error'] = "
                + margin.get("Error", ""),
            }
        )

    stock_codes = breeze.fetch_stock_codes()
    if len(stock_codes) == 0:
        errors.append(
            {
                "location": "route_order gate fetch_stock_codes",
                "contents": "stock_codes = " + json.dumps(stock_codes),
            }
        )
    return errors


@router.get("")
async def serve_landing(request: Request):
    q = request.url.query
    return redirect_to_frontend("/orders" + ("?" + q if q else ""))


def _return_idempotent_response(stored: IdempotencyResult):
    if stored.status_code == 302:
        try:
            data = json.loads(stored.response_body.decode())
            url = data.get("redirect", "/orders")
            return JSONResponse({"redirect": map_legacy_html_path_to_spa(url)})
        except Exception as e:
            logger.warning("Idempotent response parse failed: %s", e)
            return JSONResponse({"redirect": "/orders"})
    return JSONResponse(status_code=stored.status_code, content=json.loads(stored.response_body.decode()) if stored.response_body else {})


@router.post("")
async def process_post(
    body: OrderFormRequest,
    context: RequestContext = Depends(get_request_context_or_redirect),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    user_id = context.user_id

    if idempotency_key and (body.action == cfg.BUY or body.action == cfg.SELL):
        stored = idempotency_store.retrieve_result(idempotency_key, user_id)
        if stored:
            return _return_idempotent_response(stored)
    if not context.broker_token:
        raise HTTPException(status_code=401, detail="ICICI broker token missing; re-login required")
    error = {}

    customer = breeze.get_customer_details(user_id)
    if customer is None:
        error["location"] = "In route_order.py --> @router.post() --> process_post() when processing output from get_customer_details()"
        error["contents"] = "get_customer_details() returned None"
        breeze.store_error(error)
    elif customer["Status"] != 200:
        error["location"] = "In route_order.py --> @router.post() --> process_post() when processing output from get_customer_details()"
        error["contents"] = "customer['status'] = " + str(customer["Status"]) + " and customer['error'] = " + customer.get("Error", "")
        breeze.store_error(error)

    margin = breeze.get_margin_situation(user_id, target_margin_ute=100)
    if margin["Status"] != 200:
        error["location"] = "In route_order.py --> @router.post() --> process_post() when processing output from get_margin_situation()"
        error["contents"] = "margin['status'] = " + str(margin["Status"]) + " and margin['error'] = " + margin.get("Error", "")
        breeze.store_error(error)

    stock_codes = breeze.fetch_stock_codes()
    if len(stock_codes) == 0 and body.action not in (cfg.BUY, cfg.SELL, cfg.QUOTE):
        error["location"] = "In route_order.py --> @router.post() --> process_post() when processing output from fetch_stock_codes()"
        error["contents"] = "stock_codes = " + json.dumps(stock_codes)
        breeze.store_error(error)

    errors = breeze.retrieve_errors()
    if len(errors) == 0:
        if body.action == cfg.CLEAR:
            return json_redirect(f"/order?exchange_code={body.exchange_code or cfg.NFO}")

        if body.action == cfg.QUOTE:
            return json_redirect(
                f"/order?action={body.action}&exchange_code={body.exchange_code or cfg.NFO}&stock_code={body.stock_code or ''}"
                f"&expiry_date={body.expiry_date or ''}&product_type={body.product_type or ''}&right={body.right or ''}"
                f"&strike_price={body.strike_price or ''}&quantity={body.quantity or ''}"
                f"&buy_button_state={body.buy_button_state or ''}&sell_button_state={body.sell_button_state or ''}"
            )

        if body.action == cfg.BUY or body.action == cfg.SELL:
            req = body.to_place_request()
            messages = breeze.break_order(
                user_id,
                req.stock_code,
                req.expiry_date,
                req.product_type,
                req.right,
                req.strike_price,
                req.quantity,
                req.price,
                req.action,
                exchange_code=body.exchange_code or cfg.NFO,
            )
            breeze.store_messages(user_id, messages)
            redirect_url = "/book"
            if idempotency_key:
                body = json.dumps({"redirect": redirect_url}).encode()
                idempotency_store.store_result(idempotency_key, user_id, "place_order", body, 302)
            return json_redirect(redirect_url)
    raise_route_errors(errors, log_context="route_order.process_post")


@router.post("/break-chunk")
async def post_break_chunk(
    body: BreakOrderChunkRequest,
    context: RequestContext = Depends(get_request_context),
):
    if not context.broker_token:
        raise HTTPException(status_code=401, detail="ICICI broker token missing; re-login required")
    if body.chunk_index == 0:
        gate = _order_buy_sell_gate_errors(context.user_id)
        if gate:
            raise_route_errors(gate, log_context="route_order.post_break_chunk")
    pause = get_icici_rate_limit_pause_seconds(context.user_id)
    out = breeze.break_order_place_chunk(
        context.user_id,
        body.stock_code,
        body.expiry_date,
        body.product_type,
        body.right,
        body.strike_price,
        body.total_qty,
        body.price,
        body.action,
        body.exchange_code or cfg.NFO,
        body.chunk_index,
    )
    out["rate_limit_pause_seconds"] = pause
    return JSONResponse(out)


@router.post("/break-finalize")
async def post_break_finalize(
    body: BreakOrderFinalizeRequest,
    context: RequestContext = Depends(get_request_context),
):
    if not context.broker_token:
        raise HTTPException(status_code=401, detail="ICICI broker token missing; re-login required")
    try:
        price_f = float(str(body.price).strip() or 0)
    except (TypeError, ValueError):
        price_f = 0.0
    contract_label = (
        f"{body.stock_code}-{body.expiry_date}-{body.strike_price}-{body.right}"
    )
    messages = breeze.break_order_finalize_user_messages(
        contract_label,
        price_f,
        list(body.success_quantities),
        list(body.danger_lines),
    )
    breeze.store_messages(context.user_id, messages)
    return json_redirect("/book")


@router.get("/data", response_model=IciciApiResponse)
async def get_orders_api(
    ctx: RequestContext = Depends(get_request_context),
    offset: int = 0,
    limit: int = 100,
    start_date: str | None = None,
    end_date: str | None = None,
):
    user_id = ctx.user_id
    if not ctx.broker_token:
        raise HTTPException(status_code=401, detail="ICICI broker token missing; re-login required")
    token_str = ctx.broker_token

    from icici_breeze_backend.app.services.processor import get_orders_realtime

    data = get_orders_realtime(
        broker_token=token_str,
        user_id=user_id,
        start_date=start_date,
        end_date=end_date,
    )
    AuditLogger(None).log_order_access(user_id, "list")
    return IciciApiResponse.model_validate(data)


@router.get("/data/{order_id}", response_model=OrderDetailResponse)
async def get_order_detail_api(order_id: str, ctx: RequestContext = Depends(get_request_context)):
    user_id = ctx.user_id
    order = breeze.get_order_detail(user_id, order_id)
    AuditLogger(None).log_order_access(user_id, order_id)
    if order.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    return OrderDetailResponse.model_validate(order)
