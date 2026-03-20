from fastapi import APIRouter, Header
from fastapi import Request, Depends, HTTPException
from fastapi import responses
from fastapi import status
from fastapi import Form
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse as FRedirectResponse, JSONResponse
import logging
from app.services.processor import processor
import app.core.config as cfg
import json
from app.auth.context import get_request_context, get_request_context_or_redirect, RequestContext
from app.domain.order import OrderFormRequest
from app.domain.responses import IciciApiResponse, OrderDetailResponse
from audit.logger import AuditLogger
from concurrency.idempotency import idempotency_store, IdempotencyResult
from app.api.error_utils import render_error_page
from app.api.v1.route_admin import get_common_template_vars


def get_order_form_request(
    product_type: str | None = Form(None),
    stock_code: str | None = Form(None),
    exchange_code: str | None = Form(None),
    expiry_date: str | None = Form(None),
    right: str | None = Form(None),
    strike_price: str | None = Form(None),
    quantity: str | None = Form(None),
    price: str | None = Form(None),
    action: str = Form(...),
    buy_button_state: str | None = Form(None),
    sell_button_state: str | None = Form(None),
) -> OrderFormRequest:
    return OrderFormRequest(
        product_type=product_type,
        stock_code=stock_code,
        exchange_code=exchange_code,
        expiry_date=expiry_date,
        right=right,
        strike_price=strike_price,
        quantity=quantity,
        price=price,
        action=action,
        buy_button_state=buy_button_state,
        sell_button_state=sell_button_state,
    )

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="templates")
router = APIRouter()
breeze = processor()

