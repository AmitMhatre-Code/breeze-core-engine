"""L2 market-depth feed for the index signal's heavyweight constituents.

Subscribes the top-5 depth room (`{prefix}2!{token}`: NSE cash `4.2!`, BSE cash `1.2!`) for each
tracked constituent and turns every depth tick into (sum bid qty, sum ask qty) for the engines.
Depth only -- the quote room is neither needed nor subscribed.

Same shape and hazards as `index_spot_feed`: tokens come from the SDK's own SecurityMaster via
`get_stock_token_value`, keyed by the ICICI ShortName -- identical on NSE and BSE for every
heavyweight, so one registry short name serves both exchanges. `subscribe_feeds` reports failure
by *returning* a string; the SDK's auth flag is a one-way latch; and `interval` is never passed,
because setting it flips BFO token resolution for the whole process (see
`bots/scalping/futures_feed`).

The listener runs on the SDK socket thread for every tick in the process, options included, so
it is a dict lookup for anything that is not ours and a few additions for what is, and it never
raises. Depth ticks are also short-circuited in `ws_tick_pipeline.ingest_tick` after the raw
listeners: they carry no price for the P&L buffer and no contract identity for the chain
pipeline, and ~20 busy books would otherwise take slots in the chain ingest queue, which drops
its oldest entry when full.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable, Iterable
from datetime import date, datetime
from typing import Any

from icici_breeze_backend.app.core.timezone import IST

if False:  # pragma: no cover - typing only
    from icici_breeze_backend.app.services.processor import processor as Processor

_logger = logging.getLogger(__name__)

# (exchange "NSE"/"BSE", ICICI ShortName, bid qty, ask qty, receive time)
BookListener = Callable[[str, str, float, float, float], None]
# (exchange, ShortName, best bid px, best bid qty, best ask px, best ask qty, receive time) --
# the level-1 view the order-flow challenger needs (`index_signal.flow`).
TopListener = Callable[[str, str, float, float, float, float, float], None]
_TOP_KEYS = ("BestBuyRate-1", "BestBuyQty-1", "BestSellRate-1", "BestSellQty-1")

_DEPTH_QTY_RE = re.compile(r"^Best(Buy|Sell)Qty-(\d+)$")
_DEFAULT_LEVELS = 5

_lock = threading.RLock()
# Serialises whole subscribe passes: the login prefetch and the signal loop can both ask at once.
_sync_lock = threading.Lock()
_book_listener: BookListener | None = None
_top_listener: TopListener | None = None
# Levels summed per side, pushed in by the publisher from Settings -> Index Signal.
_depth_levels = _DEFAULT_LEVELS
_symbol_to_target: dict[str, tuple[str, str]] = {}
_target_to_symbol: dict[tuple[str, str], str] = {}
# Targets ICICI accepted a depth subscribe for.
_subscribed: set[tuple[str, str]] = set()
_subscribed_date: date | None = None
_listener_registered = False
_last_tick_monotonic: float | None = None
_ticks_seen = 0
_last_error: str | None = None
_unresolved: set[tuple[str, str]] = set()


def set_depth_levels(levels: int) -> None:
    global _depth_levels
    _depth_levels = max(1, int(levels))


def _levels() -> int:
    return _depth_levels


def is_depth_payload(payload: Any) -> bool:
    """True for a parsed L2 depth tick. The SDK tags these `quotes: "Market Depth"`; the `.2!`
    room prefix is the fallback should a payload arrive without the tag."""
    if not isinstance(payload, dict):
        return False
    if payload.get("quotes") == "Market Depth":
        return True
    prefix, sep, _token = str(payload.get("symbol") or "").partition("!")
    return bool(sep) and prefix.endswith(".2") and "depth" in payload


def depth_sums(depth: Any, levels: int = _DEFAULT_LEVELS) -> tuple[float, float] | None:
    """(sum bid qty, sum ask qty) over the best `levels` levels of a parsed depth block.

    Keyed on the SDK's `BestBuyQty-{k}` / `BestSellQty-{k}` names, which both exchange layouts
    share (NSE rows add order counts and flags, BSE rows do not), rather than on field position,
    so it reads a list of per-level dicts and a single flattened dict alike. None when the block
    has no quantity fields at all -- a malformed tick, as opposed to an empty book, which comes
    back as (0, 0) and is excluded downstream.
    """
    rows = [depth] if isinstance(depth, dict) else depth
    if not isinstance(rows, list):
        return None
    bid = ask = 0.0
    found = False
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key, value in row.items():
            m = _DEPTH_QTY_RE.match(str(key))
            if m is None or int(m.group(2)) > levels:
                continue
            try:
                qty = float(value)
            except (TypeError, ValueError):
                continue
            found = True
            if qty <= 0:
                continue
            if m.group(1) == "Buy":
                bid += qty
            else:
                ask += qty
    return (bid, ask) if found else None


def depth_top(depth: Any) -> tuple[float, float, float, float] | None:
    """(best bid px, best bid qty, best ask px, best ask qty) from a parsed depth block, keyed
    on the `-1` field names both exchange layouts share. None when any of the four is missing."""
    rows = [depth] if isinstance(depth, dict) else depth
    if not isinstance(rows, list):
        return None
    found: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key in _TOP_KEYS:
            if key in row and key not in found:
                try:
                    found[key] = float(row[key])
                except (TypeError, ValueError):
                    return None
    if len(found) < len(_TOP_KEYS):
        return None
    return tuple(found[k] for k in _TOP_KEYS)  # type: ignore[return-value]


def set_book_listener(cb: BookListener | None) -> None:
    global _book_listener
    with _lock:
        _book_listener = cb


def set_top_listener(cb: TopListener | None) -> None:
    global _top_listener
    with _lock:
        _top_listener = cb


def _on_raw_tick(payload: Any) -> None:
    """Raw WS listener. Runs on the SDK callback thread -- must stay cheap and never raise."""
    global _last_tick_monotonic, _ticks_seen
    try:
        if not isinstance(payload, dict):
            return
        symbol = str(payload.get("symbol") or "").strip()
        with _lock:
            target = _symbol_to_target.get(symbol)
            cb = _book_listener
            top_cb = _top_listener
        if target is None:
            return
        sums = depth_sums(payload.get("depth"), _levels())
        if sums is None:
            return
        with _lock:
            _last_tick_monotonic = time.monotonic()
            _ticks_seen += 1
        now_ts = time.time()
        if cb is not None:
            cb(target[0], target[1], sums[0], sums[1], now_ts)
        if top_cb is not None:
            top = depth_top(payload.get("depth"))
            if top is not None:
                top_cb(target[0], target[1], top[0], top[1], top[2], top[3], now_ts)
    except Exception:  # noqa: BLE001
        _logger.debug("index depth feed: tick handling failed", exc_info=True)


def _register_listener_once() -> None:
    global _listener_registered
    from icici_breeze_backend.app.services.ws_tick_pipeline import register_raw_tick_listener

    with _lock:
        if _listener_registered:
            return
        register_raw_tick_listener(_on_raw_tick)
        _listener_registered = True


def _resolve_depth_symbol(sdk: Any, exchange: str, short_name: str) -> str | None:
    result = sdk.get_stock_token_value(
        exchange_code=exchange,
        stock_code=short_name,
        get_exchange_quotes=False,
        get_market_depth=True,
    )
    # The SDK returns (rather than raises) its own exceptions -- check the shape.
    if not isinstance(result, tuple) or len(result) != 2:
        return None
    depth_token = result[1]
    return str(depth_token) if depth_token else None


def _forget(target: tuple[str, str]) -> None:
    with _lock:
        _subscribed.discard(target)
        symbol = _target_to_symbol.pop(target, None)
        if symbol is not None:
            _symbol_to_target.pop(symbol, None)


def _unsubscribe(sdk: Any, exchange: str, short_name: str) -> None:
    try:
        sdk.unsubscribe_feeds(
            exchange_code=exchange,
            stock_code=short_name,
            get_exchange_quotes=False,
            get_market_depth=True,
        )
    except Exception:  # noqa: BLE001 -- best effort; a leftover room only costs its ticks
        _logger.debug("index depth feed: unsubscribe failed for %s/%s", exchange, short_name, exc_info=True)
    _forget((exchange, short_name))


def _normalise_targets(targets: Iterable[tuple[str, str]]) -> set[tuple[str, str]]:
    return {(str(ex).upper(), str(sn).upper()) for ex, sn in targets if ex and sn}


def is_synced(targets: Iterable[tuple[str, str]]) -> bool:
    """True when today's subscribe pass covered exactly `targets` and every one was accepted."""
    wanted = _normalise_targets(targets)
    with _lock:
        return _subscribed_date == datetime.now(IST).date() and _subscribed == wanted


