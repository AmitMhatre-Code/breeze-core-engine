"""Keeps the Dashboard "Day's P&L" tile live between REST snapshots.

The last ``/dashboard/day-pnl`` REST computation establishes a per-contract
baseline of the two quantities that are *fixed* for the rest of the session --
``q0`` (start-of-day signed qty) and ``P_prev`` (previous close) -- plus the
realized cash-flow (``Σδp``) known at that instant. From then on:

  * the scrip WS feed (via ``portfolio_pnl_engine``'s ``quotes:pnl:*`` Redis
    hash) supplies a live ``P_now`` every ~2s, and
  * the order WS feed supplies fills as they happen. Each fill is booked
    immediately at an APPROXIMATE price -- the order's ``limitRate`` when
    present, otherwise the live LTP -- so the tile keeps moving, and then
    RECONCILED once by a single debounced ``processor.get_trades()`` call that
    replaces the approximation with the real executed prices.

No polling REST call is ever made to keep the tile live -- only the one-shot
reconcile per fill burst.

Identity (per contract), unchanged from
``dashboard_day_pnl.compute_day_pnl``::

    day_pnl = q1·P_now − q0·P_prev − Σ(δ·p)      with  q1 = q0 + Σδ

Options-only, same as ``compute_day_pnl`` and ``portfolio_pnl_engine`` -- a
contract needs an exchange/stock/expiry/right/strike to be priced from the
options quote path at all. Non-option positions never reach here; the tile
falls back to its REST snapshot for those (see the frontend).
"""
from __future__ import annotations

import datetime
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from icici_breeze_backend.app.core.strike import parse_strike
from icici_breeze_backend.app.core.timezone import now_ist_naive, today_ist_date
from icici_breeze_backend.app.db.redis_client import get_redis
from icici_breeze_backend.app.services.dashboard_day_pnl import (
    _EPS,
    _parse_trade_date,
    _signed_qty,
    _unrealized_component,
    make_prev_close_lookup,
)
from icici_breeze_backend.app.services.dashboard_days_pnl import _day_pnl_session_state
from icici_breeze_backend.app.services.market_calendar import latest_opened_trading_day
from icici_breeze_backend.app.services.reference_data.bhavcopy_common import safe_float
from icici_breeze_backend.app.services.reference_data.keys import pnl_quote_key
from icici_breeze_backend.app.services.reference_data.scrip_index import contract_index_key
from icici_breeze_backend.app.services.reference_data.scrip_master_sql import normalize_expiry_display

_logger = logging.getLogger(__name__)

# How long to wait after a fill before firing the single get_trades() reconcile.
# Long enough for ICICI's trade book to reflect the fill, short enough that the
# approximate price is only on screen briefly.
RECONCILE_DEBOUNCE_SECONDS = 5.0
# Backoff before retrying a reconcile whose get_trades() call failed.
RECONCILE_RETRY_SECONDS = 30.0

_lock = threading.RLock()
_listener_registered = False
_processor_instance: Any = None


def _get_processor() -> Any:
    """One `processor()` instance for the reconcile path, created lazily so the
    module stays importable without pulling in `breeze_connect` at import time
    (mirrors how the route modules each hold one)."""
    global _processor_instance
    if _processor_instance is None:
        from icici_breeze_backend.app.services.processor import processor

        _processor_instance = processor()
    return _processor_instance


@dataclass
class _Contract:
    exchange: str
    stock: str
    expiry_display: str
    right_raw: str  # as first seen on a position/trade row -- for the prev-close lookup
    strike: Any
    q0: float  # start-of-day signed qty -- FROZEN once captured
    p_prev: float | None  # previous close -- FROZEN; only consulted when q0 != 0
    sum_delta: float  # Σδ of today's fills known so far (signed)
    sum_dp: float  # Σ(δ·p) of today's fills known so far
    unpriced_delta: float = 0.0  # signed qty from fills whose price isn't resolved yet


@dataclass
class _UserState:
    session_date: datetime.date
    captured_at: float
    contracts: dict[str, _Contract]
    # order_id -> cumulative executedQuantity already folded into a contract's sum_delta.
    applied_exec: dict[str, int] = field(default_factory=dict)
    # monotonic deadline for the pending reconcile, or None when none is due.
    reconcile_due: float | None = None
    meta: dict[str, Any] = field(default_factory=dict)


_state: dict[str, _UserState] = {}
_latest: dict[str, dict[str, Any]] = {}


# --------------------------------------------------------------------------- helpers


def _today() -> datetime.date:
    return today_ist_date()


def _norm_expiry(raw: Any) -> str:
    try:
        return normalize_expiry_display(str(raw or ""))
    except (ValueError, TypeError):
        return str(raw or "").strip()


