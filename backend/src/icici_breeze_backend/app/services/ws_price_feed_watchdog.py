"""Market-hours watchdog that re-subscribes silent WS price feeds.

The subscription bookkeeping in `breeze_websocket_manager` records what we asked
ICICI for; nothing ever reconciles it against ticks actually arriving. Every
subscribe path short-circuits on that bookkeeping (`_subscribe_stock_token_batch`'s
`is_new` check), so the *first* subscribe of the day for a token is the only one
that ever reaches ICICI -- a later browser tab opening the same chain silently
attaches to the existing entry. If that first subscribe produced no feed, every
chain stays cold until the process restarts, which is exactly the failure this
module exists to break.

Two triggers, both re-issuing `subscribe_feeds` without unsubscribing first (a
duplicate subscribe is assumed idempotent -- the same assumption
`_subscribe_order_notifications` already relies on):

  * **At the open**, unconditionally, whether or not ticks are flowing. Chains
    are deliberately allowed to subscribe pre-market, and whether ICICI honours a
    subscription issued before the bell is not something we can verify from here
    -- so instead of assuming either way, re-arm once the market is actually
    open and the question stops mattering.
  * **On whole-feed silence** during market hours. Per-chain, not per-token:
    illiquid deep-OTM strikes legitimately go quiet for minutes, but a live chain
    always has *something* ticking, so total silence is unambiguous breakage.

The order-notification feed has its own equivalent (`order_feed_watchdog_tick`);
this one covers price ticks, which that watchdog explicitly does not.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import date, datetime

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.timezone import IST
from icici_breeze_backend.app.db.redis_client import cache_get_json
from icici_breeze_backend.app.services.reference_data.active_chains import (
    list_active_chains,
    parse_chain_registry_key,
)
from icici_breeze_backend.app.services.reference_data.keys import index_spot_key, ws_raw_quote_key
from icici_breeze_backend.app.services.reference_data.ws_token_index import (
    list_ws_stock_tokens_for_liquid_contracts,
    parse_ws_symbol,
)

_logger = logging.getLogger(__name__)
_lock = threading.RLock()

# Whole-feed silence during market hours before we force a re-subscribe. Well above
# the health dot's ~6s stale threshold so ordinary flush jitter never trips it.
_SILENCE_SECONDS = 45.0
# Minimum gap between forced re-subscribes of the same target, so a genuinely dead
# session degrades to one batch a minute instead of one every tick.
_THROTTLE_SECONDS = 60.0
_DEFAULT_INTERVAL_SECONDS = 30.0
_MIN_INTERVAL_SECONDS = 10.0
# Settle time after the bell before the unconditional pass -- subscribing in the
# same second the market opens is the case least likely to be honoured.
_OPEN_PASS_DELAY_SECONDS = 10.0

_INDEX_SPOT_TARGET = "__index_spot__"

# target -> monotonic timestamp we last saw it healthy (or first saw it silent)
_last_ok: dict[str, float] = {}
# target -> monotonic timestamp of the last forced re-subscribe
_last_forced: dict[str, float] = {}
_open_pass_date: date | None = None


def _interval_seconds() -> float:
    try:
        return max(
            _MIN_INTERVAL_SECONDS,
            float(
                getattr(cfg, "WS_PRICE_FEED_WATCHDOG_INTERVAL_SECONDS", _DEFAULT_INTERVAL_SECONDS)
            ),
        )
    except (TypeError, ValueError):
        return _DEFAULT_INTERVAL_SECONDS


def _newest_tick_age_seconds(exchange_code: str, ws_symbols: list[str]) -> float | None:
    """Freshest tick across a chain's tokens, or None if none have ever ticked."""
    latest: float | None = None
    for sym in ws_symbols:
        parsed = parse_ws_symbol(sym)
        if parsed is None:
            continue
        _prefix, token = parsed
        payload = cache_get_json(ws_raw_quote_key(exchange_code, token))
        if not isinstance(payload, dict):
            continue
        received_at = payload.get("received_at")
        if isinstance(received_at, (int, float)) and (latest is None or received_at > latest):
            latest = received_at
    if latest is None:
        return None
    return max(0.0, time.time() - latest)


def _index_spot_ticking() -> bool:
    """True if either index has a live spot cached. The key carries a 15s TTL, so
    its mere presence during market hours means ticks are arriving."""
    for label in ("nifty", "sensex"):
        if isinstance(cache_get_json(index_spot_key(label)), dict):
            return True
    return False


def _throttled(target: str, now: float) -> bool:
    last = _last_forced.get(target)
    return last is not None and (now - last) < _THROTTLE_SECONDS


def _mark_forced(target: str, now: float) -> None:
    _last_forced[target] = now
    # Restart the silence clock so the feed gets the full throttle window to recover
    # before we count it silent again.
    _last_ok[target] = now


def _silent_for(target: str, now: float, age: float | None) -> float:
    """How long this target has looked silent. `age` is the real tick age when we
    have one; when a target has *never* ticked we fall back to how long this
    watchdog has been watching it, so a chain subscribed into a dead feed still
    trips rather than sitting at `age is None` forever."""
    if age is not None:
        return age
    first_seen = _last_ok.setdefault(target, now)
    return now - first_seen


