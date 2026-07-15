"""Tests for the NIFTY/SENSEX system pre-subscription + navbar health status."""
from __future__ import annotations

import time
from datetime import date, timedelta

import pytest

from icici_breeze_backend.app.auth.context import get_broker_token_for_request
from icici_breeze_backend.app.services import breeze_websocket_manager as bwm
from icici_breeze_backend.app.services import index_spot_feed as isf
from icici_breeze_backend.app.services import system_chain_health as sch


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    sch.reset_state_for_tests()
    monkeypatch.setattr(sch, "_background_tasks", set())
    yield
    sch.reset_state_for_tests()


def _fake_underlyings(expiries: dict[str, list[str]]):
    def _get_underlyings(exchange_code):
        return [
            {"stock_code": stock, "expiry_dates": dates}
            for stock, dates in expiries.items()
        ]

    return _get_underlyings


class TestResolveNearestExpiry:
    def test_picks_nearest_expiry_on_or_after_today(self, monkeypatch):
        monkeypatch.setattr(
            sch,
            "get_underlyings",
            _fake_underlyings({"NIFTY": ["26-Jun-2026", "03-Jul-2026", "10-Jul-2026"]}),
        )
        expiry = sch.resolve_nearest_expiry("NFO", "NIFTY", today=date(2026, 6, 27))
        assert expiry == "03-Jul-2026"

    def test_includes_expiry_day_itself(self, monkeypatch):
        monkeypatch.setattr(
            sch,
            "get_underlyings",
            _fake_underlyings({"NIFTY": ["26-Jun-2026", "03-Jul-2026"]}),
        )
        expiry = sch.resolve_nearest_expiry("NFO", "NIFTY", today=date(2026, 6, 26))
        assert expiry == "26-Jun-2026"

    def test_no_future_expiry_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            sch,
            "get_underlyings",
            _fake_underlyings({"NIFTY": ["01-Jan-2020"]}),
        )
        assert sch.resolve_nearest_expiry("NFO", "NIFTY", today=date(2026, 6, 27)) is None

    def test_stock_code_not_found_returns_none(self, monkeypatch):
        monkeypatch.setattr(sch, "get_underlyings", _fake_underlyings({"BANKNIFTY": ["03-Jul-2026"]}))
        assert sch.resolve_nearest_expiry("NFO", "NIFTY", today=date(2026, 6, 27)) is None


_FUTURE_NIFTY_EXPIRY = (date.today() + timedelta(days=7)).strftime("%d-%b-%Y")
_FUTURE_SENSEX_EXPIRY = (date.today() + timedelta(days=6)).strftime("%d-%b-%Y")