@router.get("")
async def serve_landing(
    request: Request,
    context: RequestContext = Depends(get_request_context_or_redirect),
    product_type: str | None = None,
    stock_code: str | None = None,
    exchange_code: str | None = None,
    expiry_date: str | None = None,
    right: str | None = None,
    position: str | None = None,
    strike_price: str | None = None,
    quantity: str | None = None,
    action: str | None = None,
    buy_button_state: str | None = None,
    sell_button_state: str | None = None
):
    """Serve order page for authenticated user with JWT context.
    
    FR-001: User data isolated by user_id from JWT token.
    FR-002: RequestContext extracted from JWT, not session.
    FR-005: Routes use context to fetch user-specific data.
    FR-006: Audit log order access.
    """
    user_id = context.user_id
    if not context.broker_token:
        raise HTTPException(status_code=401, detail="ICICI broker token missing; re-login required")
    error = {}
    selected_exchange_code = exchange_code or cfg.NFO

    customer = breeze.get_customer_details(user_id)
    if customer is None:
        error['location'] = "In route_order.py --> @router.get() --> serve_landing() when processing output from get_customer_details()"
        error['contents'] = f"get_customer_details() returned None for user_id = {user_id}"
        breeze.store_error(error)
    elif customer['Status'] != 200:
        error['location'] = "In route_order.py --> @router.get() --> serve_landing() when processing output from get_customer_details()"
        error['contents'] = f"customer['status'] = {customer['Status']} and customer['error'] = {customer.get('Error', 'unknown')}"
        breeze.store_error(error)

    margin = breeze.get_margin_situation(user_id, target_margin_ute=100)
    if margin['Status'] != 200:
        error['location'] = "In route_order.py --> @router.get() --> serve_landing() when processing output from get_margin_situation()"
        error['contents'] = f"margin['status'] = {margin['Status']} and margin['error'] = {margin.get('Error', 'unknown')}"
        breeze.store_error(error)

    stock_codes = breeze.fetch_stock_codes(selected_exchange_code)
    if len(stock_codes) == 0:
        error['location'] = "In route_order.py --> @router.get() --> serve_landing() when processing output from get_stock_codes()"
        error['contents'] = f"stock_codes = {json.dumps(stock_codes) if stock_codes else 'empty'}"
        breeze.store_error(error)

    if action == cfg.SQUAREOFF:
        if position == cfg.BUY:
            action = cfg.SELL
        else:
            action = cfg.BUY

    if action == cfg.QUOTE and right and strike_price:
        quote = breeze.get_quote(
            user_id,
            stock_code=stock_code,
            expiry_date=expiry_date,
            product_type=product_type,
            right=right,
            strike_price=strike_price,
            exchange_code=selected_exchange_code,
        )
        if quote['Status'] != 200:
            error['location'] = "In route_order.py --> @router.get() --> serve_landing() when processing output from get_quote()"
            error['contents'] = quote['Error']
            breeze.store_error(error)
            quote = None
        else:
            quote = quote['Success']
    else:
        quote = None

    # Full option chain for order page: when stock_code + expiry_date present, fetch CE+PE chain
    option_chain = None
    if stock_code and expiry_date and not (action == cfg.SELL or action == cfg.BUY):
        chain_result = breeze.get_full_option_chain(
            user_id,
            stock_code=stock_code,
            exchange_code=selected_exchange_code,
            expiry_date=expiry_date,
        )
        if chain_result.get("Status") == 200:
            option_chain = chain_result.get("Success")
        else:
            error['location'] = "In route_order.py --> serve_landing() get_full_option_chain"
            error['contents'] = chain_result.get("Error", "Failed to fetch option chain")
            breeze.store_error(error)

    if action == cfg.SELL or action == cfg.BUY:
        order = {}
        order['product_type'] = product_type
        order['stock_code'] = stock_code
        order['expiry_date'] = expiry_date
        order['right'] = right
        order['buy_sell'] = action
        order['strike_price'] = strike_price
        order['quantity'] = quantity
        order['exchange_code'] = selected_exchange_code
    else:
        order = None
    
    # FR-006: Audit log order access
    AuditLogger(None).log_order_access(user_id, "browse")
    
    errors = breeze.retrieve_errors()
    if len(errors) == 0:
        stock_codes_json = json.dumps(stock_codes) if stock_codes else "null"
        return templates.TemplateResponse("order.html", {
            "request": request,
            "is_logged_in": True,
            "login_url": None,
            "active": "order",
            "customer": customer,
            "margin": margin,
            "stock_codes": stock_codes,
            "stock_codes_json": stock_codes_json,
            "order": order,
            "quote": quote,
            "option_chain": option_chain,
            "exchange_code": selected_exchange_code,
            "quantity": quantity,
            "buy_button_state": buy_button_state,
            "sell_button_state": sell_button_state,
            "user_id": user_id,
            **get_common_template_vars(context),
        })
    else:
        return render_error_page(request, errors, active="order", log_context="route_order.serve_landing")
    
def _return_idempotent_response(stored: IdempotencyResult):
    """Return stored idempotent response (Phase 6 T089)."""
    if stored.status_code == 302:
        try:
            data = json.loads(stored.response_body.decode())
            url = data.get("redirect", "/order")
            return responses.RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)
        except Exception as e:
            logger.warning("Idempotent response parse failed: %s", e)
            return responses.RedirectResponse(url="/order", status_code=status.HTTP_302_FOUND)
    return JSONResponse(status_code=stored.status_code, content=json.loads(stored.response_body.decode()) if stored.response_body else {})


