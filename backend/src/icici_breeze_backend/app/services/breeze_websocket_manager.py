"""Multiplexed Breeze WebSocket for option chain exchange quotes."""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, TYPE_CHECKING

from icici_breeze_backend.app.domain.breeze_api_tester_catalog import (
    is_breeze_invoke_response_ok,
)
import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.strike import Strike, parse_strike, strike_for_broker, strike_key
from icici_breeze_backend.app.db.redis_client import cache_get_json
from icici_breeze_backend.app.services.reference_data.keys import ws_quote_key
from icici_breeze_backend.app.services.ws_tick_pipeline import (
    ingest_tick,
    register_tick_listener,
    start_tick_pipeline,
    stop_tick_pipeline,
    unregister_tick_listener,
)

if TYPE_CHECKING:
    from icici_breeze_backend.app.services.processor import processor as Processor

_logger = logging.getLogger(__name__)
_lock = threading.RLock()

# holder_id -> set of subscription keys
_holders: dict[str, set[str]] = {}
# subscription_key -> set of holder_ids
_sub_holders: dict[str, set[str]] = {}
# subscription_key -> ICICI unsubscribe_feeds kwargs
_sub_meta: dict[str, dict[str, Any]] = {}

_sdk: Any = None
_sdk_user_id: str | None = None
_connected = False
_last_error: str | None = None
_playground_listeners: list[Any] = []
_PLAYGROUND_EVENT_LIMIT = 50
_playground_events: list[dict[str, Any]] = []
_playground_event_seq = 0
_UNTRACKED_HOLDER = "__untracked__"


def _note_error(message: str, *args: Any) -> str:
    global _last_error
    text = message % args if args else message
    _last_error = text
    _logger.warning(text)
    return text


def _clear_error() -> None:
    global _last_error
    _last_error = None


def _effective_holder(holder_id: str | None) -> str:
    h = str(holder_id or "").strip()
    return h if h else _UNTRACKED_HOLDER


def get_playground_status() -> dict[str, Any]:
    with _lock:
        return {
            "connected": _connected,
            "user_id": _sdk_user_id,
            "active_subscriptions": len(_sub_holders),
            "subscription_keys": sorted(_sub_holders.keys()),
            "active_holders": len(_holders),
            "last_error": _last_error,
        }


def _icici_command(sdk_method: str, sdk_args: dict[str, Any], *, side_effects: list[str] | None = None) -> dict[str, Any]:
    return {
        "sdk_method": sdk_method,
        "sdk_args": sdk_args,
        "side_effects": side_effects or [],
    }


def _record_playground_event(
    step: str,
    sdk_method: str,
    sdk_args: dict[str, Any],
    icici_response: Any,
    ok: bool,
    *,
    note: str | None = None,
    side_effects: list[str] | None = None,
) -> dict[str, Any]:
    global _playground_event_seq
    with _lock:
        _playground_event_seq += 1
        entry: dict[str, Any] = {
            "id": _playground_event_seq,
            "ts": time.time(),
            "step": step,
            "icici_command": _icici_command(sdk_method, sdk_args, side_effects=side_effects),
            "icici_response": icici_response,
            "ok": ok,
        }
        if note:
            entry["note"] = note
        _playground_events.append(entry)
        if len(_playground_events) > _PLAYGROUND_EVENT_LIMIT:
            _playground_events.pop(0)
        return dict(entry)


def get_playground_event_log() -> list[dict[str, Any]]:
    with _lock:
        return list(reversed(_playground_events))


def record_playground_stream_open(user_id: str) -> dict[str, Any]:
    return _record_playground_event(
        "sse_stream_open",
        "",
        {},
        None,
        True,
        note=f"SSE tick listener registered for user_id={user_id}",
    )


