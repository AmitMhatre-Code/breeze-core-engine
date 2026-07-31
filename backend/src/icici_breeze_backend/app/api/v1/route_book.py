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
    LegModifyFinalizeRequest,
    LegModifyResponse,
    LegModifyStepRequest,
    LegModifyStepResponse,
    ParkedOrderCreateRequest,
    ParkedOrderIdsRequest,
    ParkedOrderListResponse,
    ParkedOrderPatchRequest,
)
from icici_breeze_backend.app.services.leg_order_redistribution import (
    LegModifyValidationError,
    LegOrderState,
    filled_floor,
    plan_leg_redistribution,
    plan_steps,
)
from icici_breeze_backend.audit.logger import AuditLogger, OperationType
from icici_breeze_backend.app.services.user_rate_limit_prefs import (
    get_icici_rate_limit_pause_seconds,
)
from icici_breeze_backend.app.domain.responses import BookDataResponse
from icici_breeze_backend.app.repositories import squareoff_rules as squareoff_repo
from icici_breeze_backend.app.services.broker_snapshot_cache import evict_broker_snapshot
from icici_breeze_backend.app.services.exit_rule_orders import split_rule_spawned_orders
from icici_breeze_backend.app.services.gtt_exit_order_mapping import (
    fetch_all_gtt_raw_rows,
    map_gtt_order_book_rows,
)
from icici_breeze_backend.app.services import gtt_order_book_cache
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
    rule_spawned_orders: list[dict] | None = None
    if orders["Status"] != 200:
        grouped_orders = None
        orders_failed = True
    elif orders.get("Success") is None:
        grouped_orders = None
    else:
        # NOTE: the fired→Completed REST backstop deliberately does NOT live here any
        # more. Reconciling off this book tied the check to whatever date range the user
        # happened to be browsing, and the default range is today — so an SG that fired on
        # an earlier day had none of its order_ids in these rows, which reads exactly like
        # "not executed yet" and stranded it permanently. It now runs in the exit-board
        # endpoint against a window anchored to each SG's own fire date
        # (`strategy_group_lifecycle.reconcile_fired_rules_for_user`). `rules_owning_orders`
        # below includes `completed`, so a just-healed SG's orders are still attributed to
        # it here.
        gtt_raw_rows = gtt_order_book_cache.get(user_id)
        if gtt_raw_rows is None:
            gtt_raw_rows = fetch_all_gtt_raw_rows(breeze, user_id)
            gtt_order_book_cache.set_rows(user_id, gtt_raw_rows)
        # `rules_owning_orders`, not the exit-board list: a `reset` SG's orders must revert
        # to displaying as independent individual orders in the main Order Book (spec
        # section 12) -- that is exactly how the user sees and cancels the orphans. The row
        # still keeps its order_ids; "association removed" is a display rule, not a data
        # rule, and that stored set still powers the orphan warning and bulk-cancel.
        kept_orders, rule_spawned_orders = split_rule_spawned_orders(
            squareoff_repo.rules_owning_orders(user_id),
            map_gtt_order_book_rows(gtt_raw_rows),
            orders["Success"],
        )
        grouped_orders = breeze.group_orders(
            user_id, {"Success": kept_orders}, fetch_ltp=False
        )

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
        rule_spawned_orders=rule_spawned_orders,
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
    AuditLogger(None).log_operation(
        context.user_id, OperationType.ORDER_CANCEL, "Order", body.order_id.strip(),
        action_status="success" if r.get("success") else "failure",
        error_details=r.get("error"),
    )
    pause = get_icici_rate_limit_pause_seconds(context.user_id)
    return JSONResponse({**r, "rate_limit_pause_seconds": pause})


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
    audit = AuditLogger(None)
    for i, order_ref in enumerate(orders):
        ok = i in success_idx
        err = None if ok else next((e for ref, e in failures if ref == order_ref), None)
        audit.log_operation(
            context.user_id, OperationType.ORDER_CANCEL, "Order", order_ref,
            action_status="success" if ok else "failure", error_details=err,
        )
    if success_idx:
        evict_broker_snapshot(context.user_id, context.broker_token or "")
    return json_redirect("/orders")


