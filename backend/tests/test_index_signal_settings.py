"""Tests for Settings -> Index Signal: the settings store, its routes, and how the running
publisher applies a change (switch-off unsubscribes, tau restarts the smoother, top N re-baskets).

conftest points the settings row at a per-test temp DB, so every test starts from the defaults.
"""
from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import replace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from icici_breeze_backend.app.api.v1 import route_settings
from icici_breeze_backend.app.auth.context import RequestContext
from icici_breeze_backend.app.db.redis_client import cache_delete_pattern
from icici_breeze_backend.app.domain.settings_api import IndexSignalPreferencesUpdateBody
from icici_breeze_backend.app.services import breeze_websocket_manager as bwm
from icici_breeze_backend.app.services.index_signal import (
    depth_feed,
    publisher,
    reader,
    shadow_log,
    weights,
)
from icici_breeze_backend.app.services.index_signal import settings as signal_settings
from icici_breeze_backend.app.services.index_signal.engine import Constituent
from icici_breeze_backend.app.services.index_signal.settings import (
    BOUNDS,
    DEFAULTS,
    IndexSignalSettings,
)


def _ctx() -> RequestContext:
    return RequestContext(user_id="uid1", username="uid1", roles=["trader"], is_authenticated=True, broker_token="tok")


@pytest.fixture(autouse=True)
def _reset(tmp_path, monkeypatch):
    monkeypatch.setattr(shadow_log, "_db_path", lambda: str(tmp_path / "users_test.sqlite3"))
    monkeypatch.setattr(weights, "_db_path", lambda: str(tmp_path / "users_test.sqlite3"))
    for mod in (depth_feed, publisher, shadow_log, weights):
        mod.reset_state_for_tests()
    cache_delete_pattern("signal:index:*")
    yield
    for mod in (depth_feed, publisher, shadow_log, weights):
        mod.reset_state_for_tests()
    cache_delete_pattern("signal:index:*")


class TestStore:
    def test_defaults_are_seeded_and_agree_with_the_bounds(self):
        assert signal_settings.load_index_signal_settings() == DEFAULTS
        for name, bound in BOUNDS.items():
            assert getattr(DEFAULTS, name) == bound.default, name
            assert bound.min <= bound.recommended_min <= bound.recommended_max <= bound.max, name

    def test_partial_save_keeps_the_other_fields(self):
        saved = signal_settings.save_index_signal_settings(enter_threshold=0.35)
        assert saved.enter_threshold == 0.35
        assert saved.exit_threshold == DEFAULTS.exit_threshold
        assert signal_settings.load_index_signal_settings() == saved

    def test_out_of_bounds_is_rejected_and_nothing_is_written(self):
        with pytest.raises(ValueError, match="top_n must be between 3 and 15"):
            signal_settings.save_index_signal_settings(top_n=40)
        assert signal_settings.load_index_signal_settings().top_n == DEFAULTS.top_n

    def test_exit_may_not_exceed_enter(self):
        with pytest.raises(ValueError, match="exit_threshold must not exceed enter_threshold"):
            signal_settings.save_index_signal_settings(exit_threshold=0.5)
        saved = signal_settings.save_index_signal_settings(enter_threshold=0.6, exit_threshold=0.5)
        assert (saved.enter_threshold, saved.exit_threshold) == (0.6, 0.5)

    def test_types_are_checked(self):
        with pytest.raises(ValueError, match="whole number"):
            signal_settings.save_index_signal_settings(top_n=8.5)
        assert signal_settings.save_index_signal_settings(top_n=8.0).top_n == 8
        with pytest.raises(ValueError, match="true or false"):
            signal_settings.save_index_signal_settings(enabled="yes")
        with pytest.raises(ValueError, match="unknown"):
            signal_settings.save_index_signal_settings(nope=1)

    def test_switching_off_persists(self):
        signal_settings.save_index_signal_settings(enabled=False)
        assert signal_settings.load_index_signal_settings().enabled is False

    def test_warmup_is_two_time_constants(self):
        assert IndexSignalSettings(tau_seconds=4.0).signal_params().warmup_seconds == 8.0

    def test_an_out_of_range_stored_row_is_clamped_on_read(self):
        signal_settings.load_index_signal_settings()  # seeds the row
        with sqlite3.connect(signal_settings._db_path()) as conn:
            conn.execute(
                "UPDATE index_signal_settings SET top_n = 99, exit_threshold = 0.8 WHERE id = 1"
            )
            conn.commit()
        loaded = signal_settings.load_index_signal_settings()
        assert loaded.top_n == 15
        assert loaded.exit_threshold == loaded.enter_threshold