def sync_depth_subscriptions(
    proc: "Processor",
    user_id: str,
    targets: Iterable[tuple[str, str]],
    *,
    force: bool = False,
) -> bool:
    """Make the depth subscriptions match `targets` ((exchange, ShortName) pairs).

    Idempotent per IST day for an unchanged target set. Names that left the basket are
    unsubscribed first, so a daily re-rank cannot grow the room count. Returns False when there
    is no live session or any target could not be resolved or subscribed -- the latch is only
    claimed for a pass that fully succeeded, so the caller retries rather than going cold.
    `force=True` re-issues every subscribe (the watchdog's re-arm)."""
    global _subscribed_date, _last_error
    wanted = _normalise_targets(targets)
    today = datetime.now(IST).date()
    with _lock:
        if not force and _subscribed_date == today and _subscribed == wanted:
            return True

    from icici_breeze_backend.app.services.breeze_websocket_manager import (
        _ensure_ws,
        _reset_stale_auth_latch,
        _subscribe_feeds_error,
    )

    with _sync_lock:
        sdk = _ensure_ws(proc, user_id)
        if sdk is None:
            with _lock:
                _last_error = "No live broker session; depth feed not subscribed."
            return False
        _reset_stale_auth_latch(sdk)
        _register_listener_once()
        # The SDK reads `sdk.interval` while resolving tokens and never initialises it.
        if not hasattr(sdk, "interval"):
            sdk.interval = ""

        with _lock:
            dropped = sorted(_subscribed - wanted)
        for exchange, short_name in dropped:
            _unsubscribe(sdk, exchange, short_name)

        failures: list[str] = []
        for exchange, short_name in sorted(wanted):
            target = (exchange, short_name)
            with _lock:
                symbol = _target_to_symbol.get(target)
                already = target in _subscribed
            try:
                if symbol is None:
                    symbol = _resolve_depth_symbol(sdk, exchange, short_name)
                    if symbol is None:
                        failures.append(f"{exchange}/{short_name}: no depth token")
                        with _lock:
                            _unresolved.add(target)
                        continue
                    with _lock:
                        _unresolved.discard(target)
                        _symbol_to_target[symbol] = target
                        _target_to_symbol[target] = symbol
                if already and not force:
                    continue
                err = _subscribe_feeds_error(
                    sdk.subscribe_feeds(
                        exchange_code=exchange,
                        stock_code=short_name,
                        get_exchange_quotes=False,
                        get_market_depth=True,
                    )
                )
                if err is not None:
                    failures.append(f"{exchange}/{short_name}: {err}")
                    with _lock:
                        _subscribed.discard(target)
                    continue
                with _lock:
                    _subscribed.add(target)
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{exchange}/{short_name}: {exc}")
                _logger.warning(
                    "index depth feed: subscribe failed for %s/%s", exchange, short_name, exc_info=True
                )

        if failures:
            with _lock:
                _last_error = "; ".join(failures[:5])
            _logger.warning("index depth feed: %s of %s targets failed: %s", len(failures), len(wanted), _last_error)
            return False
        with _lock:
            _subscribed_date = today
            _last_error = None
        return True


