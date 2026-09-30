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


class TestDroppedOrderSocket:
    """B-37: a dropped order socket does not clear the SDK's `orderconnect`, so re-arming was
    a no-op and nothing reconnected it."""

    def _sdk(self, connected: bool):
        from types import SimpleNamespace

        client = MagicMock()
        client.connected = connected
        sdk = MagicMock()
        sdk.orderconnect = 1
        sdk.sio_order_refresh_handler = SimpleNamespace(sio=client)
        return sdk, client

    def test_a_socket_down_on_two_passes_is_reset_then_reconnected(self, monkeypatch):
        _reset(monkeypatch)
        monkeypatch.setattr(bwm, "_order_socket_down_passes", 0)
        sdk, client = self._sdk(connected=False)
        monkeypatch.setattr(bwm, "_sdk", sdk)
        monkeypatch.setattr(bwm, "_connected", True)

        bwm.order_feed_watchdog_tick()
        assert sdk.orderconnect == 1, "one pass may just be socket.io reconnecting"

        bwm.order_feed_watchdog_tick()
        client.disconnect.assert_called_once()
        assert sdk.orderconnect == 0 and sdk.sio_order_refresh_handler is None
        assert sdk.subscribe_feeds.call_count == 2

    def test_a_connected_socket_is_left_alone(self, monkeypatch):
        _reset(monkeypatch)
        monkeypatch.setattr(bwm, "_order_socket_down_passes", 0)
        sdk, client = self._sdk(connected=True)
        monkeypatch.setattr(bwm, "_sdk", sdk)
        monkeypatch.setattr(bwm, "_connected", True)
        for _ in range(3):
            bwm.order_feed_watchdog_tick()
        client.disconnect.assert_not_called()
        assert sdk.orderconnect == 1


def test_idle_lookup_subscriptions_are_released(monkeypatch):
    """B-41: holder-less lookups subscribed tokens that were never released."""
    _reset(monkeypatch)
    monkeypatch.setattr(bwm, "_holders", {bwm._UNTRACKED_HOLDER: {"4.1!1", "4.1!2"}, "sg:r1": {"4.1!2"}})
    monkeypatch.setattr(bwm, "_sub_holders", {"4.1!1": {bwm._UNTRACKED_HOLDER}, "4.1!2": {bwm._UNTRACKED_HOLDER, "sg:r1"}})
    monkeypatch.setattr(bwm, "_sub_meta", {"4.1!1": {"stock_token": ["4.1!1"]}, "4.1!2": {"stock_token": ["4.1!2"]}})
    monkeypatch.setattr(bwm, "_untracked_seen", {"4.1!1": 0.0, "4.1!2": 0.0})
    unsubscribed = []
    monkeypatch.setattr(bwm, "_icici_unsubscribe_stock_token", lambda token, meta: unsubscribed.append(token))

    assert bwm.release_idle_lookups(now=100.0) == 0, "not idle yet"
    assert bwm.release_idle_lookups(now=bwm._UNTRACKED_IDLE_SECONDS + 1) == 2
    assert unsubscribed == ["4.1!1"], "a contract an SG still holds stays subscribed"
    assert bwm._sub_holders["4.1!2"] == {"sg:r1"}
