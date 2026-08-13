"""OS worker: normalize raw WS ticks and publish canonical option chains."""
from __future__ import annotations

import logging
import os
import signal
import sys
import threading
import time

from icici_breeze_backend.app.services.chain_build_service import refresh_active_chains
from icici_breeze_backend.app.services.reference_data.active_chains import list_active_chains
from icici_breeze_backend.app.services.reference_data.keys import WS_TICK_DIRTY_CHANNEL

_logger = logging.getLogger(__name__)
_stop = threading.Event()
# Safety cap while draining a burst of dirty-tick notifications (see _pubsub_loop) --
# not expected to ever bind in practice, just prevents an unbounded drain loop.
_PUBSUB_DRAIN_LIMIT = 10_000

# Serializes the two loops below and enforces the rebuild cadence: both the timer
# and the tick feed funnel through `_maybe_refresh`, so a burst of ticks can never
# rebuild more often than the configured interval, and the two loops can never
# rebuild the same chains concurrently.
_refresh_lock = threading.Lock()
_last_refresh_monotonic: float = 0.0

# The rebuild interval follows the user's P&L recalc setting, which lives in SQLite
# and is read fresh on every call by design. Re-reading it per dirty-tick message
# would trade the CPU we're saving for disk I/O, so it's cached for this long.
_INTERVAL_CACHE_SECONDS = 30.0
_interval_cache: tuple[float, float] | None = None  # (value, monotonic_expiry)


def _poll_ms() -> int:
    """How often the timer loop *wakes*, not how often it rebuilds -- the cadence
    gate in `_maybe_refresh` decides that. Kept short so a change to the P&L recalc
    setting takes effect within a wake rather than within a rebuild interval."""
    from icici_breeze_backend.core import config as cfg

    try:
        return int(getattr(cfg, "CHAIN_BUILDER_POLL_MS", 250) or 250)
    except (TypeError, ValueError):
        return 250


def _refresh_interval_seconds() -> float:
    """Minimum gap between chain rebuilds, following the user's P&L recalc interval
    (Settings > Advanced).

    Chains feed the screens, and the Portfolio already polls them at exactly this
    interval, so rebuilding faster than it is pure waste. Note the stop-loss /
    profit-booking engine does *not* read canonical chains -- it evaluates against
    its own tick-fed quote buffer -- so this cadence never delays a rule hit.
    """
    global _interval_cache
    now = time.monotonic()
    cached = _interval_cache
    if cached is not None and now < cached[1]:
        return cached[0]
    value = 2.0
    try:
        from icici_breeze_backend.app.services.pnl_engine_settings import (
            load_pnl_engine_settings,
        )

        value = float(load_pnl_engine_settings()["pnl_recompute_interval_seconds"])
    except Exception:
        _logger.debug("P&L recalc interval lookup failed; using %.1fs", value, exc_info=True)
    value = max(0.25, value)
    _interval_cache = (value, now + _INTERVAL_CACHE_SECONDS)
    return value


def _maybe_refresh() -> None:
    """Rebuild every active chain, at most once per `_refresh_interval_seconds`.

    The elapsed check is against the *end* of the previous rebuild, so the interval
    is genuine idle time between rebuilds rather than a start-to-start rate that a
    slow rebuild could saturate.
    """
    global _last_refresh_monotonic
    interval = _refresh_interval_seconds()
    with _refresh_lock:
        if time.monotonic() - _last_refresh_monotonic < interval:
            return
        try:
            chains = list_active_chains()
            if chains:
                refresh_active_chains(
                    chains,
                    resolve_lot_size=_resolve_lot_size,
                    resolve_freeze_quantity=_resolve_freeze_quantity,
                    should_continue=_not_stopped,
                )
        except Exception:
            _logger.exception("chain-builder refresh failed")
        finally:
            _last_refresh_monotonic = time.monotonic()