class TestRoutes:
    def test_get_returns_the_settings_and_their_bounds(self):
        resp = asyncio.run(route_settings.settings_index_signal_preferences_get(_ctx()))
        assert resp.enabled is True
        assert resp.top_n == 10
        assert set(resp.bounds) == set(BOUNDS)
        assert resp.bounds["enter_threshold"].recommended_max == 0.40
        assert resp.bounds["top_n"].integer is True

    def test_put_is_partial_and_persists(self):
        body = IndexSignalPreferencesUpdateBody(tau_seconds=4.0, enabled=False)
        resp = asyncio.run(route_settings.settings_index_signal_preferences_put(body, _ctx()))
        assert (resp.tau_seconds, resp.enabled, resp.top_n) == (4.0, False, 10)
        again = asyncio.run(route_settings.settings_index_signal_preferences_get(_ctx()))
        assert (again.tau_seconds, again.enabled) == (4.0, False)

    def test_put_out_of_bounds_is_a_422(self):
        body = IndexSignalPreferencesUpdateBody(min_coverage=0.1)
        with pytest.raises(HTTPException) as exc:
            asyncio.run(route_settings.settings_index_signal_preferences_put(body, _ctx()))
        assert exc.value.status_code == 422
        assert "min_coverage" in exc.value.detail

    def test_unknown_fields_are_refused_by_the_body_model(self):
        with pytest.raises(Exception):
            IndexSignalPreferencesUpdateBody(surprise=1)

    def test_weights_route_lists_both_indices_at_the_configured_top_n(self, monkeypatch):
        signal_settings.save_index_signal_settings(top_n=8)
        monkeypatch.setattr(
            weights, "weights_overview", lambda label, top_n, **_kw: {"label": label, "top_n": top_n}
        )
        resp = asyncio.run(route_settings.settings_index_signal_weights_get(_ctx()))
        assert resp["refreshing"] is False
        assert resp["indices"] == {
            "nifty": {"label": "nifty", "top_n": 8},
            "sensex": {"label": "sensex", "top_n": 8},
        }

    def test_refresh_route_forces_a_background_refresh(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            weights,
            "refresh_due_weights_in_background",
            lambda *a, **kw: calls.append(kw) or True,
        )
        resp = asyncio.run(route_settings.settings_index_signal_weights_refresh(_ctx()))
        assert resp["started"] is True
        assert calls == [{"force": True}]

    def test_shadow_report_route(self):
        resp = asyncio.run(
            route_settings.settings_index_signal_shadow_report(days=5, min_move_bps=2.0, ctx=_ctx())
        )
        assert resp["days"] == 5
        assert resp["min_move_bps"] == 2.0
        assert set(resp["indices"]) == {"nifty", "sensex"}
        assert resp["indices"]["nifty"]["samples"] == 0
        assert resp["indices"]["nifty"]["min_move_bps"] == 2.0

    def test_readings_download_route_serves_csv(self):
        resp = asyncio.run(route_settings.settings_index_signal_readings_download(index="NIFTY", days=5, ctx=_ctx()))
        assert resp.media_type.startswith("text/csv")
        assert resp.body.decode().startswith("time_ist,state,")
        assert 'attachment; filename="nifty-signal-readings-' in resp.headers["content-disposition"]

    def test_readings_download_route_rejects_an_unknown_index(self):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(route_settings.settings_index_signal_readings_download(index="banknifty", days=5, ctx=_ctx()))
        assert exc.value.status_code == 400

    def test_weights_overview_reports_the_basket_share(self, monkeypatch):
        monkeypatch.setattr(
            weights,
            "tracked_constituents",
            lambda label, top_n, **_kw: (
                (Constituent("A", "AA", 30.0), Constituent("B", "BB", 10.0)),
                {"source": "seed", "as_of": "2026-09-10", "fetched_at": None, "unresolved": ["X"]},
            ),
        )
        view = weights.weights_overview("nifty", 10)
        assert [row["basket_share"] for row in view["tracked"]] == [75.0, 25.0]
        assert view["unresolved"] == ["X"]
        assert view["source"] == "seed"


