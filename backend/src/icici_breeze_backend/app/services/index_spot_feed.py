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
# Cash stock_code must be the ICICI scrip-master ShortName, not the display
# name -- BSE's SENSEX row has Token="SENSEX", ShortName="BSESEN", so "BSESEN"
# is what get_stock_token_value()/get_quotes() need here (NSE's NIFTY row
# happens to have ShortName="NIFTY", so it looks like the display name).
_INDEX_SCRIPS: tuple[tuple[str, str, str, str, str], ...] = (
    ("NSE", "NIFTY", cfg.NFO, "NIFTY", "nifty"),
    ("BSE", "BSESEN", cfg.BFO, "BSESEN", "sensex"),
)

_INDEX_SPOT_TTL_SECONDS = 15

_symbol_to_label: dict[str, str] = {}
_previous_close: dict[str, float] = {}
_subscribed_date: date | None = None
_listener_registered = False

# Dynamic (non-index) underlyings whose cash spot we keep warm for the
# quote_source_router chain-spot cache, seeded on demand from portfolio/orders
# chain builds (see `sync_underlying_spot_subscriptions`). Maps a subscribed
# cash ws stock_token -> (options exchange_code, options stock_code) so
# `_on_raw_tick` knows which `remember_chain_spot` key to seed. Unlike the fixed
# index scrips these carry no navbar `index_spot_key`/previous_close -- the
# SPOT column and chain-completeness gate only need the raw live spot.
_underlying_targets: dict[str, tuple[str, str]] = {}
# Every cash ws stock_token we've already called subscribe_feeds for (indices +
# dynamic underlyings), so the two subsystems never double-subscribe the same
# token (e.g. a portfolio holding NIFTY, already subscribed by the index feed).
_subscribed_cash_tokens: set[str] = set()
# (cash_exchange, cash_stock_code) pairs already processed by
# `sync_underlying_spot_subscriptions` -- the hot-path dedup so a per-chain-build
# trigger is a single set lookup and never re-resolves tokens or touches the SDK.
_synced_underlying_scrips: set[tuple[str, str]] = set()


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
        underlying = _underlying_targets.get(symbol)
        prev_close = _previous_close.get(label) if label else None
    if label is None and underlying is None:
        return
    ltp = _extract_ltp(raw)
    if ltp is None:
        return

    if label is not None:
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

    if underlying is not None:
        opt_exchange, opt_stock_code = underlying
        try:
            from icici_breeze_backend.app.services.quote_source_router import (
                remember_chain_spot,
            )

            remember_chain_spot(opt_exchange, opt_stock_code, ltp)
        except Exception:
            pass


def _register_listener_once() -> None:
    global _listener_registered
    with _lock:
        if _listener_registered:
            return
        register_raw_tick_listener(_on_raw_tick)
        _listener_registered = True


def _pick_quote_row(succ: Any, cash_exchange: str) -> dict[str, Any] | None:
    """`get_quotes` can return multiple rows for one stock_code -- e.g. BSESEN
    comes back as a junk exchange_code="NA"/ltp=0 placeholder followed by the
    real exchange_code="BSE" row. Match on the exchange we actually asked for
    instead of blindly taking the first element."""
    if isinstance(succ, dict):
        return succ
    if not isinstance(succ, list) or not succ:
        return None
    for row in succ:
        if isinstance(row, dict) and row.get("exchange_code") == cash_exchange:
            return row
    first = succ[0]
    return first if isinstance(first, dict) else None


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
    row = _pick_quote_row(succ, cash_exchange)
    if not isinstance(row, dict):
        return None
    try:
        pc = float(row.get("previous_close") or 0)
    except (TypeError, ValueError):
        return None
    return pc if pc > 0 else None


