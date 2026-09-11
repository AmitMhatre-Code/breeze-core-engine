"""The index signal's single source of truth: the engines, publication, and the loop driving them.

Runs in the API process, where WS ticks arrive (`depth_feed` listens on the SDK thread). Every
consumer -- the navbar, any other screen, the bots -- reads the published payload through
`index_signal.reader`, never an engine: the payload lives in Redis so the answer is the same in
every process, and it carries its own expiry so a stalled publisher reads as "unavailable"
rather than as a frozen verdict.

Cadence: the loop follows the user's P&L recompute interval (Settings -> Advanced), read fresh
every iteration exactly as `portfolio_pnl_engine.run_pnl_loop` does. The engines update on every
depth tick; the interval only decides how often that state is sampled, published and judged.

Tuning comes from Settings -> Index Signal (`index_signal.settings`), also read every iteration,
so every change -- including switching the signal off, which unsubscribes the depth feed --
applies within one loop. The loop itself always runs; "off" is a state it publishes, not an
absence of the loop.
"""
from __future__ import annotations

import asyncio
import logging
import math
import threading
import time
from typing import Any

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.db.redis_client import cache_get_json, cache_set_json
from icici_breeze_backend.app.services.index_signal import depth_feed, shadow_log, weights
from icici_breeze_backend.app.services.index_signal import settings as signal_settings
from icici_breeze_backend.app.services.index_signal.engine import IndexSignalEngine
from icici_breeze_backend.app.services.index_signal.settings import IndexSignalSettings
from icici_breeze_backend.app.services.reference_data.keys import index_signal_key, index_spot_key

if False:  # pragma: no cover - typing only
    from icici_breeze_backend.app.services.processor import processor as Processor

_logger = logging.getLogger(__name__)

LABELS: tuple[str, ...] = weights.LABELS
EXCHANGE_FOR_LABEL: dict[str, str] = {"nifty": "NSE", "sensex": "BSE"}

REASON_DISABLED = "disabled"

# A published payload stays valid for this many publish intervals (with a floor), so one slow
# tick does not blank the signal but a stopped loop does within seconds.
_VALIDITY_MULTIPLE = 3.0
_MIN_VALIDITY_SECONDS = 10.0
_WEIGHTS_CHECK_SECONDS = 60.0
_UNRESOLVED_RETRY_SECONDS = 60.0
_DEPTH_RETRY_SECONDS = 60.0
# Live index ticks refresh the cached spot far more often than this (see `_index_spot`).
_SPOT_MAX_AGE_SECONDS = 15.0

_lock = threading.RLock()
_engines: dict[str, IndexSignalEngine] = {}
_weights_meta: dict[str, dict[str, Any]] = {}
_applied_generation: int | None = None
_applied_top_n: int | None = None
_unresolved_pending = False
_last_apply_monotonic: float | None = None
_last_weights_check_monotonic: float | None = None
_last_depth_attempt_monotonic: float | None = None


def current_settings() -> IndexSignalSettings:
    return signal_settings.load_index_signal_settings()


def index_signal_enabled() -> bool:
    return current_settings().enabled


def _publish_interval_seconds() -> float:
    """The user's P&L recompute interval, read fresh (see module docstring)."""
    try:
        from icici_breeze_backend.app.services.pnl_engine_settings import load_pnl_engine_settings

        return float(load_pnl_engine_settings()["pnl_recompute_interval_seconds"])
    except Exception:  # noqa: BLE001
        _logger.debug("index signal: P&L interval lookup failed; using the default", exc_info=True)
        try:
            return max(1.0, min(30.0, float(getattr(cfg, "PNL_ENGINE_INTERVAL_SECONDS", 2.0))))
        except (TypeError, ValueError):
            return 2.0


def _engine(label: str) -> IndexSignalEngine:
    with _lock:
        eng = _engines.get(label)
        if eng is None:
            eng = IndexSignalEngine(
                label, EXCHANGE_FOR_LABEL[label], current_settings().signal_params()
            )
            _engines[label] = eng
        return eng


def _on_book(exchange: str, short_name: str, bid_qty: float, ask_qty: float, ts: float) -> None:
    """`depth_feed`'s book listener: NSE books drive NIFTY, BSE books drive SENSEX."""
    for label, ex in EXCHANGE_FOR_LABEL.items():
        if ex == exchange:
            _engine(label).on_book(short_name, bid_qty, ask_qty, ts)


def apply_runtime_settings(s: IndexSignalSettings) -> None:
    """Push the tuning that needs no re-subscribe into the running parts. A new tau restarts the
    smoother (see `IndexSignalEngine.set_params`); everything else applies from the next publish."""
    params = s.signal_params()
    for label in LABELS:
        _engine(label).set_params(params)
    depth_feed.set_depth_levels(s.depth_levels)
    shadow_log.set_retention_days(s.shadow_retention_days)


