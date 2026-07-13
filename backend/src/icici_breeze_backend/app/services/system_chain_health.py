"""System-level pre-subscription + health status for the NIFTY/SENSEX option
chains that back the navbar market-data health dot.

Distinct from a per-user browser-tab holder: this holder is a fixed,
process-wide id that is (re)subscribed once per IST trading day, piggy-backing
on whichever authenticated user's broker session happens to make the first
request after market open that day (see `app/auth/context.py`). There is no
stored service broker credential in this app (session persistence was
deliberately removed, see `processor.get_session_breeze`), so a background
cron with nobody logged in has no session to use.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import date, datetime
from typing import Any

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.market_hours import is_india_market_open, market_closed_reason
from icici_breeze_backend.app.core.timezone import IST
from icici_breeze_backend.app.db.redis_client import cache_get_json
from icici_breeze_backend.app.services.reference_data.keys import ws_raw_quote_key
from icici_breeze_backend.app.services.reference_data.scrip_index import get_underlyings
from icici_breeze_backend.app.services.reference_data.ws_token_index import (
    list_ws_stock_tokens_for_liquid_contracts,
    parse_ws_symbol,
)

_logger = logging.getLogger(__name__)
_lock = threading.RLock()

# (exchange_code, stock_code, label)
_SCRIPS: tuple[tuple[str, str, str], ...] = (
    (cfg.NFO, "NIFTY", "nifty"),
    (cfg.BFO, "BSESEN", "sensex"),
)

_HOLDER_PREFIX = "system:health:"

_RETRY_COOLDOWN_SECONDS = 30.0
_WARMUP_MULTIPLE = 3.0  # grace window after subscribe before "no tick yet" counts as stale
_WARMUP_FLOOR_SECONDS = 10.0
_STALE_MULTIPLE = 3.0  # stale threshold = N x poll interval, to avoid flapping on one missed flush
_STALE_FLOOR_SECONDS = 6.0

_prefetch_date: date | None = None
_prefetch_in_progress: bool = False
_prefetch_last_attempt_monotonic: float | None = None
_prefetch_last_error: str | None = None
_prefetch_expiries: dict[str, str] = {}
_prefetch_subscribed_at_monotonic: dict[str, float] = {}

# Holds references to fire-and-forget background tasks so asyncio's weak-ref
# task tracking can't garbage-collect them mid-flight; each discards itself on done.
_background_tasks: set[asyncio.Task[Any]] = set()


def _holder_id(label: str) -> str:
    return f"{_HOLDER_PREFIX}{label}"


def _quote_flush_interval_seconds() -> float:
    try:
        from icici_breeze_backend.app.services.pnl_engine_settings import load_pnl_engine_settings

        return float(load_pnl_engine_settings()["quote_flush_interval_seconds"])
    except Exception:
        _logger.debug("quote flush interval lookup failed; using default", exc_info=True)
        try:
            return float(getattr(cfg, "PNL_QUOTE_FLUSH_INTERVAL_SECONDS", 2.0))
        except (TypeError, ValueError):
            return 2.0


def _parse_display_date(value: str):
    import datetime as dt

    try:
        return dt.datetime.strptime(value, "%d-%b-%Y").date()
    except (TypeError, ValueError):
        return None


def resolve_nearest_expiry(exchange_code: str, stock_code: str, *, today: date | None = None) -> str | None:
    """Nearest expiry >= today (display format DD-Mon-YYYY).

    Deliberately `>=` rather than `>` -- a live system chain should keep
    tracking through its own expiry day, not roll to next week's while the
    current day's contracts are still tradeable.
    """
    day = today or datetime.now(IST).date()
    try:
        for entry in get_underlyings(exchange_code) or []:
            if str(entry.get("stock_code") or "").strip().upper() != stock_code.upper():
                continue
            future = sorted(
                parsed
                for d in (entry.get("expiry_dates") or [])
                if (parsed := _parse_display_date(d)) and parsed >= day
            )
            if future:
                return future[0].strftime("%d-%b-%Y")
    except Exception:
        _logger.warning("resolve_nearest_expiry failed for %s/%s", stock_code, exchange_code, exc_info=True)
    return None


def maybe_trigger_system_prefetch(user_id: str) -> None:
    """Fire-and-forget. Call from an authenticated request (needs the
    broker-token contextvar already set for this request). Safe to call on
    every request; near-zero cost once today's prefetch has already run.
    """
    if not is_india_market_open():
        return
    today = datetime.now(IST).date()
    with _lock:
        global _prefetch_in_progress, _prefetch_last_attempt_monotonic
        if _prefetch_date == today and not _prefetch_last_error:
            return
        if _prefetch_in_progress:
            return
        if _prefetch_date == today and _prefetch_last_error:
            last = _prefetch_last_attempt_monotonic
            if last is not None and (time.monotonic() - last) < _RETRY_COOLDOWN_SECONDS:
                return
        _prefetch_in_progress = True
        _prefetch_last_attempt_monotonic = time.monotonic()

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running event loop (e.g. a sync test call) -- run inline rather than dropping it.
        _run_system_prefetch_blocking(user_id, today)
        return

    task = loop.create_task(asyncio.to_thread(_run_system_prefetch_blocking, user_id, today))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def _run_system_prefetch_blocking(user_id: str, today: date) -> None:
    from icici_breeze_backend.app.services.breeze_websocket_manager import (
        release_holder,
        sync_holder_chain_subscriptions,
    )
    from icici_breeze_backend.app.services.processor import processor as Processor

    proc = Processor()
    errors: list[str] = []
    expiries: dict[str, str] = {}
    subscribed_at: dict[str, float] = {}
    for exchange_code, stock_code, label in _SCRIPS:
        holder_id = _holder_id(label)
        try:
            expiry = resolve_nearest_expiry(exchange_code, stock_code, today=today)
            if not expiry:
                errors.append(f"{label}: no tradeable expiry found")
                continue
            # Clear any prior day's (possibly different-expiry) registration first --
            # `active_chains.register_holder_chain` only ever ADDS a chain key for a
            # holder, it never drops a stale one when the expiry rolls. A per-tab
            # browser holder rarely hits this (tabs close/reopen); this holder runs
            # forever, so it must self-clean before each day's subscribe or a weekly
            # expiry rollover leaks a permanently-stale chain into `chain:active`.
            release_holder(holder_id)
            sync_holder_chain_subscriptions(proc, user_id, holder_id, stock_code, exchange_code, expiry)
            expiries[label] = expiry
            subscribed_at[label] = time.monotonic()
        except Exception as exc:
            errors.append(f"{label}: {exc}")
            _logger.warning("system prefetch failed for %s", label, exc_info=True)

    with _lock:
        global _prefetch_date, _prefetch_in_progress, _prefetch_last_error
        _prefetch_date = today
        _prefetch_in_progress = False
        _prefetch_last_error = "; ".join(errors) if errors else None
        _prefetch_expiries.update(expiries)
        _prefetch_subscribed_at_monotonic.update(subscribed_at)


def prefetch_state() -> dict[str, Any]:
    """Snapshot for health computation + tests."""
    with _lock:
        return {
            "date": _prefetch_date,
            "in_progress": _prefetch_in_progress,
            "last_error": _prefetch_last_error,
            "expiries": dict(_prefetch_expiries),
            "subscribed_at_monotonic": dict(_prefetch_subscribed_at_monotonic),
        }


def reset_state_for_tests() -> None:
    """Test-only: reset all module state back to a fresh-process baseline."""
    with _lock:
        global _prefetch_date, _prefetch_in_progress, _prefetch_last_attempt_monotonic, _prefetch_last_error
        _prefetch_date = None
        _prefetch_in_progress = False
        _prefetch_last_attempt_monotonic = None
        _prefetch_last_error = None
        _prefetch_expiries.clear()
        _prefetch_subscribed_at_monotonic.clear()


def _max_tick_age_seconds(exchange_code: str, ws_symbols: list[str]) -> float | None:
    """Per-scrip (not global) tick freshness: freshest `received_at` among this
    scrip's subscribed WS raw-quote cache entries, or None if none have ticked yet."""
    latest: float | None = None
    for sym in ws_symbols:
        parsed = parse_ws_symbol(sym)
        if parsed is None:
            continue
        _, token = parsed
        payload = cache_get_json(ws_raw_quote_key(exchange_code, token))
        if not isinstance(payload, dict):
            continue
        received_at = payload.get("received_at")
        if isinstance(received_at, (int, float)) and (latest is None or received_at > latest):
            latest = received_at
    if latest is None:
        return None
    return max(0.0, time.time() - latest)