def _leg_orders_and_plan(
    orders: list, stock_code: str, expiry_date: str, exchange_code: str, new_quantity: str, new_price: str | None
):
    """Shared plan derivation for both step and finalize — pure/deterministic given the same
    inputs, so recomputing it on every request (rather than caching across steps) always
    reproduces the same ordered step list.
    """
    leg_orders = [
        LegOrderState(
            order_id=o.order_id,
            exchange_code=o.exchange_code,
            quantity=o.quantity,
            pending_quantity=o.pending_quantity,
            status=o.status,
        )
        for o in orders
    ]
    # new_quantity is the desired OPEN quantity (already-filled quantity is untouched and is
    # added back here so the pure planner, which reasons in terms of the leg's new total, gets
    # the number it expects).
    floor = filled_floor(leg_orders)
    new_total_qty = floor + int(new_quantity)
    qty_limits = breeze.fetch_qty_limits(stock_code, exchange_code=exchange_code)
    lot_size = breeze.fetch_lot_size(stock_code, expiry_date, exchange_code=exchange_code)
    qty_per_order = new_total_qty
    if qty_limits and lot_size:
        aligned = (max(1, int(qty_limits)) // int(lot_size)) * int(lot_size)
        if aligned > 0:
            qty_per_order = aligned

    current_price = next((o.price for o in orders if o.price), None)
    price_changed = (
        new_price is not None
        and str(new_price).strip() != ""
        and str(new_price) != str(current_price)
    )
    plan = plan_leg_redistribution(
        leg_orders, qty_per_order, new_total_qty, price_changed=price_changed
    )
    return leg_orders, plan, current_price


@router.post("/modify-leg-step", response_model=LegModifyStepResponse)
async def post_modify_leg_step(
    body: LegModifyStepRequest,
    context: RequestContext = Depends(get_request_context),
    _trading_ok: None = Depends(require_trading_not_revoked),
):
    """Execute one broker-call-sized step (cancel/modify/place) of a leg-modify plan, for
    live per-order progress in the Modify dialog. The client loops: call with step_index=0,1,2...
    advancing only on a non-rate-limited response, until step_index reaches total_steps, then
    calls /modify-leg-finalize once with the accumulated outcome.
    """
    if not context.broker_token:
        raise HTTPException(status_code=401, detail="ICICI broker token missing; re-login required")

    try:
        _leg_orders, plan, current_price = _leg_orders_and_plan(
            body.orders, body.stock_code, body.expiry_date, body.exchange_code, body.new_quantity, body.new_price
        )
    except LegModifyValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))

    steps = plan_steps(plan)
    pause = get_icici_rate_limit_pause_seconds(context.user_id)

    if body.step_index >= len(steps):
        return LegModifyStepResponse(total_steps=len(steps), step_index=body.step_index, done=True)

    step = steps[body.step_index]
    outcome = breeze.execute_leg_modification_step(
        context.user_id,
        step,
        contract={
            "product_type": body.product_type,
            "stock_code": body.stock_code,
            "action": body.action,
            "strike_price": body.strike_price,
            "right": body.right,
            "expiry_date": body.expiry_date,
            "exchange_code": body.exchange_code,
        },
        new_price=body.new_price,
        current_price=current_price,
    )
    if outcome["success"] and not outcome["rate_limited"]:
        evict_broker_snapshot(context.user_id, context.broker_token or "")

    return LegModifyStepResponse(
        total_steps=len(steps),
        step_index=body.step_index,
        done=False,
        op=step.kind,
        order_id=outcome["order_id"],
        quantity=outcome["quantity"],
        price=outcome["price"],
        success=outcome["success"],
        error=outcome["error"],
        rate_limited=outcome["rate_limited"],
        rate_limit_pause_seconds=pause,
    )


@router.post("/modify-leg-finalize", response_model=LegModifyResponse)
async def post_modify_leg_finalize(
    body: LegModifyFinalizeRequest,
    context: RequestContext = Depends(get_request_context),
    _trading_ok: None = Depends(require_trading_not_revoked),
):
    """Store messages/audit-adjacent side effects once the client has driven every step of a leg
    modify (see /modify-leg-step) to completion, using the outcome the client accumulated along
    the way rather than re-deriving broker state.
    """
    if not context.broker_token:
        raise HTTPException(status_code=401, detail="ICICI broker token missing; re-login required")

    leg_orders = [
        LegOrderState(
            order_id=o.order_id,
            exchange_code=o.exchange_code,
            quantity=o.quantity,
            pending_quantity=o.pending_quantity,
            status=o.status,
        )
        for o in body.orders
    ]
    old_quantity = sum(o.pending_quantity for o in leg_orders if o.is_open)
    current_price = next((o.price for o in body.orders if o.price), None)

    if body.rule_id and body.scrip_key:
        untouched = {o.order_id for o in body.orders} - set(body.cancelled_order_ids) - {
            m.order_id for m in body.modified
        }
        final_ids = list(untouched) + [m.order_id for m in body.modified] + [p.order_id for p in body.placed]
        squareoff_repo.update_leg_order_ids(body.rule_id, body.scrip_key, final_ids)

    contract_label = f"{body.stock_code}-{body.expiry_date}-{body.strike_price}-{body.right}"
    result = {
        "cancelled": body.cancelled_order_ids,
        "modified": [m.model_dump() for m in body.modified],
        "placed": [p.model_dump() for p in body.placed],
        "failures": [(f.ref, f.error) for f in body.failures],
        "all_ok": not body.failures,
    }
    messages = breeze.build_modify_leg_messages(
        contract_label,
        old_quantity,
        body.new_quantity,
        float(current_price) if current_price else None,
        float(body.new_price) if body.new_price else None,
        result,
    )
    breeze.store_messages(context.user_id, messages)
    if body.cancelled_order_ids or body.modified or body.placed:
        evict_broker_snapshot(context.user_id, context.broker_token or "")

    return LegModifyResponse(
        success=result["all_ok"],
        cancelled_order_ids=body.cancelled_order_ids,
        modified=body.modified,
        placed=body.placed,
        failures=body.failures,
    )


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