def unsubscribe_all() -> None:
    """Drop every depth room -- the signal was switched off in Settings.

    Uses the live socket when there is one. With none, the rooms went with the socket, so only the
    bookkeeping is cleared; a later switch-on resubscribes from scratch either way."""
    global _subscribed_date, _last_error
    from icici_breeze_backend.app.services.breeze_websocket_manager import (
        _ensure_ws,
        current_ws_user_id,
    )

    with _lock:
        targets = sorted(_subscribed | set(_target_to_symbol))
    sdk = None
    user_id = current_ws_user_id()
    if targets and user_id is not None:
        from icici_breeze_backend.app.services.processor import processor

        sdk = _ensure_ws(processor(), user_id)
    with _sync_lock:
        for exchange, short_name in targets:
            if sdk is not None:
                _unsubscribe(sdk, exchange, short_name)
            else:
                _forget((exchange, short_name))
        with _lock:
            _subscribed_date = None
            _unresolved.clear()
            _last_error = None


def has_subscriptions() -> bool:
    with _lock:
        return bool(_subscribed)


def last_tick_age_seconds() -> float | None:
    with _lock:
        last = _last_tick_monotonic
    return None if last is None else max(0.0, time.monotonic() - last)


def status() -> dict[str, Any]:
    with _lock:
        return {
            "subscribed": sorted(f"{ex}/{sn}" for ex, sn in _subscribed),
            "subscribed_date": _subscribed_date.isoformat() if _subscribed_date else None,
            "unresolved": sorted(f"{ex}/{sn}" for ex, sn in _unresolved),
            "depth_levels": _depth_levels,
            "ticks_seen": _ticks_seen,
            "last_tick_age_seconds": last_tick_age_seconds(),
            "last_error": _last_error,
        }


def reset_state_for_tests() -> None:
    global _book_listener, _top_listener, _subscribed_date, _listener_registered, _depth_levels
    global _last_tick_monotonic, _ticks_seen, _last_error
    with _lock:
        _book_listener = None
        _top_listener = None
        _depth_levels = _DEFAULT_LEVELS
        _symbol_to_target.clear()
        _target_to_symbol.clear()
        _subscribed.clear()
        _unresolved.clear()
        _subscribed_date = None
        _listener_registered = False
        _last_tick_monotonic = None
        _ticks_seen = 0
        _last_error = None
