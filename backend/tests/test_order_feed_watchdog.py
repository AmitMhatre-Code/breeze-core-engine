"""Order-notification feed resilience: the account-wide feed drives the SG lifecycle
(fills -> Completed / Reset), so it must be armed independent of any option-chain
subscription (`ensure_order_feed`) and re-armed after a silent socket reconnect
(`order_feed_watchdog_tick`)."""
from __future__ import annotations

from unittest.mock import MagicMock

from icici_breeze_backend.app.services import breeze_websocket_manager as bwm


def _reset(monkeypatch) -> None:
    monkeypatch.setattr(bwm, "_sdk", None)
    monkeypatch.setattr(bwm, "_sdk_user_id", None)
    monkeypatch.setattr(bwm, "_connected", False)
    monkeypatch.setattr(bwm, "_last_error", None)


class TestWatchdog:
    def test_rearms_order_feed_when_connected(self, monkeypatch):
        _reset(monkeypatch)
        sdk = MagicMock()
        monkeypatch.setattr(bwm, "_sdk", sdk)
        monkeypatch.setattr(bwm, "_connected", True)
        monkeypatch.setattr(bwm, "_sdk_user_id", "u1")

        bwm.order_feed_watchdog_tick()

        sdk.subscribe_feeds.assert_called_once_with(get_order_notification=True)

    def test_noop_when_not_connected(self, monkeypatch):
        _reset(monkeypatch)
        sdk = MagicMock()
        monkeypatch.setattr(bwm, "_sdk", sdk)
        monkeypatch.setattr(bwm, "_connected", False)

        bwm.order_feed_watchdog_tick()

        sdk.subscribe_feeds.assert_not_called()

    def test_noop_when_no_sdk(self, monkeypatch):
        _reset(monkeypatch)
        # _sdk is None, _connected True (shouldn't happen, but must not blow up)
        monkeypatch.setattr(bwm, "_connected", True)
        bwm.order_feed_watchdog_tick()  # must not raise

    def test_warns_when_feed_silent_during_market_hours(self, monkeypatch):
        _reset(monkeypatch)
        sdk = MagicMock()
        monkeypatch.setattr(bwm, "_sdk", sdk)
        monkeypatch.setattr(bwm, "_connected", True)
        monkeypatch.setattr(bwm, "_sdk_user_id", "u1")
        monkeypatch.setattr(
            "icici_breeze_backend.app.services.ws_tick_pipeline.last_tick_age_seconds",
            lambda: 999.0,
        )
        monkeypatch.setattr(
            "icici_breeze_backend.app.services.market_calendar.is_market_open",
            lambda: True,
        )

        bwm.order_feed_watchdog_tick()

        assert bwm._last_error is not None and "silent" in bwm._last_error

    def test_no_warning_when_feed_is_fresh(self, monkeypatch):
        _reset(monkeypatch)
        sdk = MagicMock()
        monkeypatch.setattr(bwm, "_sdk", sdk)
        monkeypatch.setattr(bwm, "_connected", True)
        monkeypatch.setattr(bwm, "_sdk_user_id", "u1")
        monkeypatch.setattr(
            "icici_breeze_backend.app.services.ws_tick_pipeline.last_tick_age_seconds",
            lambda: 1.0,
        )

        bwm.order_feed_watchdog_tick()

        assert bwm._last_error is None


class TestEnsureOrderFeed:
    def test_connects_and_subscribes(self, monkeypatch):
        _reset(monkeypatch)
        sdk = MagicMock()
        sdk.subscribe_feeds.return_value = {"message": "ok"}
        monkeypatch.setattr(bwm, "_ensure_ws", lambda proc, user_id: sdk)

        ok = bwm.ensure_order_feed(MagicMock(), "u1")

        assert ok is True
        sdk.subscribe_feeds.assert_called_once_with(get_order_notification=True)

    def test_returns_false_without_a_broker_session(self, monkeypatch):
        _reset(monkeypatch)
        monkeypatch.setattr(bwm, "_ensure_ws", lambda proc, user_id: None)

        assert bwm.ensure_order_feed(MagicMock(), "u1") is False