_BASKET = (
    Constituent("HDFCBANK", "HDFBAN", 50.0),
    Constituent("ICICIBANK", "ICIBAN", 30.0),
    Constituent("RELIANCE", "RELIND", 20.0),
)


@pytest.fixture
def basket(monkeypatch):
    monkeypatch.setattr(
        weights,
        "tracked_constituents",
        lambda label, top_n, **_kw: (
            _BASKET[:top_n],
            {"source": "test", "as_of": "2026-09-11", "fetched_at": None, "unresolved": []},
        ),
    )


class TestPublisherAppliesSettings:
    def test_switching_off_unsubscribes_and_publishes_disabled(self, monkeypatch):
        dropped = []
        monkeypatch.setattr(depth_feed, "has_subscriptions", lambda: True)
        monkeypatch.setattr(depth_feed, "unsubscribe_all", lambda: dropped.append(1))
        signal_settings.save_index_signal_settings(enabled=False)

        publisher._loop_tick(2.0)

        assert dropped == [1]
        read = reader.get_index_signal("nifty")
        assert (read["state"], read["reason"]) == ("unavailable", "disabled")
        assert reader.navbar_view()["sensex"]["reason"] == "disabled"

    def test_switching_back_on_resubscribes_on_the_next_loop(self, basket, monkeypatch):
        synced = []
        monkeypatch.setattr(bwm, "current_ws_user_id", lambda: "u1")
        monkeypatch.setattr(
            "icici_breeze_backend.app.services.market_calendar.is_trading_day", lambda *a, **k: True
        )
        monkeypatch.setattr("icici_breeze_backend.app.services.processor.processor", MagicMock())
        monkeypatch.setattr(weights, "refresh_due_weights_in_background", lambda *a, **k: False)
        monkeypatch.setattr(
            publisher, "ensure_depth_feed", lambda proc, user_id, force=False: synced.append(user_id) or True
        )

        signal_settings.save_index_signal_settings(enabled=False)
        publisher._loop_tick(2.0)
        signal_settings.save_index_signal_settings(enabled=True)
        publisher._loop_tick(2.0)

        assert synced == ["u1"]
        assert reader.get_index_signal("nifty")["reason"] != "disabled"

    def test_a_new_tau_restarts_the_smoother(self, basket):
        publisher.apply_weights_if_needed(force=True, top_n=3)
        for short_name in ("HDFBAN", "ICIBAN", "RELIND"):
            publisher._on_book("NSE", short_name, 1500.0, 500.0, 100.0)
        eng = publisher._engine("nifty")
        assert eng.snapshot(101.0, session_open=True)["signal"] is not None

        publisher.apply_runtime_settings(replace(DEFAULTS, tau_seconds=5.0))
        snap = eng.snapshot(102.0, session_open=True)
        assert snap["signal"] is None
        assert snap["reason"] == "warming_up"
        assert snap["tau_seconds"] == 5.0

    def test_thresholds_change_without_restarting_the_smoother(self, basket):
        publisher.apply_weights_if_needed(force=True, top_n=3)
        for short_name in ("HDFBAN", "ICIBAN", "RELIND"):
            publisher._on_book("NSE", short_name, 1500.0, 500.0, 100.0)
        publisher.apply_runtime_settings(replace(DEFAULTS, enter_threshold=0.6, exit_threshold=0.4))
        snap = publisher._engine("nifty").snapshot(101.0, session_open=True)
        assert snap["signal"] == pytest.approx(0.5)
        assert snap["thresholds"] == {"enter": 0.6, "exit": 0.4}

    def test_depth_levels_and_retention_are_pushed_to_the_feed_and_log(self):
        publisher.apply_runtime_settings(replace(DEFAULTS, depth_levels=3, shadow_retention_days=30))
        assert depth_feed._levels() == 3
        assert shadow_log._retention_days() == 30

    def test_a_smaller_top_n_shrinks_the_basket_and_the_subscriptions(self, basket):
        publisher.apply_weights_if_needed(top_n=3)
        assert len(publisher.depth_targets()) == 6
        assert publisher.apply_weights_if_needed(top_n=2) is True
        assert publisher.depth_targets() == [
            ("NSE", "HDFBAN"),
            ("NSE", "ICIBAN"),
            ("BSE", "HDFBAN"),
            ("BSE", "ICIBAN"),
        ]