def sync_index_spot_subscriptions(proc: "Processor", user_id: str, *, force: bool = False) -> bool:
    """Idempotent per IST trading day. Call from the same daily trigger as
    `system_chain_health.maybe_trigger_system_prefetch` -- safe to call on every
    request once today's subscriptions are already in place.

    Returns False when the subscribe attempt didn't happen because there's no
    live broker session -- the caller must not treat that as a permanent
    daily "done" state, or a session that was dead on the first trigger of
    the day will never get retried (see `system_chain_health._prefetch_last_error`).

    `force=True` bypasses both the once-per-day latch and the per-token
    already-subscribed guard, re-issuing `subscribe_feeds` outright. The latch
    records what we asked for, never whether ticks arrived, so a login or the
    market-open pass must be able to re-arm a feed the latch believes is fine
    (see `ws_price_feed_watchdog`)."""
    global _subscribed_date
    today = datetime.now(IST).date()
    with _lock:
        if _subscribed_date == today and not force:
            return True

    from icici_breeze_backend.app.services.breeze_websocket_manager import _ensure_ws

    sdk = _ensure_ws(proc, user_id)
    if sdk is None:
        return False

    _register_listener_once()
    # breeze_connect's get_stock_token_value() reads sdk.interval without the SDK ever
    # initializing it in __init__ -- it's normally set as a side effect of subscribe_feeds().
    # Calling get_stock_token_value() directly on a freshly connected sdk (as below) hits an
    # uninitialized-attribute AttributeError inside the SDK, which it then returns instead of
    # raising, breaking our tuple unpack. Prime it so the SDK's own check passes.
    if not hasattr(sdk, "interval"):
        sdk.interval = ""
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
            token = str(exch_token)
            with _lock:
                _symbol_to_label[token] = label
                already_subscribed = token in _subscribed_cash_tokens
                _subscribed_cash_tokens.add(token)
            if force or not already_subscribed:
                sdk.subscribe_feeds(
                    exchange_code=cash_exchange,
                    stock_code=cash_stock_code,
                    get_exchange_quotes=True,
                    get_market_depth=False,
                )
            # Only a REST call's worth of value once per day -- skip it when we
            # already have today's close, so a forced re-subscribe (which can
            # repeat on the watchdog's throttle) doesn't re-hit `get_quotes`.
            with _lock:
                have_prev_close = _previous_close.get(label) is not None
            if not have_prev_close:
                prev_close = _fetch_previous_close(sdk, cash_exchange, cash_stock_code)
                if prev_close is not None:
                    with _lock:
                        _previous_close[label] = prev_close
        except Exception:
            _logger.warning("index spot subscribe failed for %s", label, exc_info=True)

    with _lock:
        _subscribed_date = today
    return True


def _cash_exchange_for_options(opt_exchange: str) -> str | None:
    """Cash exchange whose spot underlies an F&O options exchange, or None for
    exchanges we don't keep a spot feed for."""
    ex = str(opt_exchange or "").strip().upper()
    if ex == cfg.NFO:
        return "NSE"
    if ex == cfg.BFO:
        return "BSE"
    return None