def _row_contract_fields(row: dict[str, Any]) -> tuple[str, str, str, str, Any] | None:
    """(exchange, stock, expiry_display, right_raw, strike) from a position/trade row,
    or None when any identity field is missing. Mirrors
    ``dashboard_day_pnl._contract_fields`` but normalizes the expiry so the key
    lines up with ``portfolio_pnl_engine`` scrip keys (and thus the pnl-quote hash)."""
    exch = str(row.get("exchange_code") or "").strip().upper()
    stock = str(row.get("stock_code") or "").strip().upper()
    expiry = _norm_expiry(row.get("expiry_date"))
    right = str(row.get("right") or "").strip()
    strike = parse_strike(row.get("strike_price"))
    if not exch or not stock or not expiry or not right or strike is None:
        return None
    return exch, stock, expiry, right, strike


def _key_for(exch: str, stock: str, expiry: str, right: str, strike: Any) -> str:
    return contract_index_key(exch, stock, expiry, strike, right)


def _fetch_live_ltps(keys: list[str]) -> dict[str, float]:
    """One pipelined round trip for every contract's conflated pnl-quote LTP.
    Mirrors ``portfolio_pnl_engine._fetch_quotes`` -- never a per-key GET."""
    if not keys:
        return {}
    try:
        redis = get_redis()
        pipe = redis.pipeline(transaction=False)
        for k in keys:
            pipe.hget(pnl_quote_key(k), "ltp")
        results = pipe.execute()
    except Exception:
        _logger.debug("day-pnl live: pnl-quote fetch failed", exc_info=True)
        return {}
    out: dict[str, float] = {}
    for k, raw in zip(keys, results):
        try:
            ltp = float(raw) if raw not in (None, "") else None
        except (TypeError, ValueError):
            ltp = None
        if ltp is not None and ltp > 0:
            out[k] = ltp
    return out


# --------------------------------------------------------------------------- baseline capture


def capture_baseline(
    user_id: str,
    positions: list[dict[str, Any]] | None,
    trades: list[dict[str, Any]] | None,
    *,
    trades_source_ok: bool,
    now: datetime.datetime | None = None,
) -> None:
    """Snapshot q0 / P_prev / Σδ / Σδp per contract from a ``/dashboard/day-pnl``
    computation's own inputs. Called on every REST computation, so each Dashboard
    mount (and the manual Refresh) re-baselines and self-heals. Best-effort:
    never raises into the request path."""
    try:
        session_date = latest_opened_trading_day(now)
        prev_close_lookup = make_prev_close_lookup(now)

        acc: dict[str, dict[str, Any]] = {}

        def _slot(fields: tuple[str, str, str, str, Any]) -> dict[str, Any]:
            exch, stock, expiry, right, strike = fields
            key = _key_for(exch, stock, expiry, right, strike)
            c = acc.get(key)
            if c is None:
                c = {
                    "exchange": exch,
                    "stock": stock,
                    "expiry_display": expiry,
                    "right_raw": right,
                    "strike": strike,
                    "q1": 0.0,
                    "sum_delta": 0.0,
                    "sum_dp": 0.0,
                }
                acc[key] = c
            return c

        for pos in positions or []:
            if not isinstance(pos, dict):
                continue
            fields = _row_contract_fields(pos)
            if fields is None:
                continue
            _slot(fields)["q1"] += _signed_qty(pos.get("action"), pos.get("quantity"))

        for t in trades or []:
            if not isinstance(t, dict):
                continue
            if _parse_trade_date(t.get("trade_date")) != session_date:
                continue
            fields = _row_contract_fields(t)
            if fields is None:
                continue
            c = _slot(fields)
            delta = _signed_qty(t.get("action"), t.get("quantity"))
            price = safe_float(t.get("average_cost"), 0.0)
            c["sum_delta"] += delta
            c["sum_dp"] += delta * price

        contracts: dict[str, _Contract] = {}
        for key, c in acc.items():
            q0 = c["q1"] - c["sum_delta"]
            p_prev: float | None = None
            if abs(q0) > _EPS:
                p_prev = prev_close_lookup(
                    c["exchange"], c["stock"], c["expiry_display"], c["right_raw"], c["strike"]
                )
            contracts[key] = _Contract(
                exchange=c["exchange"],
                stock=c["stock"],
                expiry_display=c["expiry_display"],
                right_raw=c["right_raw"],
                strike=c["strike"],
                q0=q0,
                p_prev=p_prev,
                sum_delta=c["sum_delta"],
                sum_dp=c["sum_dp"],
            )

        with _lock:
            _state[user_id] = _UserState(
                session_date=_today(),
                captured_at=time.time(),
                contracts=contracts,
                meta={"trades_source_ok": bool(trades_source_ok)},
            )
            _latest.pop(user_id, None)
    except Exception:
        _logger.warning("day-pnl live: baseline capture failed for %s", user_id, exc_info=True)