@router.post("")
async def process_post(
    request: Request,
    context: RequestContext = Depends(get_request_context_or_redirect),
    form: OrderFormRequest = Depends(get_order_form_request),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    """Process order POST with optional idempotency key for exactly-once semantics (Phase 6 T090)."""
    user_id = context.user_id

    # Idempotency: return stored result if same key was already processed
    if idempotency_key and (form.action == cfg.BUY or form.action == cfg.SELL):
        stored = idempotency_store.retrieve_result(idempotency_key, user_id)
        if stored:
            return _return_idempotent_response(stored)
    if not context.broker_token:
        raise HTTPException(status_code=401, detail="ICICI broker token missing; re-login required")
    error = {}

    customer = breeze.get_customer_details(user_id)
    if customer is None:
        error['location'] = "In route_order.py --> @router.post() --> process_post() when processing output from get_customer_details()"
        error['contents'] = "get_customer_details() returned None"
        breeze.store_error(error)
    elif customer['Status'] != 200:
        error['location'] = "In route_order.py --> @router.post() --> process_post() when processing output from get_customer_details()"
        error['contents'] = "customer['status'] = "+str(customer['Status'])+" and customer['error'] = "+customer.get('Error', '')
        breeze.store_error(error)

    margin = breeze.get_margin_situation(user_id,target_margin_ute=100)
    if margin['Status'] != 200:
        error['location'] = "In route_order.py --> @router.post() --> process_post() when processing output from get_margin_situation()"
        error['contents'] = "margin['status'] = "+str(margin['Status'])+" and margin['error'] = "+margin.get('Error', '')
        breeze.store_error(error)

    stock_codes = breeze.fetch_stock_codes()
    # For Buy/Sell we only need form data and break_order; for Quote we only redirect with params. Skip empty stock_codes as fatal so redirects succeed.
    if len(stock_codes) == 0 and form.action not in (cfg.BUY, cfg.SELL, cfg.QUOTE):
        error['location'] = "In route_order.py --> @router.post() --> process_post() when processing output from fetch_stock_codes()"
        error['contents'] = "stock_codes = "+json.dumps(stock_codes)
        breeze.store_error(error)

    errors = breeze.retrieve_errors()
    if len(errors) == 0:
        if form.action == cfg.CLEAR:
            return responses.RedirectResponse(f"/order?exchange_code={form.exchange_code or cfg.NFO}", status_code=status.HTTP_302_FOUND)

        if form.action == cfg.QUOTE:
            return responses.RedirectResponse(
                url=f"/order?action={form.action}&exchange_code={form.exchange_code or cfg.NFO}&stock_code={form.stock_code or ''}&expiry_date={form.expiry_date or ''}&product_type={form.product_type or ''}&right={form.right or ''}&strike_price={form.strike_price or ''}&quantity={form.quantity or ''}&buy_button_state={form.buy_button_state or ''}&sell_button_state={form.sell_button_state or ''}",
                status_code=status.HTTP_302_FOUND,
            )

        if form.action == cfg.BUY or form.action == cfg.SELL:
            req = form.to_place_request()
            messages = breeze.break_order(
                user_id, req.stock_code, req.expiry_date, req.product_type, req.right,
                req.strike_price, req.quantity, req.price, req.action,
                exchange_code=form.exchange_code or cfg.NFO,
            )
            breeze.store_messages(user_id, messages)
            redirect_url = "/book"
            if idempotency_key:
                body = json.dumps({"redirect": redirect_url}).encode()
                idempotency_store.store_result(idempotency_key, user_id, "place_order", body, 302)
            return responses.RedirectResponse(redirect_url, status_code=status.HTTP_302_FOUND)
    return render_error_page(request, errors, active="order", log_context="route_order.process_post")


# JWT-protected API endpoints for orders (T081 - real-time from ICICI API)
@router.get("/data", response_model=IciciApiResponse)
async def get_orders_api(
    ctx: RequestContext = Depends(get_request_context),
    offset: int = 0,
    limit: int = 100,
    start_date: str | None = None,
    end_date: str | None = None,
):
    """Return list of orders for authenticated user with pagination (real-time from ICICI API).
    
    Fetches real-time order history directly from ICICI Breeze API.
    No database caching - always fresh data (FR-010).
    """
    user_id = ctx.user_id
    if not ctx.broker_token:
        raise HTTPException(status_code=401, detail="ICICI broker token missing; re-login required")
    token_str = ctx.broker_token

    from app.services.processor import get_orders_realtime
    
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
    """Return detail for a specific order ensuring ownership."""
    user_id = ctx.user_id
    order = breeze.get_order_detail(user_id, order_id)
    AuditLogger(None).log_order_access(user_id, order_id)
    if order.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    return OrderDetailResponse.model_validate(order)