def _load_env() -> None:
    try:
        from dotenv import load_dotenv

        root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
        env_path = os.path.join(root, ".env")
        if os.path.isfile(env_path):
            load_dotenv(env_path, override=True)
        else:
            load_dotenv(override=True)
    except ImportError:
        pass


def _resolve_lot_size(stock_code: str, expiry_display: str, exchange_code: str) -> int | None:
    from icici_breeze_backend.app.services.processor import processor

    return processor().fetch_lot_size(stock_code, expiry_display, exchange_code=exchange_code)


def _resolve_freeze_quantity(
    stock_code: str,
    expiry_display: str,
    exchange_code: str,
    lot_size: int,
) -> int | None:
    from icici_breeze_backend.app.services.processor import processor

    proc = processor()
    try:
        qty_limits = proc.fetch_qty_limits(stock_code, exchange_code=exchange_code)
        if qty_limits is None:
            return None
        return (max(1, int(qty_limits)) // lot_size) * lot_size
    except (TypeError, ValueError):
        return None


def _not_stopped() -> bool:
    return not _stop.is_set()


def _refresh_loop() -> None:
    """Safety net for when ticks are quiet: the tick feed drives rebuilds in the
    common case, and this only wins the cadence gate when no tick has arrived for
    a full interval (pre-open, a dead feed, an underlying that simply isn't trading)."""
    wake = max(0.05, _poll_ms() / 1000.0)
    while not _stop.is_set():
        started = time.monotonic()
        _maybe_refresh()
        elapsed = time.monotonic() - started
        _stop.wait(max(0.0, wake - elapsed))


def _pubsub_loop() -> None:
    from icici_breeze_backend.app.db.redis_client import get_redis, redis_using_memory_fallback

    if redis_using_memory_fallback():
        return
    try:
        client = get_redis()
        pubsub = client.pubsub(ignore_subscribe_messages=True)
        pubsub.subscribe(WS_TICK_DIRTY_CHANNEL)
        while not _stop.is_set():
            message = pubsub.get_message(timeout=0.5)
            if message is None or message.get("type") != "message":
                continue
            # One dirty-notification is published per changed tick, which under a
            # live tick feed can mean hundreds of messages per second -- but they
            # all just mean "some active chain changed," so a burst only ever
            # needs one refresh, not one per message. Drain whatever's already
            # queued (non-blocking) before refreshing, or a fast tick feed makes
            # this loop fall permanently behind, redoing the same refresh over
            # and over long after the ticks that triggered it are stale (this is
            # also what made shutdown slow: a large backlog of queued, already
            # long-superseded refreshes to work through before noticing _stop).
            drained = 0
            while drained < _PUBSUB_DRAIN_LIMIT:
                if pubsub.get_message(timeout=0) is None:
                    break
                drained += 1
            # Draining alone still left this rebuilding back-to-back for as long as
            # ticks kept arriving -- the drain bounds work *per burst*, not the rate
            # at which bursts are serviced. The cadence gate is what bounds the rate.
            _maybe_refresh()
    except Exception:
        _logger.debug("chain-builder pubsub unavailable; poll-only mode", exc_info=True)


def _handle_signal(signum: int, _frame: object) -> None:
    _logger.info("chain-builder received signal %s; stopping", signum)
    _stop.set()


def main() -> int:
    _load_env()
    # Shared with the API process rather than basicConfig: this worker is a separate OS
    # process, and rolling its own config meant it silently opted out of the secret
    # redaction filter and of any future handler wired up in configure_logging.
    from icici_breeze_backend.app.core.logging import configure_logging

    configure_logging(
        level=os.environ.get("LOG_LEVEL", "INFO"), process_name="chain-builder"
    )
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    _logger.info(
        "chain-builder worker starting (wake=%sms, rebuild interval=%.1fs)",
        _poll_ms(),
        _refresh_interval_seconds(),
    )
    pubsub_thread = threading.Thread(target=_pubsub_loop, name="chain-builder-pubsub", daemon=True)
    pubsub_thread.start()
    _refresh_loop()
    _logger.info("chain-builder worker stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