def get_system_health_status() -> dict[str, Any]:
    from icici_breeze_backend.app.services.breeze_websocket_manager import get_playground_status

    now = datetime.now(IST)
    poll_interval = _quote_flush_interval_seconds()
    base = {"poll_interval_seconds": poll_interval}

    if not is_india_market_open(now):
        return {
            **base,
            "status": "gray",
            "reason": f"Market closed ({market_closed_reason(now)})",
            "market_open": False,
            "prefetch_done": False,
            "detail": {},
        }

    state = prefetch_state()
    today = now.date()
    if state["date"] != today or not state["expiries"]:
        return {
            **base,
            "status": "gray",
            "reason": "Market open, waiting for first login to start data feed",
            "market_open": True,
            "prefetch_done": False,
            "detail": {},
        }

    ws_status = get_playground_status()
    connected = bool(ws_status.get("connected"))
    stale_after = max(_STALE_FLOOR_SECONDS, poll_interval * _STALE_MULTIPLE)
    warmup_after = max(_WARMUP_FLOOR_SECONDS, poll_interval * _WARMUP_MULTIPLE)

    detail: dict[str, Any] = {}
    stale_labels: list[str] = []
    warming_labels: list[str] = []
    for exchange_code, stock_code, label in _SCRIPS:
        expiry = state["expiries"].get(label)
        tokens = list_ws_stock_tokens_for_liquid_contracts(exchange_code, stock_code, expiry) if expiry else []
        age = _max_tick_age_seconds(exchange_code, tokens)
        sub_at = state["subscribed_at_monotonic"].get(label)
        warming_up = age is None and sub_at is not None and (time.monotonic() - sub_at) < warmup_after
        stale = (age is not None and age > stale_after) or (age is None and not warming_up)
        detail[label] = {
            "exchange_code": exchange_code,
            "stock_code": stock_code,
            "expiry_display": expiry,
            "subscribed_tokens": len(tokens),
            "last_tick_age_seconds": age,
            "stale": stale,
            "warming_up": warming_up,
        }
        if stale:
            stale_labels.append(label.upper())
        elif warming_up:
            warming_labels.append(label.upper())

    if not connected:
        return {
            **base,
            "status": "red",
            "reason": f"WebSocket disconnected ({ws_status.get('last_error') or state['last_error'] or 'no error detail'})",
            "market_open": True,
            "prefetch_done": True,
            "detail": detail,
        }
    if stale_labels:
        worst = detail[stale_labels[0].lower()]
        age = worst["last_tick_age_seconds"]
        age_txt = f"{age:.0f}s ago" if age is not None else "no ticks yet"
        names = " & ".join(stale_labels)
        return {
            **base,
            "status": "red",
            "reason": f"{names} feed stalled ({age_txt})",
            "market_open": True,
            "prefetch_done": True,
            "detail": detail,
        }
    if warming_labels:
        return {
            **base,
            "status": "gray",
            "reason": f"{' & '.join(warming_labels)} subscribing, waiting for first ticks",
            "market_open": True,
            "prefetch_done": True,
            "detail": detail,
        }
    return {
        **base,
        "status": "green",
        "reason": "NIFTY & SENSEX chains live",
        "market_open": True,
        "prefetch_done": True,
        "detail": detail,
    }
