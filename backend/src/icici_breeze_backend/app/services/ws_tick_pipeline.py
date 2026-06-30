"""Two-worker pipeline: fast SDK callback drain + raw tick Redis cache."""
from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from typing import Any

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.db.redis_client import cache_publish, cache_set_json
from icici_breeze_backend.app.services.reference_data.keys import (
    WS_TICK_DIRTY_CHANNEL,
    ws_raw_quote_key,
)
from icici_breeze_backend.app.services.reference_data.ws_token_index import (
    exchange_from_ws_prefix,
    parse_ws_symbol,
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
_dropped_ticks = 0
_started = False
_start_lock = threading.Lock()


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


def ingest_tick(raw: Any) -> None:
    """Called from SDK on_ticks — notify raw listeners, then enqueue for raw cache pipeline."""
    global _dropped_ticks
    payload = _raw_tick_payload(raw)
    for listener in list(_raw_listeners):
        try:
            listener(payload)
        except Exception:
            pass
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


def _cache_loop() -> None:
    assert _process_queue is not None
    ttl = int(getattr(cfg, "WS_RAW_QUOTE_TTL_SECONDS", 300) or 300)
    while not _stop.is_set():
        try:
            batch = _process_queue.get(timeout=0.1)
        except queue.Empty:
            continue
        dirty_keys: list[str] = []
        for storage_key, raw in batch:
            payload = {
                "received_at": time.time(),
                "raw": raw,
            }
            cache_set_json(storage_key, payload, ex=ttl)
            dirty_keys.append(storage_key)
            for listener in list(_listeners):
                try:
                    listener({"storage_key": storage_key, "raw": raw})
                except Exception:
                    pass
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
    }
