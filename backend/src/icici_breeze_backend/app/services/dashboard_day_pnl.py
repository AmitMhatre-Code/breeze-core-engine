"""Dashboard "Day's P&L": mark-to-market since previous close, from the trade book.

Per contract, the exact daily mark-to-market identity is::

    DayPnL = q1·P_now  −  q0·P_prev  −  Σ(δ·p)

    q1     = signed open qty now (Buy +, Sell −), from positions; 0 if flat
    P_now  = current LTP of the open position
    δ, p   = signed executed qty and fill price of TODAY's trades (Buy +, Sell −)
    q0     = q1 − Σδ  -> position at start of day, reconstructed from today's fills
    P_prev = previous trading day's close (bhavcopy `LastPric`), consulted only when q0 ≠ 0

This one identity captures, without any lot matching:
  * realized P&L from intraday round-trips / squareoffs  (flat contract: q1=0 -> −Σδp),
  * same-day entries marked from their entry price, not a prior close (q0=0),
  * carried positions marked from the previous session's close (q0≠0).

The realized/unrealized split is a presentation decomposition of the same total:
`unrealized = q1·(P_now − basis)` (basis = P_prev for the carried portion, today's entry
VWAP for the portion opened today); `realized = total − unrealized`. Total is authoritative.

Numbers are GROSS of brokerage and taxes (matches ICICI's day P&L convention; the UI
surfaces this with a tooltip). Replaces the old `dashboard_days_pnl.compute_days_pnl`,
which was unrealized-only and mispriced same-day entries and expiry-day contracts.
"""
from __future__ import annotations

import datetime
import logging
from typing import Any, Callable, Optional

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.strike import parse_strike, strike_key
from icici_breeze_backend.app.core.timezone import now_ist_naive, today_ist_date
from icici_breeze_backend.app.services.dashboard_days_pnl import _day_pnl_session_state
from icici_breeze_backend.app.services.market_calendar import (
    _previous_trading_day,
    latest_opened_trading_day,
)
from icici_breeze_backend.app.services.reference_data.bhavcopy_common import safe_float
from icici_breeze_backend.app.services.reference_data.bhavcopy_store import (
    _lookup_bhav_row,
    get_bhavcopy_source_date,
)

_logger = logging.getLogger(__name__)

# lookup(exchange_code, stock_code, expiry_display, right, strike) -> prev-close float | None
PrevCloseLookup = Callable[[str, str, str, Any], Optional[float]]

_EPS = 1e-9


def _signed_qty(action: Any, quantity: Any) -> float:
    """Signed quantity: Buy positive, Sell negative; magnitude from `quantity`."""
    q = abs(safe_float(quantity, 0.0))
    a = str(action or "").strip()
    if a == cfg.SELL:
        return -q
    if a == cfg.BUY:
        return q
    return 0.0


def _parse_trade_date(raw: Any) -> datetime.date | None:
    """Trade-book `trade_date` is like '23-Jul-2026'."""
    s = str(raw or "").strip()
    if not s:
        return None
    try:
        return datetime.datetime.strptime(s, "%d-%b-%Y").date()
    except ValueError:
        return None


def _contract_fields(row: dict[str, Any]) -> tuple[str, str, str, str, Any] | None:
    exch = str(row.get("exchange_code") or "").strip().upper()
    stock = str(row.get("stock_code") or "").strip().upper()
    expiry = str(row.get("expiry_date") or "").strip()
    right = str(row.get("right") or "").strip()
    strike = parse_strike(row.get("strike_price"))
    if not exch or not stock or not expiry or not right or strike is None:
        return None
    return exch, stock, expiry, right, strike


def _unrealized_component(
    q1: float, q0: float, p_now: float, p_prev: float, sdelta: float, sdp: float
) -> float:
    """MTM of the currently-open qty from its cost basis to P_now.

    Exact for the common cases (fresh open, pure carry, partial reduce); a bounded
    blend for the rare add-to-carry / intraday sign-flip. `realized = total − this`
    always holds by construction, so the split is self-consistent whatever the case.
    """
    if abs(q1) < _EPS:
        return 0.0
    entry_vwap = (sdp / sdelta) if abs(sdelta) > _EPS else p_now
    if abs(q0) < _EPS:
        # opened fresh today -> basis is today's entry VWAP
        return q1 * (p_now - entry_vwap)
    same_sign = (q1 > 0) == (q0 > 0)
    if same_sign and abs(q1) <= abs(q0):
        # still entirely carried (possibly partially reduced) -> basis is prev close
        return q1 * (p_now - p_prev)
    # added to a carry, or flipped through zero -> blend carried + opened-today portions
    carried_kept = q0 if same_sign else 0.0
    opened_qty = q1 - carried_kept
    return carried_kept * (p_now - p_prev) + opened_qty * (p_now - entry_vwap)


