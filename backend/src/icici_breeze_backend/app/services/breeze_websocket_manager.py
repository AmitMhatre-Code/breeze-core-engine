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
from icici_breeze_backend.app.db.redis_client import cache_get_json, cache_set_json
from icici_breeze_backend.app.services.reference_data.keys import ws_quote_key

if TYPE_CHECKING:
    from icici_breeze_backend.app.services.processor import processor as Processor

_logger = logging.getLogger(__name__)
_lock = threading.RLock()

# subscription_key -> refcount
_sub_refs: dict[str, int] = {}
_sdk: Any = None
_sdk_user_id: str | None = None
_connected = False
_last_error: str | None = None
_playground_listeners: list[Any] = []
_PLAYGROUND_EVENT_LIMIT = 50
_playground_events: list[dict[str, Any]] = []
_playground_event_seq = 0


def _note_error(message: str, *args: Any) -> str:
    global _last_error
    text = message % args if args else message
    _last_error = text
    _logger.warning(text)
    return text


def _clear_error() -> None:
    global _last_error
    _last_error = None


def get_playground_status() -> dict[str, Any]:
    with _lock:
        return {
            "connected": _connected,
            "user_id": _sdk_user_id,
            "active_subscriptions": len(_sub_refs),
            "subscription_keys": sorted(_sub_refs.keys()),
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


def _sub_key(
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


def _normalize_tick(tick: dict[str, Any], stock_code: str, expiry_display: str, right: str, strike: Strike) -> dict[str, Any]:
    total_buy = int(tick.get("totalBuyQt") or tick.get("total_buy_qty") or 0)
    total_sell = int(tick.get("totalSellQ") or tick.get("total_sell_qty") or 0)
    if total_sell > 0:
        ratio: float | str = total_buy / total_sell
    else:
        ratio = 0.0 if total_buy == 0 else "NA"
    ltp = tick.get("last") if tick.get("last") is not None else tick.get("ltp")
    return {
        "stock_code": stock_code,
        "strike_price": strike,
        "right": cfg.CALL if str(right).lower().startswith("c") else cfg.PUT,
        "expiry_date": expiry_display,
        "ltp": ltp,
        "open_interest": int(tick.get("OI") or tick.get("open_interest") or 0),
        "total_buy_qty": total_buy,
        "total_sell_qty": total_sell,
        "buy_sell_ratio": ratio,
        "best_bid_price": tick.get("bPrice") or tick.get("best_bid_price"),
        "best_offer_price": tick.get("sPrice") or tick.get("best_offer_price"),
        "spot_price": tick.get("spot_price"),
        "lot_size": tick.get("lot_size"),
        "updated_at": time.time(),
    }


def _on_ticks(ticks: Any) -> None:
    if not isinstance(ticks, dict):
        return
    stock = str(ticks.get("stock_code") or ticks.get("stock_name") or "").strip()
    expiry = str(ticks.get("expiry_date") or "").strip()
    strike_raw = ticks.get("strike_price")
    right_raw = ticks.get("right") or ticks.get("right_type") or ""
    strike = parse_strike(strike_raw)
    if not stock or not expiry or strike is None:
        return
    if "T" in expiry or len(expiry) == 10:
        import datetime as dt

        try:
            if "T" in expiry:
                expiry = expiry.split("T", 1)[0]
            expiry = dt.datetime.strptime(expiry[:10], "%Y-%m-%d").strftime("%d-%b-%Y")
        except ValueError:
            pass
    right = "call" if str(right_raw).lower().startswith("c") else "put"
    short = stock.split()[0].upper() if stock else stock
    cell = _normalize_tick(ticks, short, expiry, right, strike)
    ex = cfg.NFO if str(ticks.get("exchange_code") or ticks.get("exchange") or "").upper().find("BSE") < 0 else cfg.BFO
    key = ws_quote_key(ex, short, expiry, strike, right)
    cache_set_json(key, cell, ex=cfg.WEBSOCKET_QUOTE_TTL_SECONDS)
    payload = {"raw": dict(ticks), "normalized": cell}
    for listener in list(_playground_listeners):
        try:
            listener(payload)
        except Exception:
            pass


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
            sdk.on_ticks = _on_ticks
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


def subscribe_option(
    proc: "Processor",
    user_id: str,
    exchange_code: str,
    stock_code: str,
    expiry_display: str,
    strike: Strike,
    right: str,
) -> bool:
    key = _sub_key(exchange_code, stock_code, expiry_display, strike, right)
    with _lock:
        _sub_refs[key] = _sub_refs.get(key, 0) + 1
        if _sub_refs[key] > 1:
            return True
    sdk = _ensure_ws(proc, user_id)
    if sdk is None:
        with _lock:
            _sub_refs[key] = max(0, _sub_refs.get(key, 1) - 1)
        return False
    r = str(right or "").strip().lower()
    if r == cfg.CALL.lower():
        r = "call"
    else:
        r = "put"
    try:
        result = sdk.subscribe_feeds(
            exchange_code=exchange_code,
            stock_code=stock_code,
            expiry_date=expiry_display,
            strike_price=strike_for_broker(strike),
            right=r,
            product_type="options",
            get_market_depth=False,
            get_exchange_quotes=True,
        )
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
                with _lock:
                    _sub_refs[key] = max(0, _sub_refs.get(key, 1) - 1)
                return False
        _logger.info(
            "subscribe_feeds ok user_id=%s key=%s exchange=%s stock=%s expiry=%s strike=%s right=%s",
            user_id,
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
        with _lock:
            _sub_refs[key] = max(0, _sub_refs.get(key, 1) - 1)
        return False


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
) -> dict[str, Any] | None:
    if not strikes:
        return None
    for strike in strikes:
        for right in ("call", "put"):
            subscribe_option(proc, user_id, exchange_code, stock_code, expiry_display, strike, right)

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
    """Connect SDK WebSocket for playground without synthesizing error messages."""
    global _sdk, _sdk_user_id, _connected
    with _lock:
        if _sdk is not None and _sdk_user_id == user_id and _connected:
            return _sdk
    sdk = proc.get_session_breeze(user_id)
    if sdk is None:
        return None
    sdk.ws_connect()
    sdk.on_ticks = _on_ticks
    _register_playground_connected(sdk, user_id)
    _logger.info("WebSocket connected for user_id=%s (playground)", user_id)
    return sdk


def ws_connect_playground(proc: "Processor", user_id: str) -> dict[str, Any]:
    cmd = _icici_command("ws_connect", {}, side_effects=["sdk.on_ticks = _on_ticks"])
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


def ws_disconnect_playground() -> dict[str, Any]:
    global _sdk, _sdk_user_id, _connected
    cmd = _icici_command("ws_disconnect", {})
    err: str | None = None
    with _lock:
        if _sdk is not None:
            try:
                _sdk.ws_disconnect()
            except Exception as exc:
                err = str(exc)
        _sdk = None
        _sdk_user_id = None
        _connected = False
        _sub_refs.clear()
    ok = err is None
    entry = _record_playground_event("ws_disconnect", "ws_disconnect", {}, err, ok)
    return _playground_response(err, ok=ok, icici_command=cmd, event_id=entry["id"])


def add_playground_listener(cb: Any) -> None:
    _playground_listeners.append(cb)


def remove_playground_listener(cb: Any) -> None:
    try:
        _playground_listeners.remove(cb)
    except ValueError:
        pass


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


def playground_subscribe(proc: "Processor", user_id: str, params: dict[str, Any]) -> dict[str, Any]:
    exchange_code = str(params.get("exchange_code") or "")
    stock_code = str(params.get("stock_code") or "")
    expiry_display = str(params.get("expiry_date") or "").strip()
    strike_raw = str(params.get("strike_price") or "").strip()
    right_in = str(params.get("right") or "")

    if proc.get_session_breeze(user_id) is None:
        sdk_args = _subscribe_feeds_sdk_args(exchange_code, stock_code, expiry_display, strike_raw, right_in)
        cmd = _icici_command("subscribe_feeds", sdk_args)
        entry = _record_playground_event(
            "subscribe_feeds", "subscribe_feeds", sdk_args, None, False, note="no broker session"
        )
        return _playground_response(None, ok=False, icici_command=cmd, event_id=entry["id"])

    if not expiry_display:
        sdk_args = _subscribe_feeds_sdk_args(exchange_code, stock_code, expiry_display, strike_raw, right_in)
        cmd = _icici_command("subscribe_feeds", sdk_args)
        msg = "expiry_date is required"
        entry = _record_playground_event("subscribe_feeds", "subscribe_feeds", sdk_args, msg, False)
        return _playground_response(msg, ok=False, icici_command=cmd, event_id=entry["id"])

    if not strike_raw:
        sdk_args = _subscribe_feeds_sdk_args(exchange_code, stock_code, expiry_display, strike_raw, right_in)
        cmd = _icici_command("subscribe_feeds", sdk_args)
        msg = "strike_price is required"
        entry = _record_playground_event("subscribe_feeds", "subscribe_feeds", sdk_args, msg, False)
        return _playground_response(msg, ok=False, icici_command=cmd, event_id=entry["id"])

    strike = parse_strike(strike_raw)
    if strike is None:
        sdk_args = _subscribe_feeds_sdk_args(exchange_code, stock_code, expiry_display, strike_raw, right_in)
        cmd = _icici_command("subscribe_feeds", sdk_args)
        msg = f"Invalid strike_price: {strike_raw!r}"
        entry = _record_playground_event("subscribe_feeds", "subscribe_feeds", sdk_args, msg, False)
        return _playground_response(msg, ok=False, icici_command=cmd, event_id=entry["id"])

    try:
        strike_wire = strike_for_broker(strike)
    except ValueError as exc:
        sdk_args = _subscribe_feeds_sdk_args(exchange_code, stock_code, expiry_display, strike_raw, right_in)
        cmd = _icici_command("subscribe_feeds", sdk_args)
        msg = str(exc)
        entry = _record_playground_event("subscribe_feeds", "subscribe_feeds", sdk_args, msg, False)
        return _playground_response(msg, ok=False, icici_command=cmd, event_id=entry["id"])

    r = str(right_in or "").strip().lower()
    if r in {cfg.CALL.lower(), "call"}:
        r = "call"
    else:
        r = "put"

    sdk_args = _subscribe_feeds_sdk_args(exchange_code, stock_code, expiry_display, strike_wire, r)
    cmd = _icici_command("subscribe_feeds", sdk_args)
    sub_key = _sub_key(exchange_code, stock_code, expiry_display, strike, r)

    try:
        _ensure_playground_ws(proc, user_id)
    except Exception as exc:
        entry = _record_playground_event("subscribe_feeds", "subscribe_feeds", sdk_args, str(exc), False)
        return _playground_response(str(exc), ok=False, icici_command=cmd, event_id=entry["id"])

    sdk = proc.get_session_breeze(user_id)
    if sdk is None:
        entry = _record_playground_event(
            "subscribe_feeds", "subscribe_feeds", sdk_args, None, False, note="no broker session after connect"
        )
        return _playground_response(None, ok=False, icici_command=cmd, event_id=entry["id"])

    try:
        result = sdk.subscribe_feeds(**sdk_args)
        ok = is_breeze_invoke_response_ok(result)
        if not ok:
            err = result if isinstance(result, str) else (
                (result.get("Error") or result.get("error") or result.get("message") or str(result))
                if isinstance(result, dict)
                else str(result)
            )
            _note_error("subscribe_feeds ICICI error %s: %s", sub_key, err)
        else:
            with _lock:
                _sub_refs[sub_key] = _sub_refs.get(sub_key, 0) + 1
            _clear_error()
        entry = _record_playground_event("subscribe_feeds", "subscribe_feeds", sdk_args, result, ok)
        return _playground_response(result, ok=ok, icici_command=cmd, event_id=entry["id"])
    except Exception as exc:
        _note_error("subscribe_feeds failed %s: %s", sub_key, exc)
        entry = _record_playground_event("subscribe_feeds", "subscribe_feeds", sdk_args, str(exc), False)
        return _playground_response(str(exc), ok=False, icici_command=cmd, event_id=entry["id"])


def shutdown_websocket() -> None:
    ws_disconnect_playground()