# --------------------------------------------------------------------------- order WS feed


def start() -> None:
    """Register the order-notification listener. Idempotent; call once from the
    app lifespan alongside the P&L engine start."""
    global _listener_registered
    with _lock:
        if _listener_registered:
            return
        from icici_breeze_backend.app.services.ws_tick_pipeline import (
            register_order_notification_listener,
        )

        register_order_notification_listener(on_order_notification)
        _listener_registered = True
    _logger.info("Dashboard day-P&L live: order-notification listener registered")


def on_order_notification(n: Any) -> None:
    """Fold a fill into the live baseline. Runs on the SDK's WS callback thread,
    so this stays a pure in-memory update -- price resolution that needs Redis is
    deferred to the recompute tick. Never raises."""
    try:
        user_id = str(getattr(n, "user_id", "") or "")
        order_id = str(getattr(n, "order_id", "") or "")
        cum_exec = int(getattr(n, "executed_quantity", 0) or 0)
        if not user_id or not order_id or cum_exec <= 0:
            return
        strike = getattr(n, "strike", None)
        if strike is None:
            return

        with _lock:
            st = _state.get(user_id)
            if st is None or st.session_date != _today():
                return  # no baseline yet -- the tile stays on its REST snapshot

            prev = st.applied_exec.get(order_id, 0)
            if cum_exec <= prev:
                return  # nothing new (duplicate / out-of-order notification)
            new_qty = cum_exec - prev
            action = str(getattr(n, "action", "") or "").strip().lower()
            signed = float(new_qty) if action.startswith("b") else -float(new_qty)

            key = contract_index_key(
                str(getattr(n, "exchange_code", "") or ""),
                str(getattr(n, "stock_code", "") or ""),
                _norm_expiry(getattr(n, "expiry_display", "")),
                strike,
                str(getattr(n, "right", "") or ""),
            )
            c = st.contracts.get(key)
            if c is None:
                # A contract opened today that wasn't in the baseline -> q0 = 0
                # (not held at start of day), so P_prev is never needed for it.
                c = _Contract(
                    exchange=str(getattr(n, "exchange_code", "") or "").strip().upper(),
                    stock=str(getattr(n, "stock_code", "") or "").strip().upper(),
                    expiry_display=_norm_expiry(getattr(n, "expiry_display", "")),
                    right_raw=str(getattr(n, "right", "") or ""),
                    strike=strike,
                    q0=0.0,
                    p_prev=None,
                    sum_delta=0.0,
                    sum_dp=0.0,
                )
                st.contracts[key] = c

            limit_price = getattr(n, "limit_price", None)
            c.sum_delta += signed
            if isinstance(limit_price, (int, float)) and limit_price > 0:
                c.sum_dp += signed * float(limit_price)
            else:
                c.unpriced_delta += signed  # priced from live LTP on the next tick

            st.applied_exec[order_id] = cum_exec
            # Sliding debounce: reconcile only once fills have been quiet for
            # RECONCILE_DEBOUNCE_SECONDS, so a burst of legs settles in the trade
            # book before the single get_trades() call trues them up.
            st.reconcile_due = time.monotonic() + RECONCILE_DEBOUNCE_SECONDS
    except Exception:
        _logger.exception("day-pnl live: order-notification handling failed")


# --------------------------------------------------------------------------- reconcile


def _run_reconcile(user_id: str) -> None:
    """Single get_trades() call: replace every contract's Σδ / Σδp with the
    authoritative trade book so approximate fill prices are trued up. Runs on the
    P&L loop's worker thread, so a blocking broker call here is fine."""
    today_iso = _today().isoformat()
    try:
        resp = _get_processor().get_trades(user_id, today_iso, today_iso)
    except Exception:
        _logger.warning("day-pnl live: reconcile get_trades raised for %s", user_id, exc_info=True)
        resp = None

    ok = isinstance(resp, dict) and resp.get("Status") == 200
    if not ok:
        with _lock:
            st = _state.get(user_id)
            if st is not None:
                st.reconcile_due = time.monotonic() + RECONCILE_RETRY_SECONDS
        return

    trades = resp.get("Success") or []
    session_date = latest_opened_trading_day()

    with _lock:
        st = _state.get(user_id)
        if st is None:
            return
        for c in st.contracts.values():
            c.sum_delta = 0.0
            c.sum_dp = 0.0
            c.unpriced_delta = 0.0
        for t in trades:
            if not isinstance(t, dict):
                continue
            if _parse_trade_date(t.get("trade_date")) != session_date:
                continue
            fields = _row_contract_fields(t)
            if fields is None:
                continue
            exch, stock, expiry, right, strike = fields
            key = _key_for(exch, stock, expiry, right, strike)
            c = st.contracts.get(key)
            if c is None:
                c = _Contract(
                    exchange=exch,
                    stock=stock,
                    expiry_display=expiry,
                    right_raw=right,
                    strike=strike,
                    q0=0.0,
                    p_prev=None,
                    sum_delta=0.0,
                    sum_dp=0.0,
                )
                st.contracts[key] = c
            delta = _signed_qty(t.get("action"), t.get("quantity"))
            price = safe_float(t.get("average_cost"), 0.0)
            c.sum_delta += delta
            c.sum_dp += delta * price
        st.reconcile_due = None
        st.meta["trades_source_ok"] = True