class TestMaybeTriggerSystemPrefetch:
    def _stub_subscribe(self, monkeypatch, *, fail_once: bool = False, index_spot_ok: bool = True):
        calls: list[str] = []
        state = {"failed": False}

        def _sync(proc, user_id, holder_id, stock_code, exchange_code, expiry_display, strikes=None):
            if fail_once and not state["failed"]:
                state["failed"] = True
                raise RuntimeError("boom")
            calls.append(holder_id)
            return True

        monkeypatch.setattr(bwm, "sync_holder_chain_subscriptions", _sync)
        monkeypatch.setattr(bwm, "release_holder", lambda holder_id: {"released": 0, "holder_id": holder_id})
        # `_run_system_prefetch_blocking` imports this via a local `from ... import`,
        # so it must be patched on the real module, not on `sch`.
        monkeypatch.setattr(isf, "sync_index_spot_subscriptions", lambda proc, user_id: index_spot_ok)
        monkeypatch.setattr(
            sch,
            "get_underlyings",
            _fake_underlyings(
                {"NIFTY": [_FUTURE_NIFTY_EXPIRY], "BSESEN": [_FUTURE_SENSEX_EXPIRY]}
            ),
        )
        return calls

    def test_noop_when_market_closed(self, monkeypatch):
        calls = self._stub_subscribe(monkeypatch)
        monkeypatch.setattr(sch, "is_market_open", lambda *a, **k: False)
        sch.maybe_trigger_system_prefetch("u1")
        assert calls == []
        assert sch.prefetch_state()["date"] is None

    def test_subscribes_both_scrips_once(self, monkeypatch):
        calls = self._stub_subscribe(monkeypatch)
        monkeypatch.setattr(sch, "is_market_open", lambda *a, **k: True)
        sch.maybe_trigger_system_prefetch("u1")
        assert sorted(calls) == ["system:health:nifty", "system:health:sensex"]
        state = sch.prefetch_state()
        assert state["date"] == date.today()
        assert state["expiries"] == {"nifty": _FUTURE_NIFTY_EXPIRY, "sensex": _FUTURE_SENSEX_EXPIRY}

    def test_idempotent_same_day(self, monkeypatch):
        calls = self._stub_subscribe(monkeypatch)
        monkeypatch.setattr(sch, "is_market_open", lambda *a, **k: True)
        sch.maybe_trigger_system_prefetch("u1")
        sch.maybe_trigger_system_prefetch("u1")
        assert len(calls) == 2  # not 4 -- second call was a no-op

    def test_retries_after_cooldown_on_failure(self, monkeypatch):
        calls = self._stub_subscribe(monkeypatch, fail_once=True)
        monkeypatch.setattr(sch, "is_market_open", lambda *a, **k: True)

        fake_now = {"t": 1000.0}
        monkeypatch.setattr(time, "monotonic", lambda: fake_now["t"])

        sch.maybe_trigger_system_prefetch("u1")
        assert sch.prefetch_state()["last_error"] is not None
        assert len(calls) == 1  # sensex succeeded, nifty failed once

        # Within cooldown: no retry.
        sch.maybe_trigger_system_prefetch("u1")
        assert len(calls) == 1

        # Past cooldown: retries and this time nifty succeeds too.
        fake_now["t"] += sch._RETRY_COOLDOWN_SECONDS + 1
        sch.maybe_trigger_system_prefetch("u1")
        assert len(calls) == 3
        assert sch.prefetch_state()["last_error"] is None

    def test_index_spot_failure_recorded_and_retried(self, monkeypatch):
        """Regression test: a dead broker session on the first trigger of the day
        must not permanently mark the day as 'done' -- the daily guard used to
        get set unconditionally even when the index-spot subscribe silently
        failed (`sync_index_spot_subscriptions` returning False), so a session
        that recovered later in the day never got retried until midnight IST."""
        calls = self._stub_subscribe(monkeypatch, index_spot_ok=False)
        monkeypatch.setattr(sch, "is_market_open", lambda *a, **k: True)

        fake_now = {"t": 1000.0}
        monkeypatch.setattr(time, "monotonic", lambda: fake_now["t"])

        sch.maybe_trigger_system_prefetch("u1")
        state = sch.prefetch_state()
        assert state["last_error"] == "index-spot: no live broker session"
        assert sorted(calls) == ["system:health:nifty", "system:health:sensex"]

        # Within cooldown: no retry at all (holder chains didn't re-run either).
        sch.maybe_trigger_system_prefetch("u1")
        assert len(calls) == 2

        # Session recovers; past cooldown, the whole prefetch retries and clears.
        monkeypatch.setattr(isf, "sync_index_spot_subscriptions", lambda proc, user_id: True)
        fake_now["t"] += sch._RETRY_COOLDOWN_SECONDS + 1
        sch.maybe_trigger_system_prefetch("u1")
        assert sch.prefetch_state()["last_error"] is None
        assert len(calls) == 4


