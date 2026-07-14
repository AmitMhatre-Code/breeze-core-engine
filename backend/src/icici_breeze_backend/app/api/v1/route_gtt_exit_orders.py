"""Per-leg GTT OCO exit orders (Portfolio > individual leg > Exit Rule).

ICICI monitors the trigger/limit prices itself once placed, so unlike the group-level
square-off rule this app never persists these orders — GET always re-queries
`breeze.gtt_order_book` live and filters for the matching leg.
"""
from __future__ import annotations

import datetime

from fastapi import APIRouter, Depends, HTTPException

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.api.deps_license import require_trading_not_revoked
from icici_breeze_backend.app.auth.context import RequestContext, get_request_context
from icici_breeze_backend.app.core.strike import parse_strike
from icici_breeze_backend.app.core.timezone import today_ist_date
from icici_breeze_backend.app.domain.gtt_exit_order import (
    CancelGttExitOrderResponse,
    GttExitOrderLeg,
    GttExitOrderListResponse,
    GttExitOrderRecord,
    GttExitOrderStatusResponse,
    PlaceGttExitOrderRequest,
    PlaceGttExitOrderResponse,
)
from icici_breeze_backend.app.services import gtt_order_book_cache
from icici_breeze_backend.app.services.gtt_exit_order_mapping import (
    fetch_all_gtt_raw_rows,
    is_fully_cancelled,
    map_gtt_order_book_rows,
)
from icici_breeze_backend.app.services.processor import processor
from icici_breeze_backend.app.services.reference_data.scrip_master_sql import normalize_expiry_display
from icici_breeze_backend.audit.logger import AuditLogger, OperationType

router = APIRouter()
breeze = processor()


def _right_matches(a: str, b: str) -> bool:
    norm = lambda r: "call" if str(r or "").strip().lower().startswith("c") else "put"
    return norm(a) == norm(b)


def _expiry_matches(a: str, b: str) -> bool:
    try:
        return normalize_expiry_display(str(a or "")) == normalize_expiry_display(str(b or ""))
    except (ValueError, TypeError):
        return str(a or "").strip().lower() == str(b or "").strip().lower()


def _strike_matches(a, b) -> bool:
    pa, pb = parse_strike(a), parse_strike(b)
    return pa is not None and pa == pb


@router.post("", response_model=PlaceGttExitOrderResponse)
async def place_gtt_exit_order(
    body: PlaceGttExitOrderRequest,
    ctx: RequestContext = Depends(get_request_context),
    _: None = Depends(require_trading_not_revoked),
):
    close_action = cfg.SELL if str(body.action).strip().upper() == cfg.BUY.upper() else cfg.BUY

    response = breeze.place_gtt_oco_exit_order(
        ctx.user_id,
        product_type=body.product_type,
        stock_code=body.stock_code,
        exchange_code=body.exchange_code,
        strike_price=body.strike_price,
        right=body.right,
        quantity=body.quantity,
        expiry_date=body.expiry_date,
        close_action=close_action,
        target_trigger_price=body.target_trigger_price,
        target_limit_price=body.target_limit_price,
        stop_trigger_price=body.stop_trigger_price,
        stop_limit_price=body.stop_limit_price,
    )

    success = (response or {}).get("Success") or {}
    error = (response or {}).get("Error")
    detail = (
        f"{body.stock_code} {body.expiry_date} {body.strike_price}{body.right[:1].upper()} "
        f"target={body.target_trigger_price}/{body.target_limit_price} "
        f"stop={body.stop_trigger_price}/{body.stop_limit_price}"
    )
    if error or not success.get("gtt_order_id"):
        AuditLogger(None).log_operation(
            ctx.user_id,
            OperationType.GTT_EXIT_ORDER_PLACE_FAILED,
            "PortfolioGttExitOrder",
            "unknown",
            error_details=f"{detail} error={error}",
        )
        raise HTTPException(status_code=400, detail=error or "Failed to place GTT exit order")

    AuditLogger(None).log_operation(
        ctx.user_id,
        OperationType.GTT_EXIT_ORDER_PLACED,
        "PortfolioGttExitOrder",
        str(success.get("gtt_order_id")),
        error_details=detail,
    )
    gtt_order_book_cache.evict(ctx.user_id)
    return PlaceGttExitOrderResponse(
        gtt_order_id=str(success.get("gtt_order_id")), message=success.get("message")
    )


