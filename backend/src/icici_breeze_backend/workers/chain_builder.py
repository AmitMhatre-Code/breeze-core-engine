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


def _poll_ms() -> int:
    from icici_breeze_backend.core import config as cfg

    try:
        return int(getattr(cfg, "CHAIN_BUILDER_POLL_MS", 250) or 250)
    except (TypeError, ValueError):
        return 250


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
    interval = max(0.05, _poll_ms() / 1000.0)
    while not _stop.is_set():
        started = time.monotonic()
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
        elapsed = time.monotonic() - started
        _stop.wait(max(0.0, interval - elapsed))


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
            chains = list_active_chains()
            if not chains:
                continue
            refresh_active_chains(
                chains,
                resolve_lot_size=_resolve_lot_size,
                resolve_freeze_quantity=_resolve_freeze_quantity,
                should_continue=_not_stopped,
            )
    except Exception:
        _logger.debug("chain-builder pubsub unavailable; poll-only mode", exc_info=True)


def _handle_signal(signum: int, _frame: object) -> None:
    _logger.info("chain-builder received signal %s; stopping", signum)
    _stop.set()


def main() -> int:
    _load_env()
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    _logger.info("chain-builder worker starting (poll=%sms)", _poll_ms())
    pubsub_thread = threading.Thread(target=_pubsub_loop, name="chain-builder-pubsub", daemon=True)
    pubsub_thread.start()
    _refresh_loop()
    _logger.info("chain-builder worker stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