class TestGetSystemHealthStatus:
    def _base_prefetch(self, monkeypatch, *, subscribed_seconds_ago: float = 60.0):
        monkeypatch.setattr(sch, "_prefetch_date", date.today())
        monkeypatch.setattr(
            sch, "_prefetch_expiries", {"nifty": "03-Jul-2026", "sensex": "02-Jul-2026"}
        )
        now_mono = time.monotonic()
        monkeypatch.setattr(
            sch,
            "_prefetch_subscribed_at_monotonic",
            {"nifty": now_mono - subscribed_seconds_ago, "sensex": now_mono - subscribed_seconds_ago},
        )
        monkeypatch.setattr(
            sch,
            "list_ws_stock_tokens_for_liquid_contracts",
            lambda exchange_code, stock_code, expiry_display: ["4.1!1"] if stock_code == "NIFTY" else ["8.1!1"],
        )

    def test_market_closed_is_gray(self, monkeypatch):
        monkeypatch.setattr(sch, "is_market_open", lambda *a, **k: False)
        monkeypatch.setattr(sch, "market_closed_reason", lambda *a, **k: "weekend")
        result = sch.get_system_health_status()
        assert result["status"] == "gray"
        assert "weekend" in result["reason"]
        assert result["market_open"] is False

    def test_market_open_waiting_for_login_is_gray(self, monkeypatch):
        monkeypatch.setattr(sch, "is_market_open", lambda *a, **k: True)
        result = sch.get_system_health_status()
        assert result["status"] == "gray"
        assert "waiting for first login" in result["reason"]
        assert result["prefetch_done"] is False

    def test_disconnected_is_red(self, monkeypatch):
        monkeypatch.setattr(sch, "is_market_open", lambda *a, **k: True)
        self._base_prefetch(monkeypatch)
        monkeypatch.setattr(bwm, "get_playground_status", lambda: {"connected": False, "last_error": "boom"})
        result = sch.get_system_health_status()
        assert result["status"] == "red"
        assert "disconnected" in result["reason"]

    def test_fresh_ticks_both_scrips_is_green(self, monkeypatch):
        monkeypatch.setattr(sch, "is_market_open", lambda *a, **k: True)
        self._base_prefetch(monkeypatch)
        monkeypatch.setattr(bwm, "get_playground_status", lambda: {"connected": True, "last_error": None})
        monkeypatch.setattr(sch, "cache_get_json", lambda key: {"received_at": time.time()})
        result = sch.get_system_health_status()
        assert result["status"] == "green"
        assert result["detail"]["nifty"]["stale"] is False
        assert result["detail"]["sensex"]["stale"] is False

    def test_one_scrip_stale_is_red_naming_it(self, monkeypatch):
        monkeypatch.setattr(sch, "is_market_open", lambda *a, **k: True)
        self._base_prefetch(monkeypatch)
        monkeypatch.setattr(bwm, "get_playground_status", lambda: {"connected": True, "last_error": None})

        def _cache_get_json(key):
            if "NFO" in key:
                return {"received_at": time.time() - 120}  # NIFTY stale
            return {"received_at": time.time()}  # SENSEX fresh

        monkeypatch.setattr(sch, "cache_get_json", _cache_get_json)
        result = sch.get_system_health_status()
        assert result["status"] == "red"
        assert "NIFTY" in result["reason"]
        assert "SENSEX" not in result["reason"]
        assert result["detail"]["nifty"]["stale"] is True
        assert result["detail"]["sensex"]["stale"] is False

    def test_no_ticks_yet_within_warmup_is_gray(self, monkeypatch):
        monkeypatch.setattr(sch, "is_market_open", lambda *a, **k: True)
        self._base_prefetch(monkeypatch, subscribed_seconds_ago=1.0)
        monkeypatch.setattr(bwm, "get_playground_status", lambda: {"connected": True, "last_error": None})
        monkeypatch.setattr(sch, "cache_get_json", lambda key: None)
        result = sch.get_system_health_status()
        assert result["status"] == "gray"
        assert "subscribing" in result["reason"]

    def test_no_ticks_past_warmup_is_red(self, monkeypatch):
        monkeypatch.setattr(sch, "is_market_open", lambda *a, **k: True)
        self._base_prefetch(monkeypatch, subscribed_seconds_ago=999.0)
        monkeypatch.setattr(bwm, "get_playground_status", lambda: {"connected": True, "last_error": None})
        monkeypatch.setattr(sch, "cache_get_json", lambda key: None)
        result = sch.get_system_health_status()
        assert result["status"] == "red"


class TestStartPrefetchForNewBrokerSession:
    """The login-path trigger (called from `_complete_icici_session` in
    app/api/v1/home.py) is a deterministic alternative to the auth/context.py
    hook -- fires at the moment ICICI OAuth completes, before Ts&Cs."""

    def test_sets_broker_token_contextvar_before_triggering(self, monkeypatch):
        seen_token = {}

        def _fake_trigger(user_id):
            seen_token["value"] = get_broker_token_for_request()
            seen_token["user_id"] = user_id

        monkeypatch.setattr(sch, "maybe_trigger_system_prefetch", _fake_trigger)
        sch.start_prefetch_for_new_broker_session("user1", "broker-tok-123")
        assert seen_token == {"value": "broker-tok-123", "user_id": "user1"}

    def test_exception_from_trigger_does_not_propagate(self, monkeypatch):
        def _raise(user_id):
            raise RuntimeError("boom")

        monkeypatch.setattr(sch, "maybe_trigger_system_prefetch", _raise)
        # Must not raise -- login must never break because of this side path.
        sch.start_prefetch_for_new_broker_session("user1", "broker-tok-123")
