"""Dashboard "Day's P&L" tile: unrealized MTM since previous close (not since entry price).

v1 scope: unrealized component only (currently open positions, priced against the previous
session's close). Realized P&L from same-day squared-off trades is intentionally excluded —
see docs/architecture.md discussion / project plan for the tradeoffs (extra ICICI trade-list
calls per dashboard load, non-trivial closed-leg matching).
"""
from __future__ import annotations

import datetime
import logging
from typing import Any

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.market_hours import (
    is_india_market_open,
    is_india_trading_day,
    market_closed_reason,
)
from icici_breeze_backend.app.core.strike import parse_strike
from icici_breeze_backend.app.core.timezone import IST, now_ist_naive
from icici_breeze_backend.app.services.reference_data.bhavcopy_common import safe_float
from icici_breeze_backend.app.services.reference_data.bhavcopy_store import _lookup_bhav_row

_logger = logging.getLogger(__name__)


def _day_pnl_session_state(now: datetime.datetime | None = None) -> str:
    """One of: 'open', 'post_close', 'pre_open', 'closed_non_trading_day'."""
    dt = now or datetime.datetime.now(IST)
    if is_india_market_open(dt):
        return "open"
    if not is_india_trading_day(dt):
        return "closed_non_trading_day"
    reason = market_closed_reason(dt)
    return "pre_open" if "before market open" in reason else "post_close"


def _reference_price_for_position(pos: dict[str, Any]) -> tuple[float | None, bool]:
    """
    Resolve the "previous session close" reference price for a position.

    Uses the bhavcopy row's own `ltp` field, not `previous_close` — during today's
    session the freshest loaded bhavcopy is *yesterday's* file, so that file's own
    `previous_close` column refers to the close from two sessions ago, not yesterday.
    `ltp` on yesterday's file is yesterday's own close, which is what "previous close
    relative to today" means here.

    Returns (reference_price, used_fallback). Falls back to `average_price` (entry
    price) when no bhavcopy row exists (e.g. a newly-listed contract).
    """
    strike = parse_strike(pos.get("strike_price"))
    stock_code = pos.get("stock_code")
    expiry_display = pos.get("expiry_date")
    right = pos.get("right")
    exchange_code = pos.get("exchange_code")
    if strike is not None and stock_code and expiry_display and right and exchange_code:
        try:
            row = _lookup_bhav_row(stock_code, expiry_display, right, strike, exchange_code)
        except Exception:
            _logger.warning(
                "days_pnl: bhavcopy lookup failed stock_code=%s expiry=%s strike=%s right=%s",
                stock_code,
                expiry_display,
                strike,
                right,
                exc_info=True,
            )
            row = None
        if row is not None:
            ltp = safe_float(row.get("ltp"), 0.0)
            if ltp > 0:
                return ltp, False
    avg = safe_float(pos.get("average_price"), 0.0)
    return (avg if avg > 0 else None), True


def _position_day_mtm(pos: dict[str, Any], reference_price: float) -> float | None:
    ltp = safe_float(pos.get("ltp"), 0.0)
    qty = int(safe_float(pos.get("quantity"), 0.0))
    if ltp <= 0 or qty == 0:
        return None
    action = str(pos.get("action") or "").strip()
    if action == cfg.SELL:
        return (reference_price - ltp) * qty
    if action == cfg.BUY:
        return (ltp - reference_price) * qty
    return None


def compute_days_pnl(
    positions: list[dict[str, Any]] | None,
    *,
    now: datetime.datetime | None = None,
) -> dict[str, Any]:
    """
    Day's P&L (MTM since previous close) for the Dashboard tile.

    `positions` is the already-normalized position list, e.g.
    `portfolio["Success"]["positions"]` after `_normalize_portfolio_success_for_ui`.
    """
    session_state = _day_pnl_session_state(now)
    result: dict[str, Any] = {
        "unrealized_day_pnl": None,
        "realized_day_pnl": None,
        "total_day_pnl": None,
        "as_of": now_ist_naive().isoformat(),
        "market_session_state": session_state,
        "positions_priced": 0,
        "positions_missing_reference": 0,
        "error": None,
    }

    if session_state in ("pre_open", "closed_non_trading_day"):
        result["unrealized_day_pnl"] = 0.0
        result["total_day_pnl"] = 0.0
        return result

    if not positions:
        result["unrealized_day_pnl"] = 0.0
        result["total_day_pnl"] = 0.0
        return result

    total = 0.0
    priced = 0
    missing_reference = 0
    try:
        for pos in positions:
            if not isinstance(pos, dict):
                continue
            reference_price, used_fallback = _reference_price_for_position(pos)
            if used_fallback:
                missing_reference += 1
            if reference_price is None:
                continue
            mtm = _position_day_mtm(pos, reference_price)
            if mtm is None:
                continue
            total += mtm
            priced += 1
    except Exception as exc:
        _logger.warning("compute_days_pnl: failed to compute unrealized day MTM: %s", exc, exc_info=True)
        result["error"] = "Could not compute day's P&L for some positions."
        return result

    result["unrealized_day_pnl"] = total
    result["total_day_pnl"] = total
    result["positions_priced"] = priced
    result["positions_missing_reference"] = missing_reference
    return result
