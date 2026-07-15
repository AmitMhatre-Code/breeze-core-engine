"""Multiplexed Breeze WebSocket for option chain exchange quotes."""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, TYPE_CHECKING

from icici_breeze_backend.app.domain.breeze_api_tester_catalog import (
    sdk_args_from_user_params,
)
import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.strike import Strike, strike_for_broker, strike_key
from icici_breeze_backend.app.services.reference_data.active_chains import (
    register_holder_chain,
    release_holder_chains,
)
from icici_breeze_backend.app.services.reference_data.ws_token_index import (
    list_ws_stock_tokens_for_liquid_contracts,
)

if TYPE_CHECKING:
    from icici_breeze_backend.app.services.processor import processor as Processor

_logger = logging.getLogger(__name__)
_lock = threading.RLock()

# holder_id -> set of stock_token symbols (e.g. 4.1!44684)
_holders: dict[str, set[str]] = {}
# stock_token -> set of holder_ids
_sub_holders: dict[str, set[str]] = {}
# stock_token -> ICICI unsubscribe kwargs
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


def _subscribe_batch_size() -> int:
    try:
        return max(1, int(getattr(cfg, "BWS_SUBSCRIBE_BATCH_SIZE", 50) or 50))
    except (TypeError, ValueError):
        return 50


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


def _stock_token_sdk_args(tokens: list[str]) -> dict[str, Any]:
    return {"stock_token": list(tokens)}


def _on_ticks(ticks: Any) -> None:
    from icici_breeze_backend.app.services.ws_tick_pipeline import ingest_tick

    ingest_tick(ticks)


def _attach_sdk_ticks_handler(sdk: Any) -> None:
    from icici_breeze_backend.app.services.ws_tick_pipeline import start_tick_pipeline

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


def _icici_unsubscribe_stock_token(stock_token: str, meta: dict[str, Any]) -> bool:
    sdk = _sdk
    if sdk is None:
        return False
    try:
        if hasattr(sdk, "unsubscribe_feeds"):
            sdk.unsubscribe_feeds(**meta)
        else:
            _logger.warning("SDK missing unsubscribe_feeds; skipping %s", stock_token)
        _logger.info("unsubscribe_feeds ok token=%s", stock_token)
        return True
    except Exception as exc:
        _note_error("unsubscribe_feeds failed %s: %s", stock_token, exc)
        return False


def _icici_unsubscribe(key: str, meta: dict[str, Any]) -> bool:
    return _icici_unsubscribe_stock_token(key, meta)


def _detach_holder_from_token(holder_id: str, stock_token: str) -> None:
    meta: dict[str, Any] | None = None
    should_unsub = False
    with _lock:
        holder_tokens = _holders.get(holder_id)
        if holder_tokens is not None:
            holder_tokens.discard(stock_token)
            if not holder_tokens:
                _holders.pop(holder_id, None)
        holders = _sub_holders.get(stock_token)
        if holders is None:
            return
        holders.discard(holder_id)
        if holders:
            return
        should_unsub = True
        meta = _sub_meta.pop(stock_token, None)
        _sub_holders.pop(stock_token, None)
    if should_unsub and meta:
        _icici_unsubscribe_stock_token(stock_token, meta)


def _detach_holder_from_key(holder_id: str, key: str) -> None:
    _detach_holder_from_token(holder_id, key)


def release_holder(holder_id: str) -> dict[str, Any]:
    """Release all subscriptions owned by holder_id; keep WebSocket connected."""
    hid = str(holder_id or "").strip()
    if not hid or hid == _UNTRACKED_HOLDER:
        return {"released": 0, "holder_id": hid}
    with _lock:
        tokens = list(_holders.pop(hid, set()))
    for token in tokens:
        _detach_holder_from_token(hid, token)
    release_holder_chains(hid)
    return {"released": len(tokens), "holder_id": hid}


def _subscribe_stock_token_batch(
    proc: "Processor",
    user_id: str,
    tokens: list[str],
    *,
    holder_id: str,
) -> bool:
    if not tokens:
        return True
    hid = _effective_holder(holder_id)
    to_subscribe: list[str] = []
    with _lock:
        for token in tokens:
            if token in _holders.get(hid, set()):
                continue
            is_new = token not in _sub_holders or len(_sub_holders[token]) == 0
            if not is_new:
                _holders.setdefault(hid, set()).add(token)
                _sub_holders.setdefault(token, set()).add(hid)
            else:
                to_subscribe.append(token)

    if not to_subscribe:
        return True

    sdk = _ensure_ws(proc, user_id)
    if sdk is None:
        return False

    batch_size = _subscribe_batch_size()
    ok_all = True
    for i in range(0, len(to_subscribe), batch_size):
        chunk = to_subscribe[i : i + batch_size]
        sdk_args = _stock_token_sdk_args(chunk)
        try:
            result = sdk.subscribe_feeds(**sdk_args)
            if isinstance(result, dict):
                st = result.get("Status") or result.get("status")
                if st not in (200, None):
                    err = (
                        result.get("Error")
                        or result.get("error")
                        or result.get("message")
                        or str(result)
                    )
                    _note_error("subscribe_feeds ICICI error batch %s: %s", chunk[:3], err)
                    ok_all = False
                    continue
            with _lock:
                for token in chunk:
                    _holders.setdefault(hid, set()).add(token)
                    _sub_holders.setdefault(token, set()).add(hid)
                    _sub_meta[token] = {"stock_token": [token]}
            _logger.info(
                "subscribe_feeds batch ok user_id=%s holder=%s count=%s",
                user_id,
                hid,
                len(chunk),
            )
        except Exception as exc:
            _note_error("subscribe_feeds batch failed: %s", exc)
            ok_all = False
    return ok_all


