"""Two-worker pipeline: fast SDK callback drain + raw tick Redis cache.

Also stages a second, independent conflation path (`ConflatedTickBuffer`) that
feeds the portfolio P&L engine: the SDK callback (Worker 1) writes the latest
LTP/bid/ask per contract directly into an in-memory buffer, and an asyncio
flush loop (Worker 2, `run_pnl_quote_flush_loop`) drains it onto Redis as
pipelined hash writes every ~2s. This is deliberately decoupled from the
raw-quote coalesce/cache threads above, which serve the option-chain builder
on a much shorter (~100ms) cadence and a different key scheme.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import queue
import threading
import time
from datetime import datetime
from collections.abc import Callable
from typing import Any

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.db.redis_client import get_redis
from icici_breeze_backend.app.services.reference_data.keys import (
    WS_TICK_DIRTY_CHANNEL,
    pnl_quote_key,
    ws_raw_quote_key,
)
from icici_breeze_backend.app.services.reference_data.scrip_index import contract_index_key
from icici_breeze_backend.app.services.reference_data.ws_token_index import (
    exchange_from_ws_prefix,
    parse_ws_symbol,
)
from icici_breeze_backend.app.services.ws_tick_normalize import (
    normalize_icici_tick,
    parse_icici_tick,
)

_logger = logging.getLogger(__name__)

TickListener = Callable[[dict[str, Any]], None]

# (storage_key, raw tick, epoch seconds it reached `ingest_tick`)
RawBatch = list[tuple[str, dict[str, Any], float]]

# Batches the cache thread may have waiting. Kept short on purpose: when Redis is slow
# the drain thread holds ticks back in `_coalesce`, where a newer tick replaces an older
# one for the same contract, instead of queueing minutes of superseded writes.
_PROCESS_QUEUE_MAX_BATCHES = 8
# Pause after a failed Redis write, so a refused connection (which fails at once, unlike
# a timeout) does not spin the cache thread.
_CACHE_ERROR_BACKOFF_SECONDS = 0.5
# A Redis outage fails every batch; say so this often, not ten times a second.
_CACHE_ERROR_LOG_SECONDS = 30.0

_ingest_queue: queue.Queue[tuple[float, Any]] | None = None
# storage_key -> (received_at, raw)
_coalesce: dict[str, tuple[float, dict[str, Any]]] = {}
_coalesce_lock = threading.Lock()
_stop = threading.Event()
_drain_thread: threading.Thread | None = None
_cache_thread: threading.Thread | None = None
_process_queue: queue.Queue[RawBatch] | None = None
_listeners: list[TickListener] = []
_raw_listeners: list[TickListener] = []
# Extra consumers of parsed order-notification events, alongside the hard-wired
# `strategy_group_lifecycle.on_order_notification` call. Used by the dashboard
# day-P&L live baseline; kept a plain list because registration happens once at
# startup and dispatch is single-threaded on the SDK callback thread.
_order_notification_listeners: list[Callable[[Any], None]] = []
_dropped_ticks = 0
_started = False
_start_lock = threading.Lock()
# Monotonic time anything other than an order event last reached `ingest_tick`. Unlike
# `_last_tick_monotonic` this does not need the tick to parse as an option quote, and
# unlike the raw keys it does not need Redis -- it is the one signal that says whether
# the socket itself is delivering.
_last_ingest_monotonic: float | None = None
_cache_stats: dict[str, Any] = {
    "errors": 0,
    "last_error": None,
    "last_error_monotonic": None,
    "last_ok_monotonic": None,
    "last_error_log_monotonic": None,
    "errors_since_log": 0,
    "stale_skipped": 0,
    "thread_restarts": 0,
}


class ConflatedTickBuffer:
    """Thread-safe last-value-wins staging buffer for one flush window.

    Worker 1 (the SDK's `on_ticks` callback thread) calls `update()` inline —
    an O(1) dict write behind a plain lock, cheap enough at 600+ ticks/sec.
    Worker 2 (the asyncio flush loop) calls `drain()` once per clock tick to
    atomically swap out the whole staged dict, so a tick for the same contract
    arriving mid-flush lands in the *next* window rather than being lost.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._staged: dict[str, dict[str, Any]] = {}

    def update(
        self,
        scrip_key: str,
        *,
        ltp: float | None,
        bid: float | None,
        ask: float | None,
        ts: float,
    ) -> None:
        with self._lock:
            self._staged[scrip_key] = {"ltp": ltp, "bid": bid, "ask": ask, "timestamp": ts}

    def drain(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            if not self._staged:
                return {}
            staged, self._staged = self._staged, {}
        return staged

    def __len__(self) -> int:
        with self._lock:
            return len(self._staged)


_pnl_quote_buffer = ConflatedTickBuffer()
_last_tick_monotonic: float | None = None
_pnl_flush_stats: dict[str, Any] = {
    "last_flush_at": None,
    "last_flush_count": 0,
    "flush_errors": 0,
}


def _ingest_qsize() -> int:
    try:
        return int(getattr(cfg, "WS_TICK_INGEST_QUEUE_SIZE", 10_000))
    except (TypeError, ValueError):
        return 10_000


def _coalesce_seconds() -> float:
    try:
        return float(getattr(cfg, "WS_TICK_COALESCE_MS", 50)) / 1000.0
    except (TypeError, ValueError):
        return 0.05


def _raw_tick_payload(raw: Any) -> Any:
    if isinstance(raw, dict):
        return dict(raw)
    return raw


def raw_tick_storage_key(raw: dict[str, Any]) -> str | None:
    """Redis storage key segment for coalescing raw ticks (segment:token)."""
    symbol = raw.get("symbol")
    if symbol:
        parsed = parse_ws_symbol(str(symbol))
        if parsed is not None:
            prefix, token = parsed
            segment = exchange_from_ws_prefix(prefix)
            if segment:
                return ws_raw_quote_key(segment, token)
    for field in ("token", "Token"):
        token_raw = raw.get(field)
        if token_raw is None:
            continue
        try:
            token = int(token_raw)
        except (TypeError, ValueError):
            continue
        exchange_raw = str(raw.get("exchange_code") or raw.get("exchange") or "").upper()
        if "BSE" in exchange_raw:
            segment = cfg.BFO
        else:
            segment = cfg.NFO
        return ws_raw_quote_key(segment, token)
    return None


def _coerce_float(raw: Any) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _extract_price_fields(raw: dict[str, Any]) -> tuple[float | None, float | None, float | None]:
    ltp_raw = raw.get("last") if raw.get("last") is not None else raw.get("ltp")
    bid_raw = raw.get("bPrice") if raw.get("bPrice") is not None else raw.get("best_bid_price")
    ask_raw = raw.get("sPrice") if raw.get("sPrice") is not None else raw.get("best_offer_price")
    return _coerce_float(ltp_raw), _coerce_float(bid_raw), _coerce_float(ask_raw)


def _pnl_mark(ltp: float | None, bid: float | None, ask: float | None) -> float | str | None:
    """The price the P&L engine values a leg at.

    A `last` of 0 is a contract that has not traded, not a price (B-15). Valued at 0 it
    made a short read as full profit and a long as a total loss, which could trip a group
    target or stop, and the exit was then priced at 0 and rejected. With a two-sided book
    the mid stands in. With none the leg is unpriced: `""` overwrites any earlier figure in
    the hash, so the engine reads "no quote" and not an old price with a new timestamp.
    """
    if ltp is None:
        return None
    if ltp > 0:
        return ltp
    if bid is not None and ask is not None and bid > 0 and ask > 0:
        return round((bid + ask) / 2.0, 2)
    return ""


def _stage_pnl_quote(raw: dict[str, Any]) -> None:
    """Worker 1: resolve contract identity + conflate into the in-memory buffer.

    Deliberately independent of the raw-quote coalesce/cache threads below —
    this feeds the portfolio P&L engine's own ~2s Redis hash, not the chain
    builder's raw quote cache.
    """
    global _last_tick_monotonic
    parsed = parse_icici_tick(raw)
    if parsed is None:
        return
    ltp, bid, ask = _extract_price_fields(raw)
    if ltp is None and bid is None and ask is None:
        return
    scrip_key = contract_index_key(
        parsed.exchange_code, parsed.stock_code, parsed.expiry_display, parsed.strike, parsed.right
    )
    _pnl_quote_buffer.update(scrip_key, ltp=_pnl_mark(ltp, bid, ask), bid=bid, ask=ask, ts=time.time())
    _last_tick_monotonic = time.monotonic()


def _route_order_notification(payload: Any) -> bool:
    """Split order-notification events off the shared tick callback. Returns True when the
    payload was an order event and has been handled (so it must NOT be treated as a quote).

    Discriminator is `orderReference`, which price ticks never carry. Never raises: this
    runs on the SDK's WS thread, where an exception would take out tick ingestion for
    every other consumer too.
    """
    try:
        from icici_breeze_backend.app.services.order_notifications import (
            is_order_notification,
            parse_order_notification,
        )

        if not is_order_notification(payload):
            return False
        parsed = parse_order_notification(payload)
        if parsed is not None:
            from icici_breeze_backend.app.services.strategy_group_lifecycle import (
                on_order_notification,
            )

            on_order_notification(parsed)
            for cb in list(_order_notification_listeners):
                try:
                    cb(parsed)
                except Exception:
                    _logger.exception("Order-notification listener failed")
        # Consumed either way: an order event is never a price tick, even if the parse
        # rejected it (e.g. a cash-shape payload).
        return True
    except Exception:
        _logger.exception("Order-notification routing failed")
        return False


def ingest_tick(raw: Any) -> None:
    """Called from SDK on_ticks — notify raw listeners, then enqueue for raw cache pipeline.

    Order notifications arrive on this SAME callback: `subscribe_feeds(
    get_order_notification=True)` opens a separate socket.io client, but the SDK's
    `on_message` dispatches both it and price ticks to `breeze.on_ticks`. So they must be
    split off here, before anything downstream tries to read them as quotes.
    """
    global _dropped_ticks, _last_ingest_monotonic
    payload = _raw_tick_payload(raw)

    if _route_order_notification(payload):
        return
    _last_ingest_monotonic = time.monotonic()

    for listener in list(_raw_listeners):
        try:
            listener(payload)
        except Exception:
            pass
    if isinstance(payload, dict):
        try:
            _stage_pnl_quote(payload)
        except Exception:
            _logger.debug("PNL quote staging failed for tick", exc_info=True)
    q = _ingest_queue
    if q is None:
        return
    # Stamped here, on arrival, not when the cache thread gets round to writing it: a
    # tick that waited in a backlog must not read as fresh.
    item = (time.time(), raw)
    try:
        q.put_nowait(item)
    except queue.Full:
        try:
            q.get_nowait()
            q.put_nowait(item)
            _dropped_ticks += 1
        except queue.Empty:
            pass


def register_tick_listener(cb: TickListener) -> None:
    _listeners.append(cb)


def unregister_tick_listener(cb: TickListener) -> None:
    try:
        _listeners.remove(cb)
    except ValueError:
        pass


def register_raw_tick_listener(cb: TickListener) -> None:
    _raw_listeners.append(cb)


def unregister_raw_tick_listener(cb: TickListener) -> None:
    try:
        _raw_listeners.remove(cb)
    except ValueError:
        pass


def register_order_notification_listener(cb: "Callable[[Any], None]") -> None:
    """Subscribe to parsed `OrderNotification` events. Idempotent per callback."""
    if cb not in _order_notification_listeners:
        _order_notification_listeners.append(cb)


def unregister_order_notification_listener(cb: "Callable[[Any], None]") -> None:
    try:
        _order_notification_listeners.remove(cb)
    except ValueError:
        pass


def _drain_loop(
    ingest_queue: "queue.Queue[tuple[float, Any]]",
    process_queue: "queue.Queue[RawBatch]",
    stop: threading.Event,
) -> None:
    """Coalesce ticks per contract and hand them to the cache thread in batches.

    The queues and the stop event are passed in, not read from the module globals, so a
    thread that outlives `stop_tick_pipeline()`'s join cannot pick up the next start's."""
    global _coalesce
    interval = _coalesce_seconds()
    while not stop.is_set():
        try:
            deadline = time.monotonic() + interval
            while time.monotonic() < deadline and not stop.is_set():
                try:
                    received_at, raw = ingest_queue.get(timeout=0.01)
                except queue.Empty:
                    continue
                if not isinstance(raw, dict):
                    continue
                storage_key = raw_tick_storage_key(raw)
                if storage_key is None:
                    continue
                with _coalesce_lock:
                    _coalesce[storage_key] = (received_at, dict(raw))
            if process_queue.full():
                # The cache thread is behind (Redis slow or down). Keep coalescing: the
                # latest tick per contract waits here and goes out in one batch later.
                continue
            batch: RawBatch = []
            with _coalesce_lock:
                if _coalesce:
                    batch = [(key, raw, ts) for key, (ts, raw) in _coalesce.items()]
                    _coalesce = {}
            if batch:
                try:
                    process_queue.put_nowait(batch)
                except queue.Full:
                    _logger.warning("WS tick process queue full; dropping batch of %s", len(batch))
        except Exception:  # noqa: BLE001 -- one bad tick must not end the thread
            _logger.exception("WS tick drain pass failed")


def _stage_snapshot_cell(
    raw: Any,
    out: list[tuple[str, str, str, Any, str, dict[str, Any]]],
    *,
    received_at: float | None = None,
) -> None:
    """Stage one coalesced tick for the durable last-known-good quote snapshot.

    Runs on the cache thread against already-coalesced batches, so this normalizes
    at most once per contract per `WS_TICK_COALESCE_MS` -- strictly cheaper than
    `_stage_pnl_quote`, which already parses every raw tick on the hotter ingest
    thread. Never raises: this thread also owns the raw quote cache the chain
    builder reads.
    """
    if not isinstance(raw, dict):
        return
    try:
        result = normalize_icici_tick(raw, updated_at=received_at)
        if result is None:
            return
        parsed, cell = result
        out.append(
            (
                parsed.exchange_code,
                parsed.stock_code,
                parsed.expiry_display,
                parsed.strike,
                parsed.right,
                cell,
            )
        )
    except Exception:
        _logger.debug("Snapshot staging failed for tick", exc_info=True)


def _record_snapshot_cells(
    entries: list[tuple[str, str, str, Any, str, dict[str, Any]]],
) -> None:
    if not entries:
        return
    try:
        from icici_breeze_backend.app.services.ws_quote_snapshot import record_cells

        record_cells(entries)
    except Exception:
        _logger.debug("Snapshot record failed", exc_info=True)


def _write_raw_batch(batch: RawBatch, ttl: int) -> RawBatch:
    """Write one batch of raw ticks and its dirty notice in a single Redis round trip.

    Returns the ticks written. Raises on a Redis error; the caller owns that.

    One pipeline, not a SET and a PUBLISH per contract: with the client's 2s socket
    timeout, a stalled Redis would otherwise hold this thread for 2s *per key*. Each key
    gets only the life its tick has left (the rule the chain builder applies to cells),
    and a tick already past that is not written at all.
    """
    now = time.time()
    written: RawBatch = []
    pipe = get_redis().pipeline(transaction=False)
    for storage_key, raw, received_at in batch:
        remaining = math.ceil(ttl - (now - received_at))
        if remaining <= 0:
            _cache_stats["stale_skipped"] += 1
            continue
        payload = json.dumps(
            {"received_at": received_at, "raw": raw}, separators=(",", ":"), default=str
        )
        pipe.set(storage_key, payload, ex=remaining)
        written.append((storage_key, raw, received_at))
    if written:
        # One notice per batch. The chain builder is the only subscriber and reads it as
        # "something changed", whatever the message says.
        pipe.publish(WS_TICK_DIRTY_CHANNEL, str(len(written)))
        pipe.execute()
    return written


def _note_cache_error(exc: BaseException, batch_size: int) -> None:
    now = time.monotonic()
    _cache_stats["errors"] += 1
    _cache_stats["errors_since_log"] += 1
    _cache_stats["last_error"] = f"{type(exc).__name__}: {exc}"
    _cache_stats["last_error_monotonic"] = now
    last_log = _cache_stats["last_error_log_monotonic"]
    if last_log is not None and (now - last_log) < _CACHE_ERROR_LOG_SECONDS:
        return
    _logger.warning(
        "WS raw tick cache write failed (%s failed batch(es) since the last report; this one "
        "held %s tick(s)). Live quotes are not reaching the chain builder until Redis answers.",
        _cache_stats["errors_since_log"],
        batch_size,
        exc_info=exc,
    )
    _cache_stats["last_error_log_monotonic"] = now
    _cache_stats["errors_since_log"] = 0


def _process_batch(batch: RawBatch, ttl: int) -> None:
    written = _write_raw_batch(batch, ttl)
    if _cache_stats["last_error_monotonic"] is not None and (
        _cache_stats["last_ok_monotonic"] is None
        or _cache_stats["last_ok_monotonic"] < _cache_stats["last_error_monotonic"]
    ):
        _logger.warning("WS raw tick cache writes are landing again")
    _cache_stats["last_ok_monotonic"] = time.monotonic()
    snapshot_entries: list[tuple[str, str, str, Any, str, dict[str, Any]]] = []
    for storage_key, raw, received_at in written:
        _stage_snapshot_cell(raw, snapshot_entries, received_at=received_at)
        for listener in list(_listeners):
            try:
                listener({"storage_key": storage_key, "raw": raw})
            except Exception:
                pass
    _record_snapshot_cells(snapshot_entries)


def _cache_loop(process_queue: "queue.Queue[RawBatch]", stop: threading.Event) -> None:
    ttl = int(getattr(cfg, "WS_RAW_QUOTE_TTL_SECONDS", 300) or 300)
    while not stop.is_set():
        try:
            batch = process_queue.get(timeout=0.1)
        except queue.Empty:
            continue
        try:
            _process_batch(batch, ttl)
        except Exception as exc:  # noqa: BLE001 -- a Redis error must not end the thread
            # The batch is dropped, not retried: raw ticks are last-value-wins and the
            # next one for each contract is already on its way.
            _note_cache_error(exc, len(batch))
            stop.wait(_CACHE_ERROR_BACKOFF_SECONDS)
        finally:
            process_queue.task_done()


def _start_threads_locked() -> None:
    """Start whichever of the two threads is not running. Caller holds `_start_lock`."""
    global _drain_thread, _cache_thread
    assert _ingest_queue is not None
    assert _process_queue is not None
    if _drain_thread is None or not _drain_thread.is_alive():
        _drain_thread = threading.Thread(
            target=_drain_loop,
            args=(_ingest_queue, _process_queue, _stop),
            name="ws-tick-drain",
            daemon=True,
        )
        _drain_thread.start()
    if _cache_thread is None or not _cache_thread.is_alive():
        _cache_thread = threading.Thread(
            target=_cache_loop,
            args=(_process_queue, _stop),
            name="ws-tick-cache",
            daemon=True,
        )
        _cache_thread.start()


def revive_tick_pipeline() -> bool:
    """Restart a pipeline thread that has died. Returns True when one was restarted.

    Does nothing on a pipeline that was never started or was stopped on purpose. Both
    loops catch their own errors, so a dead thread here means something got past that;
    `_started` used to stay True regardless, which left raw ticks unwritten until the
    process restarted. Called on every `start_tick_pipeline()` and every watchdog pass.
    """
    with _start_lock:
        if not _started:
            return False
        dead = [
            name
            for name, th in (("ws-tick-drain", _drain_thread), ("ws-tick-cache", _cache_thread))
            if th is None or not th.is_alive()
        ]
        if not dead:
            return False
        _start_threads_locked()
        _cache_stats["thread_restarts"] += 1
        _logger.error("WS tick pipeline thread(s) had died and were restarted: %s", ", ".join(dead))
        return True


def raw_cache_failing() -> bool:
    """True when the pipeline is running but its raw tick writes are not landing.

    For the price-feed watchdog, which judges silence from those same keys: when this is
    True the quiet is on our side of the socket, and re-subscribing at ICICI cannot help.
    """
    if not _started:
        return False
    for th in (_drain_thread, _cache_thread):
        if th is None or not th.is_alive():
            return True
    last_error = _cache_stats["last_error_monotonic"]
    if last_error is None:
        return False
    last_ok = _cache_stats["last_ok_monotonic"]
    return last_ok is None or last_ok < last_error


def start_tick_pipeline() -> None:
    global _ingest_queue, _process_queue, _drain_thread, _cache_thread, _started, _stop
    if _started:
        revive_tick_pipeline()
        return
    with _start_lock:
        if _started:
            return
        _stop = threading.Event()
        _ingest_queue = queue.Queue(maxsize=_ingest_qsize())
        _process_queue = queue.Queue(maxsize=_PROCESS_QUEUE_MAX_BATCHES)
        _drain_thread = None
        _cache_thread = None
        _cache_stats["last_error_monotonic"] = None
        _cache_stats["last_ok_monotonic"] = None
        _start_threads_locked()
        _started = True
        _logger.info("WS tick pipeline started")


def stop_tick_pipeline() -> None:
    global _ingest_queue, _process_queue, _drain_thread, _cache_thread, _started, _coalesce
    with _start_lock:
        if not _started:
            return
        _stop.set()
        for th in (_drain_thread, _cache_thread):
            if th is not None and th.is_alive():
                th.join(timeout=2.0)
        _ingest_queue = None
        _process_queue = None
        _drain_thread = None
        _cache_thread = None
        with _coalesce_lock:
            _coalesce = {}
        _started = False
        _logger.info("WS tick pipeline stopped")


def clear_retained_pnl_quotes() -> int:
    """Drop every retained WS quote. Returns the number of keys removed.

    Called at the session boundary (see `active_chains.maybe_daily_reset_active_chains`)
    so the previous session's last traded prices cannot be read by the new one. The
    TTL computed in `_pnl_quote_ttl_seconds` should already have expired them; this is
    the explicit half of the same guarantee, for an instance whose calendar moved
    under it or whose keys were written with an older, longer TTL.
    """
    removed = 0
    try:
        redis = get_redis()
        keys = list(redis.scan_iter(match=pnl_quote_key("*"), count=500))
        for start in range(0, len(keys), 500):
            batch = keys[start : start + 500]
            if batch:
                removed += int(redis.delete(*batch) or 0)
    except Exception:  # noqa: BLE001 — hygiene must never take down the caller
        _logger.warning("Could not clear retained pnl quotes", exc_info=True)
        return removed
    if removed:
        _logger.info("Cleared %s retained pnl quote(s) at the session boundary", removed)
    return removed


def pipeline_stats() -> dict[str, Any]:
    return {
        "started": _started,
        "drain_alive": bool(_drain_thread is not None and _drain_thread.is_alive()),
        "cache_alive": bool(_cache_thread is not None and _cache_thread.is_alive()),
        "cache_errors": _cache_stats["errors"],
        "last_cache_error": _cache_stats["last_error"],
        "last_cache_write_age_seconds": _age_since(_cache_stats["last_ok_monotonic"]),
        "stale_ticks_skipped": _cache_stats["stale_skipped"],
        "thread_restarts": _cache_stats["thread_restarts"],
        "last_ingest_age_seconds": last_ingest_age_seconds(),
        "dropped_ticks": _dropped_ticks,
        "ingest_qsize": _ingest_queue.qsize() if _ingest_queue else 0,
        "process_qsize": _process_queue.qsize() if _process_queue else 0,
        "pnl_buffer_staged": len(_pnl_quote_buffer),
        "pnl_flush": dict(_pnl_flush_stats),
        "last_tick_age_seconds": last_tick_age_seconds(),
    }


def _age_since(monotonic_ts: float | None) -> float | None:
    if monotonic_ts is None:
        return None
    return max(0.0, time.monotonic() - monotonic_ts)


def last_ingest_age_seconds() -> float | None:
    """Seconds since anything but an order event arrived from the socket, or None if
    nothing has since this process started. Independent of Redis and of parsing."""
    return _age_since(_last_ingest_monotonic)


def last_tick_monotonic() -> float | None:
    """Monotonic timestamp of the most recent WS tick seen by `ingest_tick`, or None."""
    return _last_tick_monotonic


def last_tick_age_seconds() -> float | None:
    if _last_tick_monotonic is None:
        return None
    return max(0.0, time.monotonic() - _last_tick_monotonic)


def _pnl_flush_interval_seconds() -> float:
    """Read the user-configurable flush interval (Settings → Advanced), fresh
    every call — this is what makes the interval live-adjustable without a
    process restart. Falls back to the env-configured default, clamped to the
    same hard bounds, if the settings table can't be read for any reason."""
    try:
        from icici_breeze_backend.app.services.pnl_engine_settings import load_pnl_engine_settings

        return float(load_pnl_engine_settings()["quote_flush_interval_seconds"])
    except Exception:
        _logger.debug("PNL quote flush interval settings lookup failed; using env default", exc_info=True)
        try:
            v = float(getattr(cfg, "PNL_QUOTE_FLUSH_INTERVAL_SECONDS", 2.0))
        except (TypeError, ValueError):
            v = 2.0
        return max(0.5, min(10.0, v))


# Expire retained quotes this long before the next session opens, so nothing from the
# previous session is still readable once a new one begins.
_QUOTE_RETENTION_MARGIN_SECONDS = 300
# Hard ceiling on a computed TTL, so a mis-set calendar (or a long holiday run) can't
# pin the whole quote keyspace in a memory-capped Redis indefinitely.
_QUOTE_RETENTION_MAX_SECONDS = 4 * 24 * 3600
# How long a resolved next-session-open may be reused before the calendar is re-read.
_NEXT_OPEN_CACHE_SECONDS = 60.0
# (resolved_at, next_session_open)
_next_open_cache: "tuple[datetime, datetime] | None" = None


def _pnl_quote_ttl_seconds() -> int:
    """Keep a quote until shortly before the next session opens.

    Previously a flat 30s, which made the cache useless the moment ticks stopped:
    every leg then revalued at its own entry price, so a whole book read as exactly
    zero P&L after the close and through any feed outage longer than half a minute.
    Retaining the last traded price fixes that and, because the expiry is tied to the
    next *session* rather than to a wall-clock hour, it also covers a session the
    exchange runs later than the configured close — ticks that keep arriving keep
    being recorded, and keep being the freshest thing we hold.

    Retention alone would not be safe: nothing re-subscribes the feed overnight, so a
    quote held "until a newer tick replaces it" could still be sitting there the next
    morning. Every consumer therefore judges the stored timestamp, not the key's mere
    presence, and this TTL is the second line under that.
    """
    configured = getattr(cfg, "PNL_QUOTE_TTL_SECONDS", 30) or 30
    try:
        floor = max(5, int(configured))
    except (TypeError, ValueError):
        floor = 30
    try:
        from icici_breeze_backend.app.core.timezone import IST

        now = datetime.now(IST)
        target = _cached_next_session_open(now)
        remaining = int((target - now).total_seconds() - _QUOTE_RETENTION_MARGIN_SECONDS)
        return max(floor, min(remaining, _QUOTE_RETENTION_MAX_SECONDS))
    except Exception:  # noqa: BLE001 — never let calendar trouble stop the flush
        _logger.debug("quote TTL: falling back to configured floor", exc_info=True)
        return floor


def _cached_next_session_open(now: "datetime") -> "datetime":
    """Memoized `next_session_open`, because the flush worker asks every couple of
    seconds and each miss is a SQLite read of the exchange calendar. Re-resolved once
    the cached answer is stale or has been passed, so an operator editing the session
    hours in Settings is picked up within the minute rather than at the next restart.
    """
    global _next_open_cache
    cached = _next_open_cache
    if (
        cached is not None
        and now < cached[1]
        and (now - cached[0]).total_seconds() < _NEXT_OPEN_CACHE_SECONDS
    ):
        return cached[1]

    from icici_breeze_backend.app.services.market_calendar import next_session_open

    target = next_session_open(now)
    _next_open_cache = (now, target)
    return target


def flush_pnl_quotes() -> int:
    """Worker 2 tick: drain the conflation buffer into a pipelined, non-transactional
    Redis hash write. Returns the number of contracts flushed (0 when the buffer
    was empty — the common case between windows with no fresh ticks)."""
    batch = _pnl_quote_buffer.drain()
    if not batch:
        return 0
    ttl = _pnl_quote_ttl_seconds()
    redis = get_redis()
    pipe = redis.pipeline(transaction=False)
    flushed = 0
    for scrip_key, fields in batch.items():
        mapping = {k: v for k, v in fields.items() if v is not None}
        if not mapping:
            continue
        key = pnl_quote_key(scrip_key)
        pipe.hset(key, mapping=mapping)
        pipe.expire(key, ttl)
        flushed += 1
    if flushed == 0:
        return 0
    try:
        pipe.execute()
    except Exception:
        _pnl_flush_stats["flush_errors"] += 1
        _logger.warning("PNL quote pipeline flush failed", exc_info=True)
        return 0
    _pnl_flush_stats["last_flush_at"] = time.time()
    _pnl_flush_stats["last_flush_count"] = flushed
    return flushed


async def run_pnl_quote_flush_loop() -> None:
    """Worker 2: pipelined hash flush on a user-configurable clock (Settings
    → Advanced), read fresh every iteration so changes apply within one
    cycle — no restart needed.

    Mirrors the `portal_deployment_heartbeat.run_heartbeat_loop` idiom: an
    infinite loop cancelled only via the FastAPI lifespan's `task.cancel()`,
    with `CancelledError` always re-raised and all other errors logged and
    swallowed so one bad flush never kills the task.
    """
    _logger.info("PNL quote flush loop started")
    while True:
        try:
            interval = await asyncio.to_thread(_pnl_flush_interval_seconds)
            await asyncio.sleep(interval)
            await asyncio.to_thread(flush_pnl_quotes)
        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.exception("PNL quote flush tick failed")