@router.get("", response_model=GttExitOrderStatusResponse)
async def get_gtt_exit_order_status(
    stock_code: str,
    expiry_date: str,
    strike_price: str,
    right: str,
    exchange_code: str = "NFO",
    ctx: RequestContext = Depends(get_request_context),
):
    from_date = str(today_ist_date() - datetime.timedelta(days=365)) + "T06:00:00.000Z"
    to_date = str(today_ist_date()) + "T06:00:00.000Z"
    response = breeze.get_gtt_order_book(ctx.user_id, exchange_code, from_date, to_date)
    if (response or {}).get("Error"):
        raise HTTPException(status_code=400, detail=response["Error"])

    matches = [
        row
        for row in (response or {}).get("Success") or []
        if (
            str(row.get("stock_code") or "").strip().upper() == stock_code.strip().upper()
            and _expiry_matches(row.get("expiry_date"), expiry_date)
            and _strike_matches(row.get("strike_price"), strike_price)
            and _right_matches(row.get("right"), right)
            and row.get("order_details")
        )
    ]
    # A fully-cancelled order shouldn't block placing a fresh one — treat it as if it
    # weren't there rather than showing a stale "Cancel" button for an already-dead order.
    live_matches = [row for row in matches if not is_fully_cancelled(row)]
    if not live_matches:
        return GttExitOrderStatusResponse(order=None)

    # `status` vocabulary beyond "Cancelled" isn't documented by ICICI's SDK — pass raw per-leg
    # status through for the frontend to display rather than guessing which strings mean "active".
    most_recent = max(live_matches, key=lambda row: str(row.get("order_datetime") or ""))
    legs = [
        GttExitOrderLeg(
            gtt_leg_type=leg.get("gtt_leg_type"),
            action=leg.get("action"),
            trigger_price=leg.get("trigger_price"),
            limit_price=leg.get("limit_price"),
            status=leg.get("status"),
        )
        for leg in most_recent.get("order_details") or []
    ]
    return GttExitOrderStatusResponse(
        order=GttExitOrderRecord(
            gtt_order_id=str((most_recent.get("order_details") or [{}])[0].get("gtt_order_id") or ""),
            gtt_type=most_recent.get("gtt_type"),
            order_datetime=most_recent.get("order_datetime"),
            legs=legs,
        )
    )


@router.get("/all", response_model=GttExitOrderListResponse)
async def list_all_gtt_exit_orders(ctx: RequestContext = Depends(get_request_context)):
    """Every GTT order on the account across both exchanges (Orders page > Profit
    Booking / Stop Loss), unlike the single-leg lookup above. This app has no other
    GTT-placing feature, so every row ICICI returns is treated as one of this app's
    Leg Exit Rules. Best-effort per exchange — one exchange failing doesn't blank the
    whole list, same as `fetch_all_gtt_raw_rows`'s doc.

    Shares `gtt_order_book_cache` with `GET /book/data` (route_book.py) so that a
    post-mutation refresh — which invalidates both the book and this list in the same
    burst — doesn't fire two real ICICI gttorder calls back to back and trip ICICI's
    own rate limit."""
    raw_rows = gtt_order_book_cache.get(ctx.user_id)
    if raw_rows is None:
        raw_rows = fetch_all_gtt_raw_rows(breeze, ctx.user_id)
        gtt_order_book_cache.set_rows(ctx.user_id, raw_rows)
    return GttExitOrderListResponse(orders=map_gtt_order_book_rows(raw_rows))


@router.delete("/{gtt_order_id}", response_model=CancelGttExitOrderResponse)
async def cancel_gtt_exit_order(
    gtt_order_id: str,
    exchange_code: str = "NFO",
    ctx: RequestContext = Depends(get_request_context),
    _: None = Depends(require_trading_not_revoked),
):
    response = breeze.cancel_gtt_order(ctx.user_id, exchange_code, gtt_order_id)
    success = (response or {}).get("Success") or {}
    error = (response or {}).get("Error")
    if error:
        raise HTTPException(status_code=400, detail=error)
    AuditLogger(None).log_operation(
        ctx.user_id, OperationType.GTT_EXIT_ORDER_CANCELLED, "PortfolioGttExitOrder", gtt_order_id
    )
    gtt_order_book_cache.evict(ctx.user_id)
    return CancelGttExitOrderResponse(ok=True, message=success.get("message"))