def _unsubscribe_stock_token_batch(tokens: list[str]) -> None:
    if not tokens:
        return
    batch_size = _subscribe_batch_size()
    for i in range(0, len(tokens), batch_size):
        chunk = tokens[i : i + batch_size]
        meta = _stock_token_sdk_args(chunk)
        sdk = _sdk
        if sdk is None:
            continue
        try:
            if hasattr(sdk, "unsubscribe_feeds"):
                sdk.unsubscribe_feeds(**meta)
        except Exception as exc:
            _note_error("unsubscribe_feeds batch failed: %s", exc)


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
    """Subscribe a single contract via stock_token lookup (batch API with one token)."""
    from icici_breeze_backend.app.services.reference_data.ws_token_index import (
        lookup_token_for_contract,
        format_ws_stock_token,
    )

    opt = cfg.CALL if str(right).lower().startswith("c") else cfg.PUT
    token = lookup_token_for_contract(
        exchange_code, stock_code, expiry_display, float(strike), opt
    )
    if token is None:
        return False
    ws_symbol = format_ws_stock_token(exchange_code, token)
    return _subscribe_stock_token_batch(
        proc, user_id, [ws_symbol], holder_id=_effective_holder(holder_id)
    )


def sync_holder_chain_subscriptions(
    proc: "Processor",
    user_id: str,
    holder_id: str,
    stock_code: str,
    exchange_code: str,
    expiry_display: str,
    strikes: list[Strike] | None = None,
) -> bool:
    """Returns False if new tokens needed subscribing but the batch subscribe
    failed (e.g. no live broker session) -- callers that gate a daily retry
    guard on this must not treat a False return as done-for-today."""
    del strikes  # liquid tokens resolved from scrip master
    hid = _effective_holder(holder_id)
    desired = set(
        list_ws_stock_tokens_for_liquid_contracts(exchange_code, stock_code, expiry_display)
    )
    with _lock:
        current = set(_holders.get(hid, set()))
    for token in sorted(current - desired):
        _detach_holder_from_token(hid, token)
    new_tokens = sorted(desired - current)
    ok = True
    if new_tokens:
        ok = _subscribe_stock_token_batch(proc, user_id, new_tokens, holder_id=hid)
    register_holder_chain(hid, exchange_code, stock_code, expiry_display)
    return ok


def ensure_chain_subscriptions(
    proc: "Processor",
    user_id: str,
    stock_code: str,
    exchange_code: str,
    expiry_display: str,
    strikes: list[Strike] | None = None,
    *,
    lot_size: int = 0,
    freeze_quantity: int | None = None,
    holder_id: str | None = None,
    spot: float | None = None,
) -> dict[str, Any] | None:
    from icici_breeze_backend.app.services.chain_readiness import wait_for_canonical_chain

    sync_holder_chain_subscriptions(
        proc, user_id, _effective_holder(holder_id), stock_code, exchange_code, expiry_display, strikes
    )
    payload = wait_for_canonical_chain(
        exchange_code,
        stock_code,
        expiry_display,
        lot_size=lot_size,
        freeze_quantity=freeze_quantity,
        spot=spot,
    )
    if payload is None:
        return None
    out = dict(payload)
    out["quote_source"] = "websocket"
    out["bhavcopy_date"] = None
    return out


def ensure_atm_quote_subscription(
    proc: "Processor",
    user_id: str,
    stock_code: str,
    exchange_code: str,
    expiry_display: str,
    strikes: list[Strike] | None,
    atm_strike: Strike,
    *,
    lot_size: int = 0,
    freeze_quantity: int | None = None,
    holder_id: str | None = None,
) -> dict[str, Any] | None:
    """Same subscription bookkeeping as `ensure_chain_subscriptions`, but the
    wait is gated on the ATM strike's own quote only (see
    `chain_readiness.wait_for_strike_quote`) instead of every strike in the chain."""
    from icici_breeze_backend.app.services.chain_readiness import wait_for_strike_quote

    sync_holder_chain_subscriptions(
        proc, user_id, _effective_holder(holder_id), stock_code, exchange_code, expiry_display, strikes
    )
    payload = wait_for_strike_quote(
        exchange_code,
        stock_code,
        expiry_display,
        atm_strike,
        lot_size=lot_size,
        freeze_quantity=freeze_quantity,
    )
    if payload is None:
        return None
    out = dict(payload)
    out["quote_source"] = "websocket"
    out["bhavcopy_date"] = None
    return out


