import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.timezone import today_ist_date
from icici_breeze_backend.app.api.deps_license import require_trading_not_revoked
from icici_breeze_backend.app.api.frontend_redirect import json_redirect, redirect_to_frontend
from icici_breeze_backend.app.auth.context import (
    RequestContext,
    get_request_context,
    get_request_context_or_redirect,
)
from icici_breeze_backend.app.domain.order import (
    BookActionRequest,
    BookCancelCommitRequest,
    BookCancelOneRequest,
    BookGroupLtpRequest,
    BookGroupLtpResponse,
    ParkedOrderCreateRequest,
    ParkedOrderIdsRequest,
    ParkedOrderListResponse,
    ParkedOrderPatchRequest,
)
from icici_breeze_backend.app.services.icici_api_pacing import client_pause_for_rate_limit_result
from icici_breeze_backend.app.domain.responses import BookDataResponse
from icici_breeze_backend.app.services.broker_snapshot_cache import evict_broker_snapshot
from icici_breeze_backend.app.services.processor import processor

router = APIRouter()
breeze = processor()


@router.get("")
async def serve_landing(request: Request):
    q = request.url.query
    return redirect_to_frontend("/orders" + ("?" + q if q else ""))


@router.get("/data", response_model=BookDataResponse)
async def get_book_data(
    start: str | None = None,
    end: str | None = None,
    context: RequestContext = Depends(get_request_context),
):
    """Same data as legacy GET /book (messages, grouped order book, date range)."""
    user_id = context.user_id
    if not context.broker_token:
        raise HTTPException(
            status_code=401,
            detail="ICICI broker token missing; re-login required",
        )

    raw_messages = breeze.retrieve_messages(user_id)
    messages: list[dict] = list(raw_messages) if raw_messages else []

    start = (start or "").strip() or None
    end = (end or "").strip() or None
    if start is None or end is None:
        start = today_ist_date().strftime("%Y-%m-%d")
        start_date = datetime.datetime.strptime(start, "%Y-%m-%d")
        next_day = start_date
        while True:
            next_day += datetime.timedelta(days=1)
            if next_day.weekday() < 5:
                break
        end = next_day.strftime("%Y-%m-%d")

    orders = breeze.get_orders(user_id, start=start, end=end)
    orders_failed = False
    grouped_orders = None
    if orders["Status"] != 200:
        grouped_orders = None
        orders_failed = True
    elif orders.get("Success") is None:
        grouped_orders = None
    else:
        grouped_orders = breeze.group_orders(user_id, orders, fetch_ltp=False)

    if orders_failed:
        messages = list(messages)
        messages.append(
            {
                "type": cfg.WARNING,
                "message": (
                    "Orders could not be loaded for the selected date range. "
                    "Try a shorter range or try again."
                ),
            }
        )

    return BookDataResponse(
        messages=messages,
        grouped_orders=grouped_orders,
        start=start,
        end=end,
        orders_failed=orders_failed,
    )


@router.post("/group-ltp", response_model=BookGroupLtpResponse)
async def post_book_group_ltp(
    body: BookGroupLtpRequest,
    context: RequestContext = Depends(get_request_context),
):
    """Lazy batched LTP for order-book groups (after fast /book/data load)."""
    if not context.broker_token:
        raise HTTPException(
            status_code=401,
            detail="ICICI broker token missing; re-login required",
        )
    payload = [g.model_dump() for g in body.groups]
    ltps = breeze.fetch_group_ltps_batch(context.user_id, payload)
    return BookGroupLtpResponse(ltps=ltps)


@router.post("")
async def process_post(
    body: BookActionRequest,
    context: RequestContext = Depends(get_request_context_or_redirect),
    _trading_ok: None = Depends(require_trading_not_revoked),
):
    user_id = context.user_id

    if body.action == cfg.CANCEL and body.order_ids:
        details = body.cancel_details
        if details is not None and len(details) != len(body.order_ids):
            details = None
        cancel_meta = (
            [d.model_dump() for d in details]
            if details
            else None
        )
        messages = breeze.cancel_orders(
            user_id,
            body.order_ids,
            cancel_details=cancel_meta,
        )
        breeze.store_messages(user_id, messages)
        return json_redirect("/orders")
    return json_redirect(f"/orders?start={body.start or ''}&end={body.end or ''}")