# --------------------------------------------------------------------------- recompute


def _compute_live_payload(st: _UserState, ltps: dict[str, float]) -> dict[str, Any]:
    session_state = _day_pnl_session_state()
    result: dict[str, Any] = {
        "total_day_pnl": None,
        "realized_day_pnl": None,
        "unrealized_day_pnl": None,
        "is_gross": True,
        "as_of": now_ist_naive().isoformat(),
        "market_session_state": session_state,
        "contracts_priced": 0,
        "contracts_missing_prev_close": 0,
        "trades_source_ok": bool(st.meta.get("trades_source_ok", True)),
        "degraded": False,
        "error": None,
        "source": "live",
    }
    if session_state in ("pre_open", "closed_non_trading_day"):
        result.update(total_day_pnl=0.0, realized_day_pnl=0.0, unrealized_day_pnl=0.0)
        return result

    total = realized = unrealized = 0.0
    priced = 0
    missing = 0
    for key, c in st.contracts.items():
        # Resolve any fills we couldn't price at arrival (missing limitRate) from
        # the live LTP now that we can touch Redis.
        if abs(c.unpriced_delta) > _EPS:
            ltp = ltps.get(key)
            if ltp is not None:
                c.sum_dp += c.unpriced_delta * ltp
                c.unpriced_delta = 0.0

        q1 = c.q0 + c.sum_delta
        traded = abs(c.sum_delta) > _EPS or abs(c.sum_dp) > _EPS
        if abs(q1) < _EPS and not traded:
            continue

        p_now = ltps.get(key, 0.0)
        if abs(c.q0) > _EPS and c.p_prev is None:
            missing += 1
            continue
        if abs(q1) > _EPS and p_now <= 0:
            missing += 1
            continue

        p_prev = c.p_prev or 0.0
        total_c = q1 * p_now - c.q0 * p_prev - c.sum_dp
        unrealized_c = _unrealized_component(q1, c.q0, p_now, p_prev, c.sum_delta, c.sum_dp)
        total += total_c
        unrealized += unrealized_c
        realized += total_c - unrealized_c
        priced += 1
        if abs(c.unpriced_delta) > _EPS:
            missing += 1

    result.update(
        total_day_pnl=total,
        realized_day_pnl=realized,
        unrealized_day_pnl=unrealized,
        contracts_priced=priced,
        contracts_missing_prev_close=missing,
        degraded=missing > 0 or not result["trades_source_ok"],
    )
    return result


def run_tick() -> None:
    """One recompute pass for every user with a fresh baseline. Fires a pending
    reconcile if its debounce has elapsed. Called from the P&L engine loop."""
    now_mono = time.monotonic()
    today = _today()

    with _lock:
        users = list(_state.items())

    for user_id, st in users:
        if st.session_date != today:
            with _lock:
                _state.pop(user_id, None)
                _latest.pop(user_id, None)
            continue
        if st.reconcile_due is not None and now_mono >= st.reconcile_due:
            _run_reconcile(user_id)
        try:
            # Fetch quotes outside the lock (a Redis round trip), then do the
            # arithmetic under it so a concurrent order-notification mutation of
            # the same contracts can't tear the read.
            with _lock:
                keys = list(st.contracts.keys())
            ltps = _fetch_live_ltps(keys)
            with _lock:
                payload = _compute_live_payload(st, ltps)
                _latest[user_id] = payload
        except Exception:
            _logger.exception("day-pnl live: recompute failed for %s", user_id)
            continue


# --------------------------------------------------------------------------- read API


def latest(user_id: str) -> dict[str, Any] | None:
    """The live Day's P&L payload for a user, or None when there's no live value
    yet (no baseline this session, or the recompute loop hasn't run). Callers
    fall back to the REST ``/dashboard/day-pnl`` snapshot in that case."""
    with _lock:
        payload = _latest.get(user_id)
        return dict(payload) if payload else None


def reset_state_for_tests() -> None:
    global _listener_registered, _processor_instance
    with _lock:
        _state.clear()
        _latest.clear()
        _listener_registered = False
        _processor_instance = None