def sync_underlying_spot_subscriptions(
    proc: "Processor",
    user_id: str,
    underlyings: "list[tuple[str, str, str, str]]",
) -> bool:
    """Subscribe live cash spot feeds for arbitrary (non-index) option underlyings,
    keeping `quote_source_router`'s chain-spot cache warm from real intraday ticks
    instead of stale bhavcopy closes -- so the Portfolio SPOT column (and any other
    chain-derived spot) is live for single-stock options too, not just NIFTY/SENSEX.

    `underlyings`: iterable of (cash_exchange, cash_stock_code, opt_exchange,
    opt_stock_code). Idempotent and cheap on repeat calls -- the common case
    (every scrip already synced) is a single set lookup with no SDK/token work, so
    this is safe to call on every chain build. A scrip that fails to resolve/subscribe
    is left unsynced and retried on the next call. Dedups token-level against the
    index feed so a portfolio holding NIFTY never double-subscribes its cash scrip.

    Returns False only when a subscribe was actually needed but there was no live
    broker session (mirrors `sync_index_spot_subscriptions`), so a caller gating a
    retry on the result doesn't treat a dead-session first call as done."""
    pending: list[tuple[str, str, str, str]] = []
    with _lock:
        for cash_exchange, cash_stock_code, opt_exchange, opt_stock_code in underlyings:
            scrip_key = (cash_exchange.upper(), cash_stock_code.upper())
            if scrip_key in _synced_underlying_scrips:
                continue
            pending.append((cash_exchange, cash_stock_code, opt_exchange, opt_stock_code))
    if not pending:
        return True

    from icici_breeze_backend.app.services.breeze_websocket_manager import _ensure_ws

    sdk = _ensure_ws(proc, user_id)
    if sdk is None:
        return False

    _register_listener_once()
    # Same SDK quirk as `sync_index_spot_subscriptions` -- get_stock_token_value reads
    # sdk.interval, normally initialized as a side effect of subscribe_feeds().
    if not hasattr(sdk, "interval"):
        sdk.interval = ""
    for cash_exchange, cash_stock_code, opt_exchange, opt_stock_code in pending:
        try:
            exch_token, _depth_token = sdk.get_stock_token_value(
                exchange_code=cash_exchange,
                stock_code=cash_stock_code,
                get_exchange_quotes=True,
                get_market_depth=False,
            )
            if not exch_token:
                continue  # unresolved -- leave unsynced so a later call retries
            token = str(exch_token)
            with _lock:
                # Don't shadow an index token's navbar mapping; the index feed
                # already seeds remember_chain_spot for it.
                if token not in _symbol_to_label:
                    _underlying_targets[token] = (opt_exchange, opt_stock_code)
                already_subscribed = token in _subscribed_cash_tokens
                _subscribed_cash_tokens.add(token)
            if not already_subscribed:
                sdk.subscribe_feeds(
                    exchange_code=cash_exchange,
                    stock_code=cash_stock_code,
                    get_exchange_quotes=True,
                    get_market_depth=False,
                )
            with _lock:
                _synced_underlying_scrips.add(
                    (cash_exchange.upper(), cash_stock_code.upper())
                )
        except Exception:
            _logger.warning(
                "underlying spot subscribe failed for %s/%s",
                cash_exchange, cash_stock_code, exc_info=True,
            )
    return True


def ensure_underlying_spot_subscription(
    proc: "Processor", user_id: str, opt_exchange: str, opt_stock_code: str
) -> None:
    """Convenience wrapper: keep one option underlying's cash spot warm, resolving
    the cash exchange/stock_code from the options contract. No-op for exchanges
    without a spot feed, or on any failure -- callers treat live spot as a
    best-effort enrichment over the bhavcopy/REST fallback, never a hard dependency.
    Safe to call from hot chain-build paths (see `sync_underlying_spot_subscriptions`)."""
    cash_exchange = _cash_exchange_for_options(opt_exchange)
    if cash_exchange is None:
        return
    try:
        # ICICI's cash stock_code matches the options ShortName for the underlyings
        # this covers (indices carry their own fixed mapping in the index feed).
        sync_underlying_spot_subscriptions(
            proc, user_id, [(cash_exchange, opt_stock_code, opt_exchange, opt_stock_code)]
        )
    except Exception:
        _logger.debug(
            "ensure_underlying_spot_subscription failed for %s/%s",
            opt_exchange, opt_stock_code, exc_info=True,
        )


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
        _logger.warning(
            "EOD index quote non-200/malformed response for %s/%s: %r", cash_exchange, cash_stock_code, r,
        )
        return None
    succ = r.get("Success") or r.get("success")
    row = _pick_quote_row(succ, cash_exchange)
    if not isinstance(row, dict):
        _logger.warning(
            "EOD index quote empty Success payload for %s/%s: %r", cash_exchange, cash_stock_code, r,
        )
        return None
    ltp = _extract_rest_ltp(row)
    if ltp is None:
        _logger.warning(
            "EOD index quote unparseable ltp for %s/%s: %r", cash_exchange, cash_stock_code, row,
        )
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
        _underlying_targets.clear()
        _subscribed_cash_tokens.clear()
        _synced_underlying_scrips.clear()