def sub_key(
    exchange_code: str,
    stock_code: str,
    expiry_display: str,
    strike: Strike,
    right: str,
) -> str:
    r = str(right or "").strip().lower()
    if r in {"call", cfg.CALL.lower()}:
        r = "call"
    else:
        r = "put"
    return f"{exchange_code.upper()}|{stock_code.upper()}|{expiry_display}|{strike_key(strike)}|{r}"


_sub_key = sub_key  # backward compat for tests


def _subscribe_feeds_sdk_args(
    exchange_code: str,
    stock_code: str,
    expiry_date: str,
    strike_price: str,
    right: str,
) -> dict[str, Any]:
    return {
        "exchange_code": exchange_code,
        "stock_code": stock_code,
        "expiry_date": expiry_date,
        "strike_price": strike_price,
        "right": right,
        "product_type": "options",
        "get_market_depth": False,
        "get_exchange_quotes": True,
    }


def _on_ticks(ticks: Any) -> None:
    ingest_tick(ticks)


def _attach_sdk_ticks_handler(sdk: Any) -> None:
    sdk.on_ticks = _on_ticks
    start_tick_pipeline()


def _ensure_ws(proc: "Processor", user_id: str) -> Any | None:
    global _sdk, _sdk_user_id, _connected
    with _lock:
        if _sdk is not None and _sdk_user_id == user_id and _connected:
            return _sdk
        sdk = proc.get_session_breeze(user_id)
        if sdk is None:
            _note_error("WebSocket connect: no broker session for user_id=%s", user_id)
            return None
        try:
            sdk.ws_connect()
            _attach_sdk_ticks_handler(sdk)
            _sdk = sdk
            _sdk_user_id = user_id
            _connected = True
            _clear_error()
            _logger.info("WebSocket connected for user_id=%s", user_id)
            return sdk
        except Exception as exc:
            _connected = False
            _note_error("WebSocket connect failed for user_id=%s: %s", user_id, exc)
            return None


def _icici_unsubscribe(key: str, meta: dict[str, Any]) -> bool:
    sdk = _sdk
    if sdk is None:
        return False
    try:
        if hasattr(sdk, "unsubscribe_feeds"):
            sdk.unsubscribe_feeds(**meta)
        else:
            _logger.warning("SDK missing unsubscribe_feeds; skipping %s", key)
        _logger.info("unsubscribe_feeds ok key=%s", key)
        return True
    except Exception as exc:
        _note_error("unsubscribe_feeds failed %s: %s", key, exc)
        return False


def _detach_holder_from_key(holder_id: str, key: str) -> None:
    meta: dict[str, Any] | None = None
    should_unsub = False
    with _lock:
        holder_keys = _holders.get(holder_id)
        if holder_keys is not None:
            holder_keys.discard(key)
            if not holder_keys:
                _holders.pop(holder_id, None)
        holders = _sub_holders.get(key)
        if holders is None:
            return
        holders.discard(holder_id)
        if holders:
            return
        should_unsub = True
        meta = _sub_meta.pop(key, None)
        _sub_holders.pop(key, None)
    if should_unsub and meta:
        _icici_unsubscribe(key, meta)


def release_holder(holder_id: str) -> dict[str, Any]:
    """Release all subscriptions owned by holder_id; keep WebSocket connected."""
    hid = str(holder_id or "").strip()
    if not hid or hid == _UNTRACKED_HOLDER:
        return {"released": 0, "holder_id": hid}
    with _lock:
        keys = list(_holders.pop(hid, set()))
    for key in keys:
        _detach_holder_from_key(hid, key)
    return {"released": len(keys), "holder_id": hid}