def compute_day_pnl(
    positions: list[dict[str, Any]] | None,
    trades: list[dict[str, Any]] | None,
    *,
    now: datetime.datetime | None = None,
    prev_close_lookup: PrevCloseLookup | None = None,
) -> dict[str, Any]:
    """Mark-to-market Day's P&L from open positions + today's executed trades.

    `prev_close_lookup` resolves the previous session's close for a carried contract;
    inject a stub in tests. When omitted, carried contracts cannot be valued and are
    reported under `contracts_missing_prev_close` (realized-only, degraded).
    """
    session_state = _day_pnl_session_state(now)
    result: dict[str, Any] = {
        "total_day_pnl": None,
        "realized_day_pnl": None,
        "unrealized_day_pnl": None,
        "is_gross": True,
        "as_of": now_ist_naive().isoformat(),
        "market_session_state": session_state,
        "contracts_priced": 0,
        "contracts_missing_prev_close": 0,
        "trades_source_ok": True,
        "degraded": False,
        "error": None,
    }

    if session_state in ("pre_open", "closed_non_trading_day"):
        result.update(total_day_pnl=0.0, realized_day_pnl=0.0, unrealized_day_pnl=0.0)
        return result

    session_date = latest_opened_trading_day(now)

    contracts: dict[tuple, dict[str, Any]] = {}

    def _slot(key: tuple, fields: tuple) -> dict[str, Any]:
        c = contracts.get(key)
        if c is None:
            exch, stock, expiry, right, strike = fields
            c = {
                "exchange": exch,
                "stock": stock,
                "expiry": expiry,
                "right": right,
                "strike": strike,
                "q1": 0.0,
                "p_now": 0.0,
                "sdelta": 0.0,
                "sdp": 0.0,
                "traded": False,
            }
            contracts[key] = c
        return c

    for pos in positions or []:
        if not isinstance(pos, dict):
            continue
        fields = _contract_fields(pos)
        if fields is None:
            continue
        key = (fields[0], fields[1], fields[2], fields[3], strike_key(fields[4]))
        c = _slot(key, fields)
        c["q1"] += _signed_qty(pos.get("action"), pos.get("quantity"))
        ltp = safe_float(pos.get("ltp"), 0.0)
        if ltp > 0:
            c["p_now"] = ltp

    for t in trades or []:
        if not isinstance(t, dict):
            continue
        if _parse_trade_date(t.get("trade_date")) != session_date:
            continue
        fields = _contract_fields(t)
        if fields is None:
            continue
        key = (fields[0], fields[1], fields[2], fields[3], strike_key(fields[4]))
        c = _slot(key, fields)
        delta = _signed_qty(t.get("action"), t.get("quantity"))
        price = safe_float(t.get("average_cost"), 0.0)
        c["sdelta"] += delta
        c["sdp"] += delta * price
        c["traded"] = True

    total = realized = unrealized = 0.0
    priced = 0
    missing_prev = 0
    try:
        for c in contracts.values():
            q1, p_now, sdelta, sdp = c["q1"], c["p_now"], c["sdelta"], c["sdp"]
            if abs(q1) < _EPS and not c["traded"]:
                continue  # no open position and no fill today (nothing to value)
            # NB: a flat intraday round-trip has q1==0 AND sdelta==0 but traded=True and
            # sdp!=0 -> it must be valued (realized = −sdp), not skipped.
            q0 = q1 - sdelta
            p_prev: float | None = None
            if abs(q0) > _EPS:
                if prev_close_lookup is not None:
                    p_prev = prev_close_lookup(
                        c["exchange"], c["stock"], c["expiry"], c["right"], c["strike"]
                    )
                if p_prev is None:
                    missing_prev += 1
                    continue  # carried baseline unknown -> cannot value this contract
            if abs(q1) > _EPS and p_now <= 0:
                missing_prev += 1
                continue  # open leg with no current price
            total_c = q1 * p_now - q0 * (p_prev or 0.0) - sdp
            unrealized_c = _unrealized_component(q1, q0, p_now, p_prev or 0.0, sdelta, sdp)
            total += total_c
            unrealized += unrealized_c
            realized += total_c - unrealized_c
            priced += 1
    except Exception as exc:
        _logger.warning("compute_day_pnl failed: %s", exc, exc_info=True)
        result["error"] = "Could not compute day's P&L for some contracts."
        return result

    result.update(
        total_day_pnl=total,
        realized_day_pnl=realized,
        unrealized_day_pnl=unrealized,
        contracts_priced=priced,
        contracts_missing_prev_close=missing_prev,
        degraded=missing_prev > 0,
    )
    return result


