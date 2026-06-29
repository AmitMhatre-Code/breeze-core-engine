"""Tests for holder-scoped WebSocket subscription lifecycle."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from icici_breeze_backend.app.services import breeze_websocket_manager as bwm


def _reset_bwm(monkeypatch) -> None:
    monkeypatch.setattr(bwm, "_holders", {})
    monkeypatch.setattr(bwm, "_sub_holders", {})
    monkeypatch.setattr(bwm, "_sub_meta", {})
    monkeypatch.setattr(bwm, "_sdk", None)
    monkeypatch.setattr(bwm, "_sdk_user_id", None)
    monkeypatch.setattr(bwm, "_connected", False)
    monkeypatch.setattr(bwm, "_playground_events", [])
    monkeypatch.setattr(bwm, "_playground_event_seq", 0)
    monkeypatch.setattr(bwm, "_last_error", None)


def _mock_sdk(monkeypatch):
    sdk = MagicMock()
    sdk.subscribe_feeds.return_value = {"message": "ok"}
    sdk.unsubscribe_feeds.return_value = {"message": "ok"}
    proc = MagicMock()
    proc.get_session_breeze.return_value = sdk
    monkeypatch.setattr(bwm, "start_tick_pipeline", lambda: None)
    _reset_bwm(monkeypatch)
    monkeypatch.setattr(bwm, "_sdk", sdk)
    monkeypatch.setattr(bwm, "_connected", True)
    monkeypatch.setattr(bwm, "_sdk_user_id", "u1")
    return sdk, proc


def test_subscribe_and_release_single_holder(monkeypatch):
    sdk, proc = _mock_sdk(monkeypatch)
    ok = bwm.subscribe_option(
        proc, "u1", "NFO", "NIFTY", "30-Jun-2026", 25000.0, "call", holder_id="h1"
    )
    assert ok is True
    sdk.subscribe_feeds.assert_called_once()
    assert bwm.get_playground_status()["active_subscriptions"] == 1

    out = bwm.release_holder("h1")
    assert out["released"] == 1
    sdk.unsubscribe_feeds.assert_called_once()
    assert bwm.get_playground_status()["active_subscriptions"] == 0
    sdk.ws_disconnect.assert_not_called()


def test_two_holders_share_subscription(monkeypatch):
    sdk, proc = _mock_sdk(monkeypatch)
    bwm.subscribe_option(proc, "u1", "NFO", "NIFTY", "30-Jun-2026", 25000.0, "call", holder_id="h1")
    bwm.subscribe_option(proc, "u1", "NFO", "NIFTY", "30-Jun-2026", 25000.0, "call", holder_id="h2")
    sdk.subscribe_feeds.assert_called_once()

    bwm.release_holder("h1")
    sdk.unsubscribe_feeds.assert_not_called()
    assert bwm.get_playground_status()["active_subscriptions"] == 1

    bwm.release_holder("h2")
    sdk.unsubscribe_feeds.assert_called_once()


def test_sync_holder_chain_replaces_strikes(monkeypatch):
    sdk, proc = _mock_sdk(monkeypatch)
    bwm.sync_holder_chain_subscriptions(
        proc, "u1", "h1", "NIFTY", "NFO", "30-Jun-2026", [24000.0, 25000.0]
    )
    assert sdk.subscribe_feeds.call_count == 4  # 2 strikes x call/put

    bwm.sync_holder_chain_subscriptions(
        proc, "u1", "h1", "NIFTY", "NFO", "30-Jun-2026", [25000.0, 26000.0]
    )
    # 24000 call+put unsubscribed; 26000 call+put subscribed
    assert sdk.unsubscribe_feeds.call_count == 2
    assert sdk.subscribe_feeds.call_count == 6


def test_release_unknown_holder_is_idempotent():
    out = bwm.release_holder("missing-holder")
    assert out["released"] == 0
