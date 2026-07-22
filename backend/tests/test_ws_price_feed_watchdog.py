"""Tests for the market-hours price-feed watchdog.

The failure this guards against: subscription bookkeeping records what we asked
ICICI for and is never reconciled against ticks arriving, so a subscribe that
silently produced no feed used to stay broken until the process restarted.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from icici_breeze_backend.app.core.timezone import IST
from icici_breeze_backend.app.services import breeze_websocket_manager as bwm
from icici_breeze_backend.app.services import ws_price_feed_watchdog as wd

_CHAIN = "NFO|NIFTY|30-Jul-2026"
_TOKENS = ["4.1!44684", "4.1!44685"]


@pytest.fixture(autouse=True)
def _reset():
    wd.reset_state_for_tests()
    yield
    wd.reset_state_for_tests()


@pytest.fixture
def env(monkeypatch):
    """Market open, one active chain, a connected socket, ticks controllable."""
    state = {
        "tick_age": 1.0,       # None = never ticked
        "spot_ticking": True,
        "forced_chains": [],
        "forced_spot": 0,
        "forced_order_feed": 0,
        "clock": {"t": 10_000.0},
    }

    # One controllable clock for the whole test, so the open pass and the silence
    # checks share a timeline (the throttle compares monotonic timestamps).
    monkeypatch.setattr(wd.time, "monotonic", lambda: state["clock"]["t"])
    monkeypatch.setattr(wd, "list_active_chains", lambda: [_CHAIN])
    monkeypatch.setattr(
        wd, "list_ws_stock_tokens_for_liquid_contracts", lambda *a, **k: list(_TOKENS)
    )
    monkeypatch.setattr(wd, "_newest_tick_age_seconds", lambda *a, **k: state["tick_age"])
    monkeypatch.setattr(wd, "_index_spot_ticking", lambda: state["spot_ticking"])
    monkeypatch.setattr(
        wd, "_force_chain", lambda chain_key: state["forced_chains"].append(chain_key)
    )
    monkeypatch.setattr(
        wd, "_force_index_spot", lambda: state.__setitem__("forced_spot", state["forced_spot"] + 1)
    )
    monkeypatch.setattr(
        wd,
        "_force_order_feed",
        lambda: state.__setitem__("forced_order_feed", state["forced_order_feed"] + 1),
    )
    monkeypatch.setattr(bwm, "_sdk", MagicMock())
    monkeypatch.setattr(bwm, "_connected", True)
    monkeypatch.setattr(bwm, "_sdk_user_id", "u1")
    return state


def _now(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 7, 21, hour, minute, second, tzinfo=IST)


def _mark_open_pass_done(env, monkeypatch):
    """Run the once-a-day open pass and settle past its throttle, so later
    assertions see only silence-driven work from a clean baseline."""
    wd.price_feed_watchdog_tick(_now(9, 15, 30))
    env["clock"]["t"] += wd._THROTTLE_SECONDS + 1
    wd._last_ok.clear()
    wd._last_forced.clear()
    env["forced_chains"].clear()
    env["forced_spot"] = 0
    env["forced_order_feed"] = 0


class TestMarketOpenPass:
    def test_forces_resubscribe_unconditionally_at_open(self, env, monkeypatch):
        """Fires even though ticks are flowing: whether ICICI honours a pre-open
        subscription is unverified, so the open pass removes the question."""
        env["tick_age"] = 1.0
        wd.price_feed_watchdog_tick(_now(9, 15, 30))
        assert env["forced_chains"] == [_CHAIN]
        assert env["forced_spot"] == 1
        assert env["forced_order_feed"] == 1

    def test_open_pass_runs_once_per_day(self, env, monkeypatch):
        wd.price_feed_watchdog_tick(_now(9, 15, 30))
        wd.price_feed_watchdog_tick(_now(9, 16, 0))
        assert env["forced_chains"] == [_CHAIN]

    def test_waits_for_settle_delay_after_the_bell(self, env, monkeypatch):
        """Subscribing in the same second the market opens is the case least likely
        to be honoured, so the pass waits out a short settle window."""
        wd.price_feed_watchdog_tick(_now(9, 15, 2))
        assert env["forced_chains"] == []
        wd.price_feed_watchdog_tick(_now(9, 15, 30))
        assert env["forced_chains"] == [_CHAIN]

    def test_open_pass_deferred_when_nobody_logged_in(self, env, monkeypatch):
        """With no broker session there is nothing subscribed and no session to
        subscribe with -- today's pass must stay unclaimed so it runs for real once
        someone logs in, rather than being silently burned at 9:15."""
        monkeypatch.setattr(bwm, "_connected", False)
        wd.price_feed_watchdog_tick(_now(9, 15, 30))
        assert env["forced_chains"] == []

        monkeypatch.setattr(bwm, "_connected", True)
        wd.price_feed_watchdog_tick(_now(10, 30, 0))
        assert env["forced_chains"] == [_CHAIN]


class TestSilenceTrigger:
    def test_no_action_while_ticks_are_flowing(self, env, monkeypatch):
        _mark_open_pass_done(env, monkeypatch)
        env["tick_age"] = 2.0
        wd.price_feed_watchdog_tick(_now(11, 0, 0))
        assert env["forced_chains"] == []

    def test_resubscribes_chain_after_silence_threshold(self, env, monkeypatch):
        _mark_open_pass_done(env, monkeypatch)
        env["tick_age"] = wd._SILENCE_SECONDS + 5
        wd.price_feed_watchdog_tick(_now(11, 0, 0))
        assert env["forced_chains"] == [_CHAIN]

    def test_chain_that_never_ticked_trips_on_watchdog_clock(self, env, monkeypatch):
        """`age is None` means no token has *ever* ticked -- the case a chain
        subscribed into a dead feed produces. It must still trip, so the silence
        clock falls back to how long the watchdog has been watching."""
        _mark_open_pass_done(env, monkeypatch)
        env["tick_age"] = None

        wd.price_feed_watchdog_tick(_now(11, 0, 0))
        assert env["forced_chains"] == []  # first sighting starts the clock

        env["clock"]["t"] += wd._SILENCE_SECONDS + 1
        wd.price_feed_watchdog_tick(_now(11, 1, 0))
        assert env["forced_chains"] == [_CHAIN]

    def test_throttled_between_forced_resubscribes(self, env, monkeypatch):
        _mark_open_pass_done(env, monkeypatch)
        env["tick_age"] = wd._SILENCE_SECONDS + 5

        wd.price_feed_watchdog_tick(_now(11, 0, 0))
        assert len(env["forced_chains"]) == 1

        env["clock"]["t"] += wd._THROTTLE_SECONDS - 5
        wd.price_feed_watchdog_tick(_now(11, 0, 55))
        assert len(env["forced_chains"]) == 1  # still throttled

        env["clock"]["t"] += 10
        wd.price_feed_watchdog_tick(_now(11, 1, 5))
        assert len(env["forced_chains"]) == 2

    def test_index_spot_resubscribed_when_silent(self, env, monkeypatch):
        _mark_open_pass_done(env, monkeypatch)
        env["spot_ticking"] = False

        wd.price_feed_watchdog_tick(_now(11, 0, 0))
        assert env["forced_spot"] == 0

        env["clock"]["t"] += wd._SILENCE_SECONDS + 1
        wd.price_feed_watchdog_tick(_now(11, 1, 0))
        assert env["forced_spot"] == 1


class TestGating:
    def test_noop_before_the_open(self, env, monkeypatch):
        env["tick_age"] = None
        env["spot_ticking"] = False
        wd.price_feed_watchdog_tick(_now(8, 30, 0))
        assert env["forced_chains"] == []
        assert env["forced_spot"] == 0

    def test_noop_after_the_close(self, env, monkeypatch):
        env["tick_age"] = None
        wd.price_feed_watchdog_tick(_now(16, 30, 0))
        assert env["forced_chains"] == []

    def test_noop_on_non_trading_day(self, env, monkeypatch):
        monkeypatch.setattr(
            "icici_breeze_backend.app.services.market_calendar.is_trading_day",
            lambda *a, **k: False,
        )
        wd.price_feed_watchdog_tick(_now(11, 0, 0))
        assert env["forced_chains"] == []

    def test_premarket_silence_does_not_count_towards_the_clock(self, env, monkeypatch):
        """Silence before the bell is expected. It must not accumulate, or the first
        in-hours pass would force immediately on a perfectly healthy feed."""
        env["tick_age"] = None
        # Claim today's open pass up front, so this exercises the silence path only.
        monkeypatch.setattr(wd, "_open_pass_date", _now(9, 15).date())

        wd.price_feed_watchdog_tick(_now(8, 30, 0))
        env["clock"]["t"] += 3600  # an hour of (expected) pre-open quiet
        wd.price_feed_watchdog_tick(_now(9, 16, 0))
        assert env["forced_chains"] == []  # clock restarted at the bell, not at 8:30

        env["clock"]["t"] += wd._SILENCE_SECONDS + 1
        wd.price_feed_watchdog_tick(_now(9, 17, 0))
        assert env["forced_chains"] == [_CHAIN]  # in-hours silence does count


class TestForceResubscribeTokens:
    def test_reissues_subscribe_for_already_held_tokens(self, monkeypatch):
        """The whole point: the normal path short-circuits on bookkeeping, so
        without a force path nothing can ever re-subscribe a live-but-dead token."""
        sdk = MagicMock()
        sdk.subscribe_feeds.return_value = {"Status": 200}
        monkeypatch.setattr(bwm, "_sdk", sdk)
        monkeypatch.setattr(bwm, "_connected", True)
        monkeypatch.setattr(bwm, "_sdk_user_id", "u1")
        monkeypatch.setattr(bwm, "_holders", {"h1": set(_TOKENS)})
        monkeypatch.setattr(bwm, "_sub_holders", {t: {"h1"} for t in _TOKENS})

        assert bwm.force_resubscribe_tokens(_TOKENS) is True
        sdk.subscribe_feeds.assert_called_once()
        assert sdk.subscribe_feeds.call_args.kwargs["stock_token"] == sorted(_TOKENS)

    def test_does_not_unsubscribe_first(self, monkeypatch):
        sdk = MagicMock()
        sdk.subscribe_feeds.return_value = {"Status": 200}
        monkeypatch.setattr(bwm, "_sdk", sdk)
        monkeypatch.setattr(bwm, "_connected", True)
        bwm.force_resubscribe_tokens(_TOKENS)
        sdk.unsubscribe_feeds.assert_not_called()

    def test_leaves_holder_bookkeeping_untouched(self, monkeypatch):
        sdk = MagicMock()
        sdk.subscribe_feeds.return_value = {"Status": 200}
        monkeypatch.setattr(bwm, "_sdk", sdk)
        monkeypatch.setattr(bwm, "_connected", True)
        holders = {"h1": set(_TOKENS)}
        sub_holders = {t: {"h1"} for t in _TOKENS}
        monkeypatch.setattr(bwm, "_holders", holders)
        monkeypatch.setattr(bwm, "_sub_holders", sub_holders)

        bwm.force_resubscribe_tokens(_TOKENS)
        assert holders == {"h1": set(_TOKENS)}
        assert sub_holders == {t: {"h1"} for t in _TOKENS}

    def test_returns_false_without_a_live_socket(self, monkeypatch):
        monkeypatch.setattr(bwm, "_sdk", None)
        monkeypatch.setattr(bwm, "_connected", False)
        assert bwm.force_resubscribe_tokens(_TOKENS) is False

    def test_reports_icici_error_status(self, monkeypatch):
        sdk = MagicMock()
        sdk.subscribe_feeds.return_value = {"Status": 500, "Error": "nope"}
        monkeypatch.setattr(bwm, "_sdk", sdk)
        monkeypatch.setattr(bwm, "_connected", True)
        assert bwm.force_resubscribe_tokens(_TOKENS) is False


class TestForcedChainSubscription:
    def test_force_resubscribes_tokens_the_holder_already_has(self, monkeypatch):
        """`sync_holder_chain_subscriptions(force=True)` must re-issue subscribes for
        tokens already recorded against the holder -- the login-path behaviour."""
        sdk = MagicMock()
        sdk.subscribe_feeds.return_value = {"Status": 200}
        proc = MagicMock()
        proc.get_session_breeze.return_value = sdk
        monkeypatch.setattr(bwm, "_sdk", sdk)
        monkeypatch.setattr(bwm, "_connected", True)
        monkeypatch.setattr(bwm, "_sdk_user_id", "u1")
        monkeypatch.setattr(bwm, "_holders", {"h1": set(_TOKENS)})
        monkeypatch.setattr(bwm, "_sub_holders", {t: {"h1"} for t in _TOKENS})
        monkeypatch.setattr(bwm, "_sub_meta", {t: {"stock_token": [t]} for t in _TOKENS})
        monkeypatch.setattr(
            bwm, "list_ws_stock_tokens_for_liquid_contracts", lambda *a, **k: list(_TOKENS)
        )
        monkeypatch.setattr(bwm, "register_holder_chain", lambda *a, **k: None)

        assert (
            bwm.sync_holder_chain_subscriptions(
                proc, "u1", "h1", "NIFTY", "NFO", "30-Jul-2026"
            )
            is True
        )
        sdk.subscribe_feeds.assert_not_called()  # normal path: bookkeeping says done

        assert (
            bwm.sync_holder_chain_subscriptions(
                proc, "u1", "h1", "NIFTY", "NFO", "30-Jul-2026", force=True
            )
            is True
        )
        sdk.subscribe_feeds.assert_called_once()