def _force_chain(chain_key: str) -> None:
    from icici_breeze_backend.app.services.breeze_websocket_manager import (
        force_resubscribe_tokens,
    )

    parsed = parse_chain_registry_key(chain_key)
    if parsed is None:
        return
    exchange_code, stock_code, expiry_display = parsed
    tokens = list_ws_stock_tokens_for_liquid_contracts(exchange_code, stock_code, expiry_display)
    if not tokens:
        # Tokens are re-resolved from the scrip master on every pass rather than
        # read back from holder bookkeeping, so a chain registered while the token
        # map was empty self-heals here once the map is populated.
        _logger.warning(
            "price-feed watchdog: no tradeable tokens resolve for %s; nothing to re-subscribe",
            chain_key,
        )
        return
    ok = force_resubscribe_tokens(tokens)
    _logger.info(
        "price-feed watchdog: forced re-subscribe chain=%s tokens=%s ok=%s",
        chain_key,
        len(tokens),
        ok,
    )


def _force_index_spot() -> None:
    from icici_breeze_backend.app.services.breeze_websocket_manager import current_ws_user_id
    from icici_breeze_backend.app.services.index_spot_feed import sync_index_spot_subscriptions
    from icici_breeze_backend.app.services.processor import processor

    user_id = current_ws_user_id()
    if user_id is None:
        return
    ok = sync_index_spot_subscriptions(processor(), user_id, force=True)
    _logger.info("price-feed watchdog: forced re-subscribe index spot ok=%s", ok)


def _force_order_feed() -> None:
    from icici_breeze_backend.app.services.breeze_websocket_manager import (
        current_ws_user_id,
        ensure_order_feed,
    )
    from icici_breeze_backend.app.services.processor import processor

    user_id = current_ws_user_id()
    if user_id is None:
        return
    ensure_order_feed(processor(), user_id)


def _run_open_pass(now: float) -> None:
    """One unconditional re-arm of everything currently subscribed."""
    chains = list_active_chains()
    _logger.info("price-feed watchdog: market-open re-subscribe pass (%s active chains)", len(chains))
    for chain_key in chains:
        _force_chain(chain_key)
        _mark_forced(chain_key, now)
    _force_index_spot()
    _mark_forced(_INDEX_SPOT_TARGET, now)
    _force_order_feed()


def _check_silent_feeds(now: float) -> None:
    for chain_key in list_active_chains():
        parsed = parse_chain_registry_key(chain_key)
        if parsed is None:
            continue
        exchange_code, stock_code, expiry_display = parsed
        tokens = list_ws_stock_tokens_for_liquid_contracts(
            exchange_code, stock_code, expiry_display
        )
        age = _newest_tick_age_seconds(exchange_code, tokens) if tokens else None
        if age is not None and age <= _SILENCE_SECONDS:
            _last_ok[chain_key] = now
            continue
        silent_for = _silent_for(chain_key, now, age)
        if silent_for < _SILENCE_SECONDS or _throttled(chain_key, now):
            continue
        _logger.warning(
            "price-feed watchdog: chain %s silent for %.0fs; re-subscribing",
            chain_key,
            silent_for,
        )
        _force_chain(chain_key)
        _mark_forced(chain_key, now)

    if _index_spot_ticking():
        _last_ok[_INDEX_SPOT_TARGET] = now
        return
    if _silent_for(_INDEX_SPOT_TARGET, now, None) < _SILENCE_SECONDS or _throttled(
        _INDEX_SPOT_TARGET, now
    ):
        return
    _logger.warning("price-feed watchdog: index spot silent; re-subscribing")
    _force_index_spot()
    _mark_forced(_INDEX_SPOT_TARGET, now)


def price_feed_watchdog_tick(now_ist: datetime | None = None) -> None:
    """One watchdog pass. Safe to call directly (tests); never raises."""
    from icici_breeze_backend.app.services.market_calendar import (
        get_calendar_config,
        is_market_open,
        is_trading_day,
    )

    global _open_pass_date

    now_dt = now_ist or datetime.now(IST)
    now = time.monotonic()
    # Held across the whole pass, subscribe calls included: each tick runs on an
    # arbitrary `asyncio.to_thread` worker, so a pass that overruns the interval
    # would otherwise overlap the next one and double-force every silent chain.
    with _lock:
        if not is_trading_day(now_dt):
            return
        if not is_market_open(now_dt):
            # Silence before the bell is expected, not a fault -- don't chase it, and
            # don't let the pre-open quiet count towards the silence clock.
            _last_ok.clear()
            return

        open_at = get_calendar_config().open_time(now_dt)
        past_settle = (now_dt - open_at).total_seconds() >= _OPEN_PASS_DELAY_SECONDS
        if _open_pass_date != now_dt.date() and past_settle:
            from icici_breeze_backend.app.services.breeze_websocket_manager import (
                current_ws_user_id,
            )

            if current_ws_user_id() is None:
                # Nobody logged in yet, so there is no session to subscribe with and
                # nothing subscribed to re-arm. Leave today's pass unclaimed so it
                # runs for real once someone logs in -- the login trigger
                # (`start_prefetch_for_new_broker_session`) covers the gap meanwhile.
                return
            _open_pass_date = now_dt.date()
            _run_open_pass(now)
            return

        _check_silent_feeds(now)


async def run_price_feed_watchdog_loop() -> None:
    """Cancelled only via the FastAPI lifespan's `task.cancel()`; mirrors
    `run_order_feed_watchdog_loop`'s error handling so one bad pass can't kill it."""
    interval = _interval_seconds()
    _logger.info("Price-feed watchdog started (interval=%.0fs)", interval)
    while True:
        try:
            await asyncio.sleep(interval)
            await asyncio.to_thread(price_feed_watchdog_tick)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            _logger.exception("Price-feed watchdog tick failed")


def reset_state_for_tests() -> None:
    """Test-only: reset module state back to a fresh-process baseline."""
    global _open_pass_date
    with _lock:
        _open_pass_date = None
        _last_ok.clear()
        _last_forced.clear()