def make_prev_close_lookup(now: datetime.datetime | None = None) -> PrevCloseLookup:
    """Prev-close resolver backed by the loaded bhavcopy, guarded by source date.

    Only returns a price when the loaded bhavcopy for that exchange is dated exactly the
    previous trading day relative to the current session -- so a today's/expiry-day file
    (or a stale one) never masquerades as "previous close".
    """
    session = latest_opened_trading_day(now)
    expected_prev = _previous_trading_day(session)

    def lookup(exchange_code: str, stock_code: str, expiry_display: str, right: str, strike: Any) -> float | None:
        try:
            src = get_bhavcopy_source_date(exchange_code)
            if src is None or src != expected_prev:
                return None
            row = _lookup_bhav_row(stock_code, expiry_display, right, strike, exchange_code)
            if not row:
                return None
            ltp = safe_float(row.get("ltp"), 0.0)
            return ltp if ltp > 0 else None
        except Exception:
            _logger.debug("prev-close lookup failed for %s %s", stock_code, strike, exc_info=True)
            return None

    return lookup


def build_dashboard_day_pnl(user_id: str, processor, *, broker_token: str) -> dict[str, Any]:
    """Lazy endpoint payload: fetch positions + today's trades, compute mark-to-market."""
    from icici_breeze_backend.app.api.v1.route_portfolio import _normalize_portfolio_success_for_ui
    from icici_breeze_backend.app.services.broker_snapshot_cache import get_snapshot

    # Prefer the login-warmed snapshot so P_now matches the Open P&L tile; else fetch live.
    snap = get_snapshot(user_id, broker_token)
    portfolio = snap.portfolio if snap and snap.portfolio is not None else None
    if not (isinstance(portfolio, dict) and portfolio.get("Status") == 200):
        raw = processor.get_positions(user_id)
        portfolio = _normalize_portfolio_success_for_ui(raw) if isinstance(raw, dict) else raw

    positions: list[dict[str, Any]] = []
    if isinstance(portfolio, dict):
        success = portfolio.get("Success")
        if isinstance(success, dict) and isinstance(success.get("positions"), list):
            positions = success["positions"]

    today = today_ist_date().isoformat()
    trades_resp = processor.get_trades(user_id, today, today)
    trades_ok = isinstance(trades_resp, dict) and trades_resp.get("Status") == 200
    trades = (trades_resp.get("Success") or []) if trades_ok else []

    result = compute_day_pnl(positions, trades, prev_close_lookup=make_prev_close_lookup())
    result["trades_source_ok"] = bool(trades_ok)
    if not trades_ok:
        result["degraded"] = True
        result["error"] = result.get("error") or "Trade book unavailable; realized P&L excluded."

    # Seed the live baseline off this same positions + trades data so the tile can
    # stay live off the WS feeds afterwards without any further REST calls (see
    # dashboard_day_pnl_live).
    try:
        from icici_breeze_backend.app.services import dashboard_day_pnl_live

        dashboard_day_pnl_live.capture_baseline(
            user_id, positions, list(trades), trades_source_ok=bool(trades_ok)
        )
    except Exception:  # pragma: no cover - best effort, never break the endpoint
        _logger.debug("day-pnl live baseline seed failed", exc_info=True)

    return result