def subscribe_option(
    proc: "Processor",
    user_id: str,
    exchange_code: str,
    stock_code: str,
    expiry_display: str,
    strike: Strike,
    right: str,
    *,
    holder_id: str | None = None,
) -> bool:
    hid = _effective_holder(holder_id)
    key = sub_key(exchange_code, stock_code, expiry_display, strike, right)
    with _lock:
        if key in _holders.get(hid, set()):
            return True
        is_new_icici_sub = key not in _sub_holders or len(_sub_holders[key]) == 0
    if not is_new_icici_sub:
        with _lock:
            _holders.setdefault(hid, set()).add(key)
            _sub_holders.setdefault(key, set()).add(hid)
        return True
    sdk = _ensure_ws(proc, user_id)
    if sdk is None:
        return False
    r = str(right or "").strip().lower()
    if r == cfg.CALL.lower():
        r = "call"
    else:
        r = "put"
    sdk_args = _subscribe_feeds_sdk_args(
        exchange_code,
        stock_code,
        expiry_display,
        strike_for_broker(strike),
        r,
    )
    try:
        result = sdk.subscribe_feeds(**sdk_args)
        time.sleep(0.02)
        if isinstance(result, dict):
            st = result.get("Status") or result.get("status")
            if st not in (200, None):
                err = (
                    result.get("Error")
                    or result.get("error")
                    or result.get("message")
                    or str(result)
                )
                _note_error("subscribe_feeds ICICI error %s: %s", key, err)
                return False
        with _lock:
            _holders.setdefault(hid, set()).add(key)
            _sub_holders.setdefault(key, set()).add(hid)
            _sub_meta[key] = dict(sdk_args)
        _logger.info(
            "subscribe_feeds ok user_id=%s holder=%s key=%s exchange=%s stock=%s expiry=%s strike=%s right=%s",
            user_id,
            hid,
            key,
            exchange_code,
            stock_code,
            expiry_display,
            strike,
            r,
        )
        return True
    except Exception as exc:
        _note_error("subscribe_feeds failed %s: %s", key, exc)
        return False


def sync_holder_chain_subscriptions(
    proc: "Processor",
    user_id: str,
    holder_id: str,
    stock_code: str,
    exchange_code: str,
    expiry_display: str,
    strikes: list[Strike],
) -> None:
    hid = _effective_holder(holder_id)
    desired: set[str] = set()
    for strike in strikes:
        for right in ("call", "put"):
            desired.add(sub_key(exchange_code, stock_code, expiry_display, strike, right))
    with _lock:
        current = set(_holders.get(hid, set()))
    for key in current - desired:
        _detach_holder_from_key(hid, key)
    for strike in strikes:
        for right in ("call", "put"):
            subscribe_option(
                proc,
                user_id,
                exchange_code,
                stock_code,
                expiry_display,
                strike,
                right,
                holder_id=hid,
            )


def ensure_chain_subscriptions(
    proc: "Processor",
    user_id: str,
    stock_code: str,
    exchange_code: str,
    expiry_display: str,
    strikes: list[Strike],
    *,
    lot_size: int = 0,
    freeze_quantity: int | None = None,
    holder_id: str | None = None,
) -> dict[str, Any] | None:
    if not strikes:
        return None
    sync_holder_chain_subscriptions(
        proc, user_id, _effective_holder(holder_id), stock_code, exchange_code, expiry_display, strikes
    )

    calls: list[dict[str, Any]] = []
    puts: list[dict[str, Any]] = []
    spot_price = None
    for strike in strikes:
        for right, bucket in (("call", calls), ("put", puts)):
            key = ws_quote_key(exchange_code, stock_code, expiry_display, strike, right)
            cell = cache_get_json(key)
            if not cell:
                continue
            if lot_size:
                cell["lot_size"] = lot_size
            bucket.append(cell)
            sp = cell.get("spot_price")
            if spot_price is None and sp:
                try:
                    spot_price = float(sp)
                except (TypeError, ValueError):
                    pass

    if not calls and not puts:
        return None

    call_by = {parse_strike(r["strike_price"]): r for r in calls if parse_strike(r.get("strike_price")) is not None}
    put_by = {parse_strike(r["strike_price"]): r for r in puts if parse_strike(r.get("strike_price")) is not None}
    chain_strikes = sorted(set(strikes) | set(call_by) | set(put_by))
    chain_rows = [
        {"strike_price": k, "call": call_by.get(k), "put": put_by.get(k)}
        for k in chain_strikes
    ]
    max_call_oi = max((r.get("open_interest", 0) for r in calls), default=0)
    max_put_oi = max((r.get("open_interest", 0) for r in puts), default=0)
    atm_strike = None
    if spot_price is not None and chain_strikes:
        atm_strike = min(chain_strikes, key=lambda s: abs(s - spot_price))

    return {
        "chain_rows": chain_rows,
        "max_call_oi": max_call_oi,
        "max_put_oi": max_put_oi,
        "expiry_display": expiry_display,
        "stock_code": stock_code,
        "exchange_code": exchange_code,
        "spot_price": spot_price,
        "atm_strike": atm_strike,
        "lot_size": lot_size or None,
        "freeze_quantity": freeze_quantity,
        "quote_source": "websocket",
        "bhavcopy_date": None,
    }


