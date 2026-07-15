"""Live NIFTY/SENSEX index-tick feed.

Two purposes:
  1. Powers the navbar spot ticker (`/dashboard/index-quotes`).
  2. Keeps `quote_source_router`'s chain-completeness spot cache warm from a
     source that doesn't depend on options bhavcopy/REST data at all -- see
     `quote_source_router.remember_chain_spot`. Options bhavcopy only carries a
     row for strikes that actually traded, and for thinner chains (Sensex
     weeklies) the tradeable-strike range's deep extremes routinely have none,
     so a live index spot removes that fragility for `chain_readiness.is_chain_complete`'s
     ATM-window gate: with a spot always available, it never falls back to the
     stricter "every tradeable strike must tick" rule for NIFTY/SENSEX chains.

Subscribes once per IST trading day, piggybacked on the same "first
authenticated request with a broker session" trigger as
`system_chain_health.maybe_trigger_system_prefetch` (there is no stored
service broker credential in this app -- see that module's docstring for why).
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime
from typing import Any

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.timezone import IST
from icici_breeze_backend.app.db.redis_client import cache_get_json, cache_set_json
from icici_breeze_backend.app.services.reference_data.keys import index_spot_key
from icici_breeze_backend.app.services.ws_tick_pipeline import register_raw_tick_listener

if False:  # pragma: no cover - typing only
    from icici_breeze_backend.app.services.processor import processor as Processor

_logger = logging.getLogger(__name__)
_lock = threading.RLock()

# (cash exchange_code, cash stock_code, options exchange_code, options stock_code, label)
_INDEX_SCRIPS: tuple[tuple[str, str, str, str, str], ...] = (
    ("NSE", "NIFTY", cfg.NFO, "NIFTY", "nifty"),
    ("BSE", "SENSEX", cfg.BFO, "BSESEN", "sensex"),
)

_INDEX_SPOT_TTL_SECONDS = 15

_symbol_to_label: dict[str, str] = {}
_previous_close: dict[str, float] = {}
_subscribed_date: date | None = None
_listener_registered = False


def _extract_ltp(raw: dict[str, Any]) -> float | None:
    raw_ltp = raw.get("last") if raw.get("last") is not None else raw.get("ltp")
    try:
        ltp = float(raw_ltp)
    except (TypeError, ValueError):
        return None
    return ltp if ltp > 0 else None


def _on_raw_tick(raw: dict[str, Any]) -> None:
    """Registered as a raw WS tick listener -- runs on the SDK callback thread,
    so this must stay a cheap dict lookup and never raise."""
    symbol = str(raw.get("symbol") or "").strip()
    if not symbol:
        return
    with _lock:
        label = _symbol_to_label.get(symbol)
        prev_close = _previous_close.get(label) if label else None
    if label is None:
        return
    ltp = _extract_ltp(raw)
    if ltp is None:
        return

    change = ltp - prev_close if prev_close else None
    change_pct = (change / prev_close * 100.0) if change is not None and prev_close else None
    cache_set_json(
        index_spot_key(label),
        {
            "ltp": ltp,
            "previous_close": prev_close,
            "change": change,
            "change_pct": change_pct,
            "updated_at": time.time(),
        },
        ex=_INDEX_SPOT_TTL_SECONDS,
    )

    for _cash_ex, _cash_stock, opt_exchange, opt_stock_code, lbl in _INDEX_SCRIPS:
        if lbl == label:
            try:
                from icici_breeze_backend.app.services.quote_source_router import (
                    remember_chain_spot,
                )

                remember_chain_spot(opt_exchange, opt_stock_code, ltp)
            except Exception:
                pass
            break


def _register_listener_once() -> None:
    global _listener_registered
    with _lock:
        if _listener_registered:
            return
        register_raw_tick_listener(_on_raw_tick)
        _listener_registered = True


def _fetch_previous_close(sdk: Any, cash_exchange: str, cash_stock_code: str) -> float | None:
    try:
        r = sdk.get_quotes(
            stock_code=cash_stock_code,
            exchange_code=cash_exchange,
            product_type="cash",
            expiry_date="",
            right="",
            strike_price="",
        )
    except Exception:
        _logger.warning(
            "index spot previous-close fetch failed for %s/%s", cash_exchange, cash_stock_code,
            exc_info=True,
        )
        return None
    if not isinstance(r, dict) or (r.get("Status") or r.get("status")) != 200:
        return None
    succ = r.get("Success") or r.get("success")
    row = succ[0] if isinstance(succ, list) and succ else succ if isinstance(succ, dict) else None
    if not isinstance(row, dict):
        return None
    try:
        pc = float(row.get("previous_close") or 0)
    except (TypeError, ValueError):
        return None
    return pc if pc > 0 else None


def sync_index_spot_subscriptions(proc: "Processor", user_id: str) -> bool:
    """Idempotent per IST trading day. Call from the same daily trigger as
    `system_chain_health.maybe_trigger_system_prefetch` -- safe to call on every
    request once today's subscriptions are already in place.

    Returns False when the subscribe attempt didn't happen because there's no
    live broker session -- the caller must not treat that as a permanent
    daily "done" state, or a session that was dead on the first trigger of
    the day will never get retried (see `system_chain_health._prefetch_last_error`)."""
    global _subscribed_date
    today = datetime.now(IST).date()
    with _lock:
        if _subscribed_date == today:
            return True

    from icici_breeze_backend.app.services.breeze_websocket_manager import _ensure_ws

    sdk = _ensure_ws(proc, user_id)
    if sdk is None:
        return False

    _register_listener_once()
    for cash_exchange, cash_stock_code, _opt_exchange, _opt_stock_code, label in _INDEX_SCRIPS:
        try:
            exch_token, _depth_token = sdk.get_stock_token_value(
                exchange_code=cash_exchange,
                stock_code=cash_stock_code,
                get_exchange_quotes=True,
                get_market_depth=False,
            )
            if not exch_token:
                continue
            with _lock:
                _symbol_to_label[str(exch_token)] = label
            sdk.subscribe_feeds(
                exchange_code=cash_exchange,
                stock_code=cash_stock_code,
                get_exchange_quotes=True,
                get_market_depth=False,
            )
            prev_close = _fetch_previous_close(sdk, cash_exchange, cash_stock_code)
            if prev_close is not None:
                with _lock:
                    _previous_close[label] = prev_close
        except Exception:
            _logger.warning("index spot subscribe failed for %s", label, exc_info=True)

    with _lock:
        _subscribed_date = today
    return True


def _extract_rest_ltp(row: dict[str, Any]) -> float | None:
    """REST `get_quotes` rows use `ltp` (occasionally `last`) -- opposite field
    priority from `_extract_ltp`, which parses raw WS tick payloads."""
    raw_ltp = row.get("ltp") if row.get("ltp") is not None else row.get("last")
    try:
        ltp = float(raw_ltp)
    except (TypeError, ValueError):
        return None
    return ltp if ltp > 0 else None


def _fetch_eod_quote(proc: "Processor", user_id: str, cash_exchange: str, cash_stock_code: str) -> dict[str, Any] | None:
    """One-off REST quote for the post-close fallback -- no WS session needed,
    just an authenticated SDK instance (unlike `sync_index_spot_subscriptions`,
    which needs `_ensure_ws` because it subscribes to live ticks)."""
    sdk = proc.get_session_breeze(user_id)
    if sdk is None:
        return None
    try:
        r = sdk.get_quotes(
            stock_code=cash_stock_code,
            exchange_code=cash_exchange,
            product_type="cash",
            expiry_date="",
            right="",
            strike_price="",
        )
    except Exception:
        _logger.warning(
            "EOD index quote fetch failed for %s/%s", cash_exchange, cash_stock_code,
            exc_info=True,
        )
        return None
    if not isinstance(r, dict) or (r.get("Status") or r.get("status")) != 200:
        return None
    succ = r.get("Success") or r.get("success")
    row = succ[0] if isinstance(succ, list) and succ else succ if isinstance(succ, dict) else None
    if not isinstance(row, dict):
        return None
    ltp = _extract_rest_ltp(row)
    if ltp is None:
        return None
    try:
        prev_close = float(row.get("previous_close") or 0)
        prev_close = prev_close if prev_close > 0 else None
    except (TypeError, ValueError):
        prev_close = None
    change = ltp - prev_close if prev_close else None
    change_pct = (change / prev_close * 100.0) if change is not None and prev_close else None
    return {
        "ltp": ltp,
        "previous_close": prev_close,
        "change": change,
        "change_pct": change_pct,
        "updated_at": time.time(),
    }


def get_index_quotes_status(proc: "Processor", user_id: str) -> dict[str, Any]:
    """Live NIFTY/SENSEX spot for the navbar ticker.

    During market hours, this is a pure cache read -- ticks arrive fast enough
    (`_INDEX_SPOT_TTL_SECONDS`) that an empty cache just means "not subscribed
    yet" and will fill in on its own. Outside market hours nothing is ticking,
    so an empty cache means "market closed, nobody's fetched today's close
    yet" -- fetch it once via REST and cache it with no TTL (the key gets
    naturally overwritten by the first live tick the next time the market is
    open, so there's no separate cleanup step)."""
    from icici_breeze_backend.app.services.market_calendar import is_market_open

    market_open = is_market_open()
    quotes: dict[str, Any] = {}
    for cash_exchange, cash_stock_code, _opt_ex, _opt_stock, label in _INDEX_SCRIPS:
        payload = cache_get_json(index_spot_key(label))
        if payload is None and not market_open:
            payload = _fetch_eod_quote(proc, user_id, cash_exchange, cash_stock_code)
            if payload is not None:
                cache_set_json(index_spot_key(label), payload, ex=None)
        quotes[label] = payload if isinstance(payload, dict) else None
    return {"quotes": quotes}


def reset_state_for_tests() -> None:
    """Test-only: reset all module state back to a fresh-process baseline."""
    global _subscribed_date, _listener_registered
    with _lock:
        _subscribed_date = None
        _listener_registered = False
        _symbol_to_label.clear()
        _previous_close.clear()
