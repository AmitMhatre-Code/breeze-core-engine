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
import logging
import queue
import threading
import time
from collections.abc import Callable
from typing import Any

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.db.redis_client import cache_publish, cache_set_json, get_redis
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

_ingest_queue: queue.Queue[Any] | None = None
_coalesce: dict[str, dict[str, Any]] = {}
_coalesce_lock = threading.Lock()
_stop = threading.Event()
_drain_thread: threading.Thread | None = None
_cache_thread: threading.Thread | None = None
_process_queue: queue.Queue[list[tuple[str, dict[str, Any]]]] | None = None
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
    _pnl_quote_buffer.update(scrip_key, ltp=ltp, bid=bid, ask=ask, ts=time.time())
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
    global _dropped_ticks
    payload = _raw_tick_payload(raw)

    if _route_order_notification(payload):
        return

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
    try:
        q.put_nowait(raw)
    except queue.Full:
        try:
            q.get_nowait()
            q.put_nowait(raw)
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


def _drain_loop() -> None:
    global _coalesce
    assert _ingest_queue is not None
    assert _process_queue is not None
    interval = _coalesce_seconds()
    while not _stop.is_set():
        deadline = time.monotonic() + interval
        while time.monotonic() < deadline and not _stop.is_set():
            try:
                raw = _ingest_queue.get(timeout=0.01)
            except queue.Empty:
                continue
            if not isinstance(raw, dict):
                continue
            storage_key = raw_tick_storage_key(raw)
            if storage_key is None:
                continue
            with _coalesce_lock:
                _coalesce[storage_key] = dict(raw)
        batch: list[tuple[str, dict[str, Any]]] = []
        with _coalesce_lock:
            if _coalesce:
                batch = list(_coalesce.items())
                _coalesce = {}
        if batch:
            try:
                _process_queue.put_nowait(batch)
            except queue.Full:
                _logger.warning("WS tick process queue full; dropping batch of %s", len(batch))


def _stage_snapshot_cell(
    raw: Any,
    out: list[tuple[str, str, str, Any, str, dict[str, Any]]],
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
        result = normalize_icici_tick(raw)
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


def _cache_loop() -> None:
    assert _process_queue is not None
    ttl = int(getattr(cfg, "WS_RAW_QUOTE_TTL_SECONDS", 300) or 300)
    while not _stop.is_set():
        try:
            batch = _process_queue.get(timeout=0.1)
        except queue.Empty:
            continue
        dirty_keys: list[str] = []
        snapshot_entries: list[tuple[str, str, str, Any, str, dict[str, Any]]] = []
        for storage_key, raw in batch:
            payload = {
                "received_at": time.time(),
                "raw": raw,
            }
            cache_set_json(storage_key, payload, ex=ttl)
            dirty_keys.append(storage_key)
            _stage_snapshot_cell(raw, snapshot_entries)
            for listener in list(_listeners):
                try:
                    listener({"storage_key": storage_key, "raw": raw})
                except Exception:
                    pass
        _record_snapshot_cells(snapshot_entries)
        for storage_key in dirty_keys:
            cache_publish(WS_TICK_DIRTY_CHANNEL, storage_key)
        _process_queue.task_done()


def start_tick_pipeline() -> None:
    global _ingest_queue, _process_queue, _drain_thread, _cache_thread, _started, _stop
    with _start_lock:
        if _started:
            return
        _stop = threading.Event()
        _ingest_queue = queue.Queue(maxsize=_ingest_qsize())
        _process_queue = queue.Queue(maxsize=256)
        _drain_thread = threading.Thread(target=_drain_loop, name="ws-tick-drain", daemon=True)
        _cache_thread = threading.Thread(target=_cache_loop, name="ws-tick-cache", daemon=True)
        _drain_thread.start()
        _cache_thread.start()
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


def pipeline_stats() -> dict[str, Any]:
    return {
        "started": _started,
        "dropped_ticks": _dropped_ticks,
        "ingest_qsize": _ingest_queue.qsize() if _ingest_queue else 0,
        "process_qsize": _process_queue.qsize() if _process_queue else 0,
        "pnl_buffer_staged": len(_pnl_quote_buffer),
        "pnl_flush": dict(_pnl_flush_stats),
        "last_tick_age_seconds": last_tick_age_seconds(),
    }


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


def _pnl_quote_ttl_seconds() -> int:
    try:
        return max(5, int(getattr(cfg, "PNL_QUOTE_TTL_SECONDS", 30) or 30))
    except (TypeError, ValueError):
        return 30


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