def _playground_response(
    response: Any,
    ok: bool | None = None,
    *,
    icici_command: dict[str, Any] | None = None,
    event_id: int | None = None,
) -> dict[str, Any]:
    if ok is None:
        ok = is_breeze_invoke_response_ok(response) if response is not None else True
    out: dict[str, Any] = {
        "ok": ok,
        "response": response,
        **get_playground_status(),
    }
    if icici_command is not None:
        out["icici_command"] = icici_command
    if event_id is not None:
        out["event_id"] = event_id
    return out


def _register_playground_connected(sdk: Any, user_id: str) -> None:
    global _sdk, _sdk_user_id, _connected
    with _lock:
        _sdk = sdk
        _sdk_user_id = user_id
        _connected = True
        _clear_error()


def _ensure_playground_ws(proc: "Processor", user_id: str) -> Any | None:
    global _sdk, _sdk_user_id, _connected
    with _lock:
        if _sdk is not None and _sdk_user_id == user_id and _connected:
            return _sdk
    sdk = proc.get_session_breeze(user_id)
    if sdk is None:
        return None
    sdk.ws_connect()
    _attach_sdk_ticks_handler(sdk)
    _register_playground_connected(sdk, user_id)
    _logger.info("WebSocket connected for user_id=%s (playground)", user_id)
    return sdk


def ws_connect_playground(proc: "Processor", user_id: str) -> dict[str, Any]:
    cmd = _icici_command("ws_connect", {}, side_effects=["sdk.on_ticks = ingest_tick"])
    if proc.get_session_breeze(user_id) is None:
        entry = _record_playground_event(
            "ws_connect", "ws_connect", {}, None, False, note="no broker session", side_effects=cmd["side_effects"]
        )
        return _playground_response(None, ok=False, icici_command=cmd, event_id=entry["id"])
    try:
        _ensure_playground_ws(proc, user_id)
        entry = _record_playground_event(
            "ws_connect", "ws_connect", {}, None, True, side_effects=cmd["side_effects"]
        )
        return _playground_response(None, ok=True, icici_command=cmd, event_id=entry["id"])
    except Exception as exc:
        with _lock:
            _connected = False
        entry = _record_playground_event(
            "ws_connect", "ws_connect", {}, str(exc), False, side_effects=cmd["side_effects"]
        )
        return _playground_response(str(exc), ok=False, icici_command=cmd, event_id=entry["id"])


def _unsubscribe_all_feeds() -> None:
    with _lock:
        keys_meta = list(_sub_meta.items())
        _holders.clear()
        _sub_holders.clear()
        _sub_meta.clear()
    for key, meta in keys_meta:
        _icici_unsubscribe(key, meta)


