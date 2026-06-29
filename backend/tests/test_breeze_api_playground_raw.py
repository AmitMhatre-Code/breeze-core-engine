"""Breeze API Playground: raw ICICI passthrough (no synthesized invoke errors)."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from icici_breeze_backend.app.domain.breeze_api_tester_catalog import (
    build_invoke_args_permissive,
    is_breeze_invoke_response_ok,
)
from icici_breeze_backend.app.services.breeze_websocket_manager import (
    get_playground_event_log,
    playground_subscribe,
    ws_connect_playground,
)


def _reset_bwm(monkeypatch) -> None:
    import icici_breeze_backend.app.services.breeze_websocket_manager as bwm

    monkeypatch.setattr(bwm, "_sdk", None)
    monkeypatch.setattr(bwm, "_sdk_user_id", None)
    monkeypatch.setattr(bwm, "_connected", False)
    monkeypatch.setattr(bwm, "_sub_refs", {})
    monkeypatch.setattr(bwm, "_playground_events", [])
    monkeypatch.setattr(bwm, "_playground_event_seq", 0)
    monkeypatch.setattr(bwm, "_last_error", None)


def test_build_invoke_args_permissive_allows_empty_required_params():
    pos, kw = build_invoke_args_permissive(
        "subscribe_feeds",
        {
            "exchange_code": "NFO",
            "stock_code": "",
            "expiry_date": "",
            "strike_price": "",
            "right": "",
        },
    )
    assert pos == ()
    assert kw["stock_code"] == ""
    assert kw["strike_price"] == ""


def test_build_invoke_args_permissive_invalid_json_passes_raw_string():
    pos, kw = build_invoke_args_permissive(
        "margin_calculator",
        {"margin_list": "{not json", "exchange_code": "NFO"},
    )
    assert pos == ("{not json",)
    assert kw["exchange_code"] == "NFO"


def test_is_breeze_invoke_response_ok_string_exception():
    err = "Exception while subscribing to feeds Strike Price cannot be empty for Product-Type 'Options'."
    assert is_breeze_invoke_response_ok(err) is False
    assert is_breeze_invoke_response_ok({"message": "Stock NIFTY subscribed successfully"}) is True
    assert is_breeze_invoke_response_ok({"Status": 400, "Error": "bad"}) is False


def test_playground_subscribe_empty_strike_passthrough_icici_response(monkeypatch):
    """ICICI SDK may return success even when strike_price is empty (live playground behaviour)."""
    sdk = MagicMock()
    sdk.ws_connect.return_value = None
    sdk.subscribe_feeds.return_value = {"message": "Stock NIFTY subscribed successfully"}

    proc = MagicMock()
    proc.get_session_breeze.return_value = sdk
    _reset_bwm(monkeypatch)

    out = playground_subscribe(
        proc,
        "u1",
        {
            "exchange_code": "NFO",
            "stock_code": "NIFTY",
            "expiry_date": "30-Jun-2026",
            "strike_price": "",
            "right": "call",
        },
    )

    assert out["ok"] is True
    assert out["response"] == {"message": "Stock NIFTY subscribed successfully"}
    assert out["icici_command"]["sdk_args"]["strike_price"] == ""
    sdk.subscribe_feeds.assert_called_once()


def test_playground_subscribe_sdk_exception_in_response(monkeypatch):
    sdk = MagicMock()
    sdk.ws_connect.side_effect = RuntimeError("Could not authenticate credentials. Please check token and keys")

    proc = MagicMock()
    proc.get_session_breeze.return_value = sdk
    _reset_bwm(monkeypatch)

    out = playground_subscribe(
        proc,
        "u1",
        {
            "exchange_code": "NFO",
            "stock_code": "NIFTY",
            "expiry_date": "30-Jun-2026",
            "strike_price": "25000",
            "right": "call",
        },
    )

    assert out["ok"] is False
    assert out["response"] == "Could not authenticate credentials. Please check token and keys"
    assert out["icici_command"]["sdk_method"] == "subscribe_feeds"


def test_ws_connect_playground_includes_icici_command(monkeypatch):
    sdk = MagicMock()
    proc = MagicMock()
    proc.get_session_breeze.return_value = sdk
    _reset_bwm(monkeypatch)

    out = ws_connect_playground(proc, "u1")

    assert out["ok"] is True
    assert out["icici_command"]["sdk_method"] == "ws_connect"
    assert "on_ticks" in " ".join(out["icici_command"].get("side_effects") or [])
    assert out["event_id"] is not None
    events = get_playground_event_log()
    assert events and events[0]["step"] == "ws_connect"


def test_playground_subscribe_success_tracks_active_subscriptions(monkeypatch):
    sdk = MagicMock()
    sdk.subscribe_feeds.return_value = {"message": "Stock NIFTY subscribed successfully"}

    proc = MagicMock()
    proc.get_session_breeze.return_value = sdk
    _reset_bwm(monkeypatch)

    out = playground_subscribe(
        proc,
        "u1",
        {
            "exchange_code": "NFO",
            "stock_code": "NIFTY",
            "expiry_date": "30-Jun-2026",
            "strike_price": "25000",
            "right": "call",
        },
    )

    assert out["ok"] is True
    assert out["active_subscriptions"] == 1
    assert out["icici_command"]["sdk_args"]["strike_price"] == "25000"
    assert out["icici_command"]["sdk_args"]["product_type"] == "options"
    assert out["icici_command"]["sdk_args"]["get_exchange_quotes"] is True
    sdk.subscribe_feeds.assert_called_once_with(
        exchange_code="NFO",
        stock_code="NIFTY",
        expiry_date="30-Jun-2026",
        strike_price="25000",
        right="call",
        product_type="options",
        get_market_depth=False,
        get_exchange_quotes=True,
    )


def test_sse_tick_queue_uses_threadsafe_put():
    async def _run() -> list[dict]:
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        received: list[dict] = []

        def _on_tick(payload: dict) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, payload)

        _on_tick({"raw": {"ltp": 1}, "normalized": {"ltp": 1}})
        item = await asyncio.wait_for(queue.get(), timeout=1.0)
        received.append(item)
        return received

    received = asyncio.run(_run())
    assert received == [{"raw": {"ltp": 1}, "normalized": {"ltp": 1}}]
