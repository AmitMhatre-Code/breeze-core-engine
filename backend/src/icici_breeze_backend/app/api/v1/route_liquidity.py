"""Order-book liquidity checks (docs/liquidity-checks-plan.md).

Mounted at `/api/settings/liquidity`, like Storage (#44), so the existing `/api/settings/:path*`
rewrite and nginx's `/api/` block already reach it -- no proxy entries to add.

`POST /check` is what the order tickets poll while a quantity is entered: it reads the in-process
book store and the settings row, and pins each leg's depth room as a lookup so a ticket has depth
even when chain-wide depth is capped. It never calls ICICI's REST API. A plain `def`, so the
socket subscribe runs on the threadpool (#61).
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from icici_breeze_backend.app.api.deps import get_current_user
from icici_breeze_backend.app.auth.context import RequestContext
from icici_breeze_backend.app.services.liquidity import check as liquidity_check
from icici_breeze_backend.app.services.liquidity import settings as liquidity_settings
from icici_breeze_backend.app.services.liquidity import subscriptions as depth_subs

router = APIRouter()


class CheckLeg(BaseModel):
    ref: str
    strike: float
    right: str
    side: Optional[Literal["Buy", "Sell"]] = None
    quantity: int = Field(ge=0)


class CheckBody(BaseModel):
    exchange_code: str
    stock_code: str
    expiry_display: str
    lot_size: Optional[int] = None
    legs: list[CheckLeg] = Field(default_factory=list, max_length=40)


class SettingsBody(BaseModel):
    max_deviation_pct: Optional[float] = None
    min_deviation_ticks: Optional[int] = None
    ltp_stale_seconds: Optional[int] = None


def _settings_response() -> dict[str, Any]:
    return {
        **liquidity_settings.load_liquidity_settings().as_dict(),
        "bounds": liquidity_settings.bounds(),
        "depth": depth_subs.status(),
    }


def _lot_size(body: CheckBody) -> int:
    if body.lot_size and body.lot_size > 0:
        return int(body.lot_size)
    from icici_breeze_backend.app.services.processor import processor

    try:
        return int(
            processor().fetch_lot_size(body.stock_code, body.expiry_display, exchange_code=body.exchange_code)
            or 1
        )
    except Exception:  # noqa: BLE001
        return 1


@router.post("/check")
def liquidity_check_legs(body: CheckBody, ctx: RequestContext = Depends(get_current_user)) -> dict[str, Any]:
    legs = [l for l in body.legs if l.quantity > 0]
    if legs and liquidity_check._market_open():
        from icici_breeze_backend.app.services.processor import processor

        liquidity_check.pin_depth(
            processor(), ctx.user_id, body.exchange_code, body.stock_code, body.expiry_display,
            {(l.strike, l.right) for l in legs},
        )
    verdicts = liquidity_check.check_legs(
        body.exchange_code,
        body.stock_code,
        body.expiry_display,
        [
            liquidity_check.LegQuery(
                ref=l.ref, strike=l.strike, right=l.right, side=l.side, quantity=l.quantity
            )
            for l in legs
        ],
        lot_size=_lot_size(body),
    )
    return {"legs": verdicts, "settings": liquidity_settings.load_liquidity_settings().as_dict()}


@router.get("")
def liquidity_settings_get(_: RequestContext = Depends(get_current_user)) -> dict[str, Any]:
    return _settings_response()


@router.put("")
def liquidity_settings_put(body: SettingsBody, _: RequestContext = Depends(get_current_user)) -> dict[str, Any]:
    try:
        liquidity_settings.save_liquidity_settings(
            max_deviation_pct=body.max_deviation_pct,
            min_deviation_ticks=body.min_deviation_ticks,
            ltp_stale_seconds=body.ltp_stale_seconds,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return _settings_response()