def _playground_response(
    response: Any,
    ok: bool | None = None,
    *,
    icici_command: dict[str, Any] | None = None,
    event_id: int | None = None,
) -> dict[str, Any]:
    if ok is None:
        ok = True
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
        tokens_meta = list(_sub_meta.items())
        _holders.clear()
        _sub_holders.clear()
        _sub_meta.clear()
    for token, meta in tokens_meta:
        _icici_unsubscribe_stock_token(token, meta)


def ws_disconnect_playground() -> dict[str, Any]:
    global _sdk, _sdk_user_id, _connected
    from icici_breeze_backend.app.services.ws_tick_pipeline import stop_tick_pipeline

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


def _playground_sub_key(sdk_args: dict[str, Any]) -> str:
    stock_token = sdk_args.get("stock_token")
    if isinstance(stock_token, list) and stock_token:
        return "|".join(sorted(str(t) for t in stock_token))
    if isinstance(stock_token, str) and stock_token.strip():
        return stock_token.strip()
    r = str(sdk_args.get("right") or "").strip().lower()
    if r in {cfg.CALL.lower(), "call"}:
        r = "call"
    elif r:
        r = "put"
    return "|".join(
        [
            str(sdk_args.get("exchange_code") or "").upper(),
            str(sdk_args.get("stock_code") or "").upper(),
            str(sdk_args.get("expiry_date") or ""),
            str(sdk_args.get("strike_price") or ""),
            r,
        ]
    )


def _track_playground_sub(holder_id: str | None, sdk_args: dict[str, Any]) -> None:
    hid = _effective_holder(holder_id)
    if hid == _UNTRACKED_HOLDER:
        return
    stock_token = sdk_args.get("stock_token")
    tokens: list[str] = []
    if isinstance(stock_token, list):
        tokens = [str(t) for t in stock_token]
    elif isinstance(stock_token, str) and stock_token.strip():
        tokens = [stock_token.strip()]
    if tokens:
        with _lock:
            for token in tokens:
                _holders.setdefault(hid, set()).add(token)
                _sub_holders.setdefault(token, set()).add(hid)
                _sub_meta[token] = {"stock_token": [token]}
        return
    key = _playground_sub_key(sdk_args)
    with _lock:
        _holders.setdefault(hid, set()).add(key)
        _sub_holders.setdefault(key, set()).add(hid)
        _sub_meta[key] = dict(sdk_args)


def add_playground_listener(cb: Any) -> None:
    from icici_breeze_backend.app.services.ws_tick_pipeline import (
        register_raw_tick_listener,
    )

    _playground_listeners.append(cb)
    register_raw_tick_listener(cb)


def remove_playground_listener(cb: Any) -> None:
    from icici_breeze_backend.app.services.ws_tick_pipeline import (
        unregister_raw_tick_listener,
    )

    try:
        _playground_listeners.remove(cb)
    except ValueError:
        pass
    unregister_raw_tick_listener(cb)


def playground_subscribe(proc: "Processor", user_id: str, params: dict[str, Any]) -> dict[str, Any]:
    params = dict(params or {})
    holder_id = str(params.get("holder_id") or "").strip() or None
    sdk_args = sdk_args_from_user_params(params)
    cmd = _icici_command("subscribe_feeds", sdk_args)

    if proc.get_session_breeze(user_id) is None:
        entry = _record_playground_event(
            "subscribe_feeds", "subscribe_feeds", sdk_args, None, False, note="no broker session"
        )
        return _playground_response(None, ok=False, icici_command=cmd, event_id=entry["id"])

    try:
        sdk = _ensure_playground_ws(proc, user_id)
        if sdk is None:
            raise RuntimeError(_last_error or "WebSocket connect failed")
        result = sdk.subscribe_feeds(**sdk_args)
        _track_playground_sub(holder_id, sdk_args)
        _clear_error()
        entry = _record_playground_event("subscribe_feeds", "subscribe_feeds", sdk_args, result, True)
        return _playground_response(result, ok=True, icici_command=cmd, event_id=entry["id"])
    except Exception as exc:
        entry = _record_playground_event("subscribe_feeds", "subscribe_feeds", sdk_args, str(exc), False)
        return _playground_response(str(exc), ok=False, icici_command=cmd, event_id=entry["id"])


def shutdown_websocket() -> None:
    ws_disconnect_playground()