def apply_weights_if_needed(*, force: bool = False, top_n: int | None = None) -> bool:
    """Push the current weights into the engines when they changed, the tracked count changed,
    or names were still unresolved a minute ago. Returns True when any engine's set of tracked
    names changed -- i.e. the depth subscriptions need to follow."""
    global _applied_generation, _applied_top_n, _unresolved_pending, _last_apply_monotonic
    top_n = current_settings().top_n if top_n is None else top_n
    generation = weights.weights_generation()
    now_m = time.monotonic()
    with _lock:
        due = (
            force
            or _applied_generation != generation
            or _applied_top_n != top_n
            or (
                _unresolved_pending
                and (_last_apply_monotonic is None or now_m - _last_apply_monotonic >= _UNRESOLVED_RETRY_SECONDS)
            )
        )
        if not due:
            return False
        _last_apply_monotonic = now_m

    changed = False
    unresolved = False
    for label in LABELS:
        constituents, meta = weights.tracked_constituents(label, top_n)
        if _engine(label).set_constituents(constituents):
            changed = True
        # A short basket means the registry could not resolve names it should have, most often
        # because it is still cold after a boot -- worth another try shortly.
        unresolved = unresolved or len(constituents) < top_n
        meta["tracked"] = len(constituents)
        with _lock:
            _weights_meta[label] = meta
    with _lock:
        _applied_generation = generation
        _applied_top_n = top_n
        _unresolved_pending = unresolved
    return changed


def depth_targets() -> list[tuple[str, str]]:
    return [
        (EXCHANGE_FOR_LABEL[label], short_name)
        for label in LABELS
        for short_name in sorted(_engine(label).tracked_short_names())
    ]


def ensure_depth_feed(proc: "Processor", user_id: str, *, force: bool = False) -> bool:
    """Subscribe the depth rooms the current baskets need. True when the signal is switched off
    (nothing owed); False when there is nothing to subscribe yet or any subscribe failed."""
    if not index_signal_enabled():
        return True
    apply_weights_if_needed()
    depth_feed.set_book_listener(_on_book)
    targets = depth_targets()
    if not targets:
        return False
    return depth_feed.sync_depth_subscriptions(proc, user_id, targets, force=force)


def _index_spot(label: str, now: float | None = None) -> float | None:
    """The index level for the shadow log, or None when the cached one is not a live tick. Out of
    hours the navbar caches a REST close with no expiry, and it stays until the first live tick --
    so at the open a slow index feed would otherwise log yesterday's close as the current level."""
    payload = cache_get_json(index_spot_key(label))
    if not isinstance(payload, dict):
        return None
    try:
        ltp = float(payload.get("ltp"))
        updated_at = float(payload.get("updated_at"))
    except (TypeError, ValueError):
        return None
    ts = time.time() if now is None else now
    if ltp <= 0 or ts - updated_at > _SPOT_MAX_AGE_SECONDS:
        return None
    return ltp


def _validity_seconds(interval: float) -> float:
    return max(_MIN_VALIDITY_SECONDS, _VALIDITY_MULTIPLE * interval)


def _write_payload(label: str, payload: dict[str, Any], valid_for: float) -> None:
    try:
        cache_set_json(index_signal_key(label), payload, ex=int(math.ceil(valid_for)) + 5)
    except Exception:  # noqa: BLE001
        _logger.warning("index signal: publish to Redis failed for %s", label, exc_info=True)


def publish_once(
    *,
    now: float | None = None,
    interval: float | None = None,
    session_open: bool | None = None,
) -> dict[str, dict[str, Any]]:
    """Snapshot every engine, publish to Redis, and feed the shadow log."""
    ts = time.time() if now is None else now
    interval = _publish_interval_seconds() if interval is None else interval
    if session_open is None:
        from icici_breeze_backend.app.services.market_calendar import is_market_open

        session_open = is_market_open()
    valid_for = _validity_seconds(interval)
    out: dict[str, dict[str, Any]] = {}
    for label in LABELS:
        payload = _engine(label).snapshot(ts, session_open=session_open)
        with _lock:
            payload["weights"] = dict(_weights_meta.get(label) or {})
        payload["published_at"] = ts
        payload["valid_until"] = ts + valid_for
        payload["publish_interval_seconds"] = interval
        _write_payload(label, payload, valid_for)
        try:
            shadow_log.record(label, payload, spot=_index_spot(label, ts), now=ts)
        except Exception:  # noqa: BLE001 -- evidence is best-effort; never block publication
            _logger.debug("index signal: shadow log write failed for %s", label, exc_info=True)
        out[label] = payload
    return out


