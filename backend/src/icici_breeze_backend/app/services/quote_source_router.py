"""Route option chain quotes to websocket, bhavcopy, or ICICI REST."""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any, TYPE_CHECKING

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.market_hours import is_india_market_open
from icici_breeze_backend.app.core.timezone import IST, now_ist
from icici_breeze_backend.app.services.reference_data.bhavcopy_store import (
    build_chain_from_bhavcopy,
    get_bhavcopy_source_date,
)
from icici_breeze_backend.app.services.reference_data.scrip_index import get_strikes

if TYPE_CHECKING:
    from icici_breeze_backend.app.services.processor import processor as Processor

_logger = logging.getLogger(__name__)


def _previous_trading_day(d: dt.date) -> dt.date:
    from icici_breeze_backend.app.core.market_hours import is_india_trading_day

    prev = d - dt.timedelta(days=1)
    while not is_india_trading_day(dt.datetime(prev.year, prev.month, prev.day, 12, 0, tzinfo=IST)):
        prev -= dt.timedelta(days=1)
    return prev


def latest_concluded_trading_day(now: dt.datetime | None = None) -> dt.date:
    """Last trading day whose session has fully ended (for bhavcopy freshness)."""
    dt_ist = (now or now_ist()).astimezone(IST)
    from icici_breeze_backend.app.core.market_hours import is_india_trading_day

    close = dt_ist.replace(hour=15, minute=30, second=0, microsecond=0)
    if is_india_trading_day(dt_ist) and dt_ist >= close:
        return dt_ist.date()
    return _previous_trading_day(dt_ist.date())


def bhavcopy_is_fresh(exchange_code: str, now: dt.datetime | None = None) -> bool:
    src = get_bhavcopy_source_date(exchange_code)
    if src is None:
        return False
    return src >= latest_concluded_trading_day(now)


def resolve_quote_source(exchange_code: str, now: dt.datetime | None = None) -> str:
    if is_india_market_open(now):
        return "websocket"
    if bhavcopy_is_fresh(exchange_code, now):
        return "bhavcopy"
    return "icici_api"


def assemble_chain_with_router(
    proc: "Processor",
    user_id: str,
    stock_code: str,
    exchange_code: str,
    expiry_display: str,
) -> dict[str, Any]:
    """Build option chain using websocket / bhavcopy / REST per routing rules."""
    source = resolve_quote_source(exchange_code)
    lot_size = proc.fetch_lot_size(stock_code, expiry_display, exchange_code=exchange_code)
    freeze_quantity = None
    try:
        if lot_size is not None:
            ls = int(lot_size)
            if ls > 0:
                qty_limits = proc.fetch_qty_limits(stock_code, exchange_code=exchange_code)
                if qty_limits is not None:
                    freeze_quantity = (max(1, int(qty_limits)) // ls) * ls
    except (TypeError, ValueError):
        freeze_quantity = None

    strikes = get_strikes(stock_code, expiry_display, exchange_code=exchange_code)
    if not strikes:
        strikes = proc.list_option_strikes(stock_code, expiry_display, exchange_code=exchange_code)

    if source == "websocket":
        from icici_breeze_backend.app.services.breeze_websocket_manager import ensure_chain_subscriptions

        ws_payload = ensure_chain_subscriptions(
            proc,
            user_id,
            stock_code,
            exchange_code,
            expiry_display,
            strikes,
            lot_size=int(lot_size) if lot_size else 0,
            freeze_quantity=freeze_quantity,
        )
        if ws_payload is not None:
            return {"Status": 200, "Error": None, "Success": ws_payload}

        _logger.warning("WebSocket chain empty for %s %s; falling back", stock_code, expiry_display)
        if bhavcopy_is_fresh(exchange_code):
            source = "bhavcopy"
        else:
            source = "icici_api"

    if source == "bhavcopy":
        payload = build_chain_from_bhavcopy(
            stock_code,
            expiry_display,
            exchange_code,
            lot_size=int(lot_size) if lot_size else None,
            freeze_quantity=freeze_quantity,
        )
        if payload:
            if not payload.get("chain_rows") and strikes:
                payload["chain_rows"] = [
                    {"strike_price": s, "call": None, "put": None} for s in strikes
                ]
            return {"Status": 200, "Error": None, "Success": payload}
        _logger.warning("Bhavcopy chain missing for %s %s; falling back to REST", stock_code, expiry_display)
        source = "icici_api"

    result = proc._get_full_option_chain_icici_rest(
        user_id, stock_code, exchange_code, expiry_display
    )
    if result.get("Status") == 200 and isinstance(result.get("Success"), dict):
        result["Success"]["quote_source"] = "icici_api"
        result["Success"]["bhavcopy_date"] = None
    return result
