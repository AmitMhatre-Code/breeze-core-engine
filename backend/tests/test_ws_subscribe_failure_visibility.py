"""Regression tests for the two defects that let a dead option-chain feed report
success for an hour in production (24-Jul-2026).

1. `breeze_connect.subscribe_feeds` never raises -- it catches every exception and
   *returns* the message as a plain string. The old check only inspected `dict`
   results, so a rejected subscribe was recorded as subscribed, logged `ok=True`
   once a minute, and left `last_error: null`.

2. `SocketEventBreeze.authentication` is a one-way latch: any socket.io
   `connect_error` sets it False and nothing in the SDK ever sets it back, so one
   transient blip makes `subscribe_feeds` refuse forever ("Could not authenticate
   credentials...") while rooms joined before the blip keep streaming.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from icici_breeze_backend.app.services import breeze_websocket_manager as bwm

_AUTH_LATCH_ERROR = (
    "Exception while subscribing to feeds Could not authenticate credentials. "
    "Please check token and keys"
)


def _reset(monkeypatch) -> None:
    monkeypatch.setattr(bwm, "_holders", {})
    monkeypatch.setattr(bwm, "_sub_holders", {})
    monkeypatch.setattr(bwm, "_sub_meta", {})
    monkeypatch.setattr(bwm, "_last_error", None)


def _connected_sdk(monkeypatch, subscribe_result):
    sdk = MagicMock()
    sdk.subscribe_feeds.return_value = subscribe_result
    _reset(monkeypatch)
    monkeypatch.setattr(bwm, "_sdk", sdk)
    monkeypatch.setattr(bwm, "_connected", True)
    monkeypatch.setattr(bwm, "_sdk_user_id", "u1")
    return sdk


class TestSubscribeFeedsErrorDetection:
    def test_string_return_is_a_failure_not_a_success(self):
        assert bwm._subscribe_feeds_error(_AUTH_LATCH_ERROR) == _AUTH_LATCH_ERROR

    def test_none_return_is_a_failure(self):
        assert bwm._subscribe_feeds_error(None) is not None

    def test_dict_response_without_status_is_success(self):
        # The SDK's success shape: socket_connection_response -> {"message": ...}
        assert bwm._subscribe_feeds_error({"message": "Stock subscribed"}) is None

    def test_dict_with_error_status_is_a_failure(self):
        assert bwm._subscribe_feeds_error({"Status": 500, "Error": "nope"}) == "nope"

    def test_unknown_shape_is_treated_as_success(self):
        # Deliberately narrow: only the SDK's real failure shapes count as failures,
        # so a MagicMock in a test fake isn't misread as a dead feed.
        assert bwm._subscribe_feeds_error(MagicMock()) is None


class TestForcedResubscribeReportsFailure:
    def test_rejected_subscribe_is_not_reported_as_ok(self, monkeypatch):
        """The watchdog's `ok=True` on a dead feed is what hid the outage."""
        _connected_sdk(monkeypatch, _AUTH_LATCH_ERROR)

        assert bwm.force_resubscribe_tokens(["4.1!63939", "4.1!63940"]) is False
        assert bwm._last_error is not None
        assert "authenticate" in bwm._last_error

    def test_rejected_subscribe_is_not_recorded_as_subscribed(self, monkeypatch):
        """Bookkeeping must not claim tokens ICICI refused -- `active_subscriptions`
        read 407 while nothing was actually joined."""
        sdk = _connected_sdk(monkeypatch, _AUTH_LATCH_ERROR)
        proc = MagicMock()
        proc.get_session_breeze.return_value = sdk

        ok = bwm._subscribe_stock_token_batch(
            proc, "u1", ["4.1!63939"], holder_id="h1", force=True
        )

        assert ok is False
        assert bwm.get_playground_status()["active_subscriptions"] == 0

    def test_successful_subscribe_still_reports_ok(self, monkeypatch):
        _connected_sdk(monkeypatch, {"message": "Stock subscribed"})
        assert bwm.force_resubscribe_tokens(["4.1!63939"]) is True
        assert bwm._last_error is None


class TestStaleAuthLatch:
    def test_latch_is_cleared_before_subscribing(self, monkeypatch):
        """One transient connect_error must not deafen every future subscribe."""
        sdk = _connected_sdk(monkeypatch, {"message": "Stock subscribed"})
        sdk.sio_rate_refresh_handler.authentication = False

        assert bwm.force_resubscribe_tokens(["4.1!63939"]) is True
        assert sdk.sio_rate_refresh_handler.authentication is True

    def test_latch_cleared_for_the_order_feed_too(self, monkeypatch):
        sdk = _connected_sdk(monkeypatch, {"message": "Order notification subscribed"})
        sdk.sio_rate_refresh_handler.authentication = False

        bwm.order_feed_watchdog_tick()

        assert sdk.sio_rate_refresh_handler.authentication is True

    def test_healthy_latch_is_left_alone(self, monkeypatch):
        sdk = _connected_sdk(monkeypatch, {"message": "Stock subscribed"})
        sdk.sio_rate_refresh_handler.authentication = True

        bwm.force_resubscribe_tokens(["4.1!63939"])

        assert sdk.sio_rate_refresh_handler.authentication is True

    def test_missing_handler_does_not_raise(self, monkeypatch):
        sdk = _connected_sdk(monkeypatch, {"message": "Stock subscribed"})
        sdk.sio_rate_refresh_handler = None

        assert bwm.force_resubscribe_tokens(["4.1!63939"]) is True


class TestOrderFeedFailureIsVisible:
    def test_rejected_order_subscribe_is_recorded(self, monkeypatch):
        """"Subscribed to ICICI order notifications" used to be logged
        unconditionally, so a deaf SG lifecycle looked healthy."""
        _connected_sdk(monkeypatch, _AUTH_LATCH_ERROR)

        bwm.order_feed_watchdog_tick()

        assert bwm._last_error is not None
        assert "get_order_notification" in bwm._last_error