def publish_disabled(*, now: float | None = None, interval: float | None = None) -> dict[str, dict[str, Any]]:
    """What readers see while the signal is switched off: an explicit, still-expiring
    `unavailable / disabled`, so the navbar can hide the chip rather than show "no reading"."""
    ts = time.time() if now is None else now
    interval = _publish_interval_seconds() if interval is None else interval
    valid_for = _validity_seconds(interval)
    out: dict[str, dict[str, Any]] = {}
    for label in LABELS:
        payload: dict[str, Any] = {
            "label": label,
            "exchange": EXCHANGE_FOR_LABEL[label],
            "state": "unavailable",
            "reason": REASON_DISABLED,
            "signal": None,
            "raw_wobi": None,
            "coverage": None,
            "constituents": [],
            "weights": {},
            "computed_at": ts,
            "published_at": ts,
            "valid_until": ts + valid_for,
            "publish_interval_seconds": interval,
        }
        _write_payload(label, payload, valid_for)
        out[label] = payload
    return out


def _switch_off() -> None:
    """Drop the depth rooms. Clearing the retry clock means switching back on resubscribes on
    the very next loop rather than after the retry throttle."""
    global _last_depth_attempt_monotonic
    with _lock:
        _last_depth_attempt_monotonic = None
    if not depth_feed.has_subscriptions():
        return
    try:
        depth_feed.unsubscribe_all()
        _logger.info("index signal switched off; depth feed unsubscribed")
    except Exception:  # noqa: BLE001
        _logger.warning("index signal: unsubscribing the depth feed failed", exc_info=True)


def _maybe_sync_depth_feed(targets_changed: bool, now_m: float) -> None:
    """Keep the subscriptions following the baskets, and retry a pass that did not complete.
    The login prefetch normally subscribes first; this is what heals a boot with a cold registry,
    a subscribe ICICI refused, or the signal being switched back on."""
    global _last_depth_attempt_monotonic
    from icici_breeze_backend.app.services.breeze_websocket_manager import current_ws_user_id
    from icici_breeze_backend.app.services.market_calendar import is_trading_day

    user_id = current_ws_user_id()
    if user_id is None or not is_trading_day():
        return
    targets = depth_targets()
    if not targets:
        return
    if not targets_changed:
        if depth_feed.is_synced(targets):
            return
        with _lock:
            last = _last_depth_attempt_monotonic
        if last is not None and now_m - last < _DEPTH_RETRY_SECONDS:
            return
    with _lock:
        _last_depth_attempt_monotonic = now_m
    from icici_breeze_backend.app.services.processor import processor

    ensure_depth_feed(processor(), user_id)


def _loop_tick(interval: float) -> None:
    global _last_weights_check_monotonic
    s = current_settings()
    if not s.enabled:
        _switch_off()
        publish_disabled(interval=interval)
        return

    apply_runtime_settings(s)
    now_m = time.monotonic()
    with _lock:
        check_weights = (
            _last_weights_check_monotonic is None
            or now_m - _last_weights_check_monotonic >= _WEIGHTS_CHECK_SECONDS
        )
        if check_weights:
            _last_weights_check_monotonic = now_m
    if check_weights:
        try:
            weights.refresh_due_weights_in_background()
        except Exception:  # noqa: BLE001
            _logger.warning("index signal: weights refresh check failed", exc_info=True)
    try:
        changed = apply_weights_if_needed(top_n=s.top_n)
        _maybe_sync_depth_feed(changed, now_m)
    except Exception:  # noqa: BLE001 -- a subscription problem must not stop publication
        _logger.warning("index signal: basket/subscription upkeep failed", exc_info=True)
    publish_once(interval=interval)


async def run_index_signal_loop() -> None:
    """Cancelled only via the FastAPI lifespan's `task.cancel()`; one bad tick never kills it
    (the `run_pnl_loop` idiom)."""
    _logger.info("Index signal loop started")
    depth_feed.set_book_listener(_on_book)
    while True:
        try:
            interval = await asyncio.to_thread(_publish_interval_seconds)
            await asyncio.sleep(interval)
            await asyncio.to_thread(_loop_tick, interval)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            _logger.exception("Index signal tick failed")


def status() -> dict[str, Any]:
    with _lock:
        meta = {label: dict(m) for label, m in _weights_meta.items()}
    return {
        "settings": current_settings().to_dict(),
        "weights": meta,
        "weights_refresh": weights.refresh_status(),
        "depth_feed": depth_feed.status(),
    }


def reset_state_for_tests() -> None:
    global _applied_generation, _applied_top_n, _unresolved_pending, _last_apply_monotonic
    global _last_weights_check_monotonic, _last_depth_attempt_monotonic
    with _lock:
        _engines.clear()
        _weights_meta.clear()
        _applied_generation = None
        _applied_top_n = None
        _unresolved_pending = False
        _last_apply_monotonic = None
        _last_weights_check_monotonic = None
        _last_depth_attempt_monotonic = None