@router.post("/cancel-one")
async def post_cancel_one(
    body: BookCancelOneRequest,
    context: RequestContext = Depends(get_request_context),
    _trading_ok: None = Depends(require_trading_not_revoked),
):
    if not context.broker_token:
        raise HTTPException(status_code=401, detail="ICICI broker token missing; re-login required")
    r = breeze.cancel_order_single(context.user_id, body.order_id.strip())
    return JSONResponse(
        {**r, "rate_limit_pause_seconds": client_pause_for_rate_limit_result(context.user_id, r)}
    )


@router.post("/cancel-commit")
async def post_cancel_commit(
    body: BookCancelCommitRequest,
    context: RequestContext = Depends(get_request_context),
    _trading_ok: None = Depends(require_trading_not_revoked),
):
    if not context.broker_token:
        raise HTTPException(status_code=401, detail="ICICI broker token missing; re-login required")
    orders = [r.order_ref for r in body.results]
    success_idx = [i for i, r in enumerate(body.results) if r.success]
    failures = [
        (r.order_ref, r.error or "Unknown error")
        for r in body.results
        if not r.success
    ]
    details = None
    if body.cancel_details is not None and len(body.cancel_details) == len(orders):
        details = [d.model_dump() for d in body.cancel_details]
    messages = breeze.build_cancel_order_messages(
        success_idx, failures, orders, details
    )
    breeze.store_messages(context.user_id, messages)
    if success_idx:
        evict_broker_snapshot(context.user_id, context.broker_token or "")
    return json_redirect("/orders")


@router.get("/parked-orders", response_model=ParkedOrderListResponse)
async def list_parked_orders(
    context: RequestContext = Depends(get_request_context),
):
    """User-scoped parked (draft) orders for Order Book."""
    rows = breeze.list_parked_orders(context.user_id)
    return ParkedOrderListResponse(orders=rows)


@router.post("/parked-orders", response_model=ParkedOrderListResponse)
async def create_parked_orders(
    body: ParkedOrderCreateRequest,
    context: RequestContext = Depends(get_request_context),
    _trading_ok: None = Depends(require_trading_not_revoked),
):
    breeze.create_parked_orders(
        context.user_id,
        body.items,
        body.replace_ids,
    )
    return ParkedOrderListResponse(
        orders=breeze.list_parked_orders(context.user_id)
    )


@router.patch("/parked-orders/{order_id}")
async def patch_parked_order(
    order_id: str,
    body: ParkedOrderPatchRequest,
    context: RequestContext = Depends(get_request_context),
    _trading_ok: None = Depends(require_trading_not_revoked),
):
    patch = body.model_dump(exclude_unset=True)
    kw: dict = {}
    if "quantity" in patch and patch["quantity"] is not None:
        kw["quantity"] = patch["quantity"]
    if "price" in patch and patch["price"] is not None:
        kw["price"] = patch["price"]
    if "chunk_qty" in patch:
        v = patch["chunk_qty"]
        kw["chunk_qty"] = "" if v is None else str(v)
    row = breeze.update_parked_order(
        context.user_id,
        order_id.strip(),
        **kw,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Parked order not found or not owned")
    return row


@router.delete("/parked-orders/{order_id}")
async def delete_parked_order_route(
    order_id: str,
    context: RequestContext = Depends(get_request_context),
    _trading_ok: None = Depends(require_trading_not_revoked),
):
    ok = breeze.delete_parked_order(context.user_id, order_id.strip())
    if not ok:
        raise HTTPException(status_code=404, detail="Parked order not found or not owned")
    return JSONResponse({"ok": True})


@router.post("/parked-orders/delete-many")
async def delete_parked_orders_many(
    body: ParkedOrderIdsRequest,
    context: RequestContext = Depends(get_request_context),
    _trading_ok: None = Depends(require_trading_not_revoked),
):
    n = breeze.delete_parked_orders(context.user_id, body.ids)
    return JSONResponse({"deleted": n})