def ws_disconnect_playground() -> dict[str, Any]:
    global _sdk, _sdk_user_id, _connected
    cmd = _icici_command("ws_disconnect", {})
    err: str | None = None
    _unsubscribe_all_feeds()
    with _lock:
        if _sdk is not None:
            try:
                _sdk.on_ticks = None
                _sdk.ws_disconnect()
            except Exception as exc:
                err = str(exc)
        _sdk = None
        _sdk_user_id = None
        _connected = False
    stop_tick_pipeline()
    ok = err is None
    entry = _record_playground_event("ws_disconnect", "ws_disconnect", {}, err, ok)
    return _playground_response(err, ok=ok, icici_command=cmd, event_id=entry["id"])


def ws_release_playground(holder_id: str | None = None) -> dict[str, Any]:
    """Release subscriptions for holder; keep ICICI socket open."""
    hid = str(holder_id or "").strip()
    released = release_holder(hid) if hid else {"released": 0, "holder_id": hid}
    entry = _record_playground_event(
        "ws_release",
        "release_holder",
        {"holder_id": hid},
        released,
        True,
        note="subscriptions released; socket kept open",
    )
    return _playground_response(released, ok=True, icici_command=_icici_command("release_holder", {"holder_id": hid}), event_id=entry["id"])


def add_playground_listener(cb: Any) -> None:
    _playground_listeners.append(cb)
    register_tick_listener(cb)


def remove_playground_listener(cb: Any) -> None:
    try:
        _playground_listeners.remove(cb)
    except ValueError:
        pass
    unregister_tick_listener(cb)


def playground_subscribe(proc: "Processor", user_id: str, params: dict[str, Any]) -> dict[str, Any]:
    exchange_code = str(params.get("exchange_code") or "")
    stock_code = str(params.get("stock_code") or "")
    expiry_date = str(params.get("expiry_date") or "")
    strike_price = str(params.get("strike_price") or "")
    right = str(params.get("right") or "")
    holder_id = str(params.get("holder_id") or "").strip() or None

    sdk_args = _subscribe_feeds_sdk_args(exchange_code, stock_code, expiry_date, strike_price, right)
    cmd = _icici_command("subscribe_feeds", sdk_args)

    if proc.get_session_breeze(user_id) is None:
        entry = _record_playground_event(
            "subscribe_feeds", "subscribe_feeds", sdk_args, None, False, note="no broker session"
        )
        return _playground_response(None, ok=False, icici_command=cmd, event_id=entry["id"])

    try:
        _ensure_playground_ws(proc, user_id)
    except Exception as exc:
        entry = _record_playground_event("subscribe_feeds", "subscribe_feeds", sdk_args, str(exc), False)
        return _playground_response(str(exc), ok=False, icici_command=cmd, event_id=entry["id"])

    strike = parse_strike(strike_price)
    if strike is None or not expiry_date.strip():
        entry = _record_playground_event(
            "subscribe_feeds",
            "subscribe_feeds",
            sdk_args,
            None,
            False,
            note="strike and expiry required for holder-tracked subscribe",
        )
        return _playground_response(None, ok=False, icici_command=cmd, event_id=entry["id"])

    r = str(right or "").strip().lower()
    if r in {cfg.CALL.lower(), "call"}:
        r = "call"
    else:
        r = "put"

    ok = subscribe_option(
        proc,
        user_id,
        exchange_code,
        stock_code,
        expiry_date.strip(),
        strike,
        r,
        holder_id=holder_id,
    )
    result = {"message": "subscribed"} if ok else (_last_error or "subscribe failed")
    if not ok:
        entry = _record_playground_event("subscribe_feeds", "subscribe_feeds", sdk_args, result, False)
        return _playground_response(result, ok=False, icici_command=cmd, event_id=entry["id"])
    _clear_error()
    entry = _record_playground_event("subscribe_feeds", "subscribe_feeds", sdk_args, result, True)
    return _playground_response(result, ok=True, icici_command=cmd, event_id=entry["id"])


def shutdown_websocket() -> None:
    ws_disconnect_playground()
