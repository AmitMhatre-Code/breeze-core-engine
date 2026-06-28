"""Breeze API Playground: raw ICICI passthrough (no synthesized invoke errors)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from icici_breeze_backend.app.domain.breeze_api_tester_catalog import (
    build_invoke_args_permissive,
    is_breeze_invoke_response_ok,
)
from icici_breeze_backend.app.services.breeze_websocket_manager import playground_subscribe


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


def test_playground_subscribe_empty_strike_returns_verbatim_sdk_string(monkeypatch):
    sdk = MagicMock()
    sdk.ws_connect.return_value = None
    sdk.subscribe_feeds.return_value = (
        "Exception while subscribing to feeds Strike Price cannot be empty for Product-Type 'Options'."
    )

    proc = MagicMock()
    proc.get_session_breeze.return_value = sdk

    import icici_breeze_backend.app.services.breeze_websocket_manager as bwm

    monkeypatch.setattr(bwm, "_sdk", None)
    monkeypatch.setattr(bwm, "_sdk_user_id", None)
    monkeypatch.setattr(bwm, "_connected", False)
    monkeypatch.setattr(bwm, "_sub_refs", {})

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

    assert out["ok"] is False
    assert "subscribe validation" not in str(out.get("response"))
    assert "Strike Price cannot be empty" in str(out.get("response"))
    sdk.subscribe_feeds.assert_called_once()


def test_playground_subscribe_sdk_exception_in_response(monkeypatch):
    sdk = MagicMock()
    sdk.ws_connect.side_effect = RuntimeError("Could not authenticate credentials. Please check token and keys")

    proc = MagicMock()
    proc.get_session_breeze.return_value = sdk

    import icici_breeze_backend.app.services.breeze_websocket_manager as bwm

    monkeypatch.setattr(bwm, "_sdk", None)
    monkeypatch.setattr(bwm, "_sdk_user_id", None)
    monkeypatch.setattr(bwm, "_connected", False)

    out = playground_subscribe(proc, "u1", {"exchange_code": "NFO"})

    assert out["ok"] is False
    assert out["response"] == "Could not authenticate credentials. Please check token and keys"
