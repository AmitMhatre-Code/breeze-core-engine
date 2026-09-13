"""Bots -> Backtest: the shared service, the background jobs and the routes (plan 8.11).

What these pin: fetching is refused off the live broker and in market hours, and a running
fetch stops itself when the market opens; one job runs at a time; the fly and Bot 2 size from
one lot of today's margin against their saved ceiling or share; Bot 2 runs only the saved
shortlist of enabled indices; every run is kept with the settings it ran on, and a restart
never leaves one spinning.
"""
from __future__ import annotations

import datetime
import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from icici_breeze_backend.app.api.v1.route_bots_backtest import router
from icici_breeze_backend.app.auth.context import RequestContext, get_request_context
from icici_breeze_backend.app.db.bots_migrate import ensure_bots_tables
from icici_breeze_backend.app.domain.bots import (
    ExpiryIndexWriterConfig,
    IndexWriterLeg,
    IronFlyScalperConfig,
    MomentumLongScalperConfig,
)
from icici_breeze_backend.app.repositories import bots as repo
from icici_breeze_backend.app.services.bots import backtest_jobs as jobs
from icici_breeze_backend.app.services.bots import backtest_service as service
from icici_breeze_backend.app.services.bots import charges as charges_mod
from icici_breeze_backend.app.services.bots.scalping import backtest_regime as regime
from icici_breeze_backend.app.services.bots.scalping import backtest_store as store
from icici_breeze_backend.app.services.bots.scalping import spreads
from icici_breeze_backend.app.services.bots.scalping.backtest_fetch import Fetcher, Stopped
from icici_breeze_backend.app.services.bots.scalping.backtest_options import OK, ModelPricer
from icici_breeze_backend.app.services.bots.scalping.backtest_store import HistCandle

D = datetime.date
MIN = datetime.timedelta(minutes=1)
_TS = "%Y-%m-%d %H:%M:%S"


@pytest.fixture
def env(tmp_path, monkeypatch):
    users = str(tmp_path / "users.sqlite3")
    cache = str(tmp_path / "backtest.sqlite3")
    monkeypatch.setattr(repo, "_db_path", lambda: users)
    monkeypatch.setattr(spreads, "_db_path", lambda: users)
    monkeypatch.setattr(charges_mod, "_db_path", lambda: users)
    monkeypatch.setattr(store, "db_path", lambda: cache)
    monkeypatch.setattr(jobs, "_store_ready", False)
    monkeypatch.setattr(service, "holidays", lambda: set())
    ensure_bots_tables(users)
    store.ensure_tables(cache)
    yield {"users": users, "cache": cache}
    if jobs._thread is not None:
        jobs._thread.join(timeout=10)


def _rows(day, closes, volumes, start=datetime.time(9, 15)):
    base = datetime.datetime.combine(day, start)
    return [
        {"datetime": (base + i * MIN).strftime(_TS), "open": c, "high": c, "low": c, "close": c, "volume": v}
        for i, (c, v) in enumerate(zip(closes, volumes))
    ]


def _cache_trending(cache, day=D(2026, 3, 9)):
    closes = [24_000.0] * 25 + [24_000.0 + 12 * i for i in range(1, 40)]
    volumes = [1_000] * 25 + [90_000] * 39
    store.store_candles(_rows(day, closes, volumes), path=cache)


def _wait_for_job():
    if jobs._thread is not None:
        jobs._thread.join(timeout=30)
    return jobs.state()


# --- the service ------------------------------------------------------------------------


def test_the_range_floors_at_history_start_and_defaults_to_yesterday():
    assert service.clip_range(None, None, D(2026, 9, 14)) == (regime.HISTORY_START, D(2026, 9, 13))
    assert service.clip_range(D(2025, 6, 1), D(2026, 2, 1), D(2026, 9, 14))[0] == regime.HISTORY_START
    with pytest.raises(ValueError):
        service.clip_range(D(2026, 3, 5), D(2026, 3, 1), D(2026, 9, 14))


def test_bot2_scope_is_the_enabled_indices_with_their_shortlists_in_priority_order():
    config = ExpiryIndexWriterConfig(
        indices={
            "NIFTY": IndexWriterLeg(enabled=True, strategies=["naked_pe", "short_strangle"], priority=2),
            "BSESEN": IndexWriterLeg(enabled=True, priority=1),
        }
    )
    scopes = service.expiry_scope(config)
    assert [s.index for s in scopes] == ["BSESEN", "NIFTY"]
    assert scopes[1].strategies == ("naked_pe", "short_strangle")
    assert service.expiry_scope(ExpiryIndexWriterConfig()) == []  # both ship disabled


class _Flat:
    real = True
    source = "scripted"

    def bar(self, key, minute, *, spot=0.0, sigma=0.0):
        return OK, HistCandle(minute, 10.0, 10.0, 10.0, 10.0, 10)


def test_bot2_runs_each_strategy_at_its_own_lots_and_skips_a_zero(env):
    day = D(2026, 3, 10)
    store.store_candles(_rows(day, [24_000.0] * 375, [0] * 375), stock_code="NIFTY",
                        table="spot_candles", path=env["cache"])
    scopes = [service.Scope("NIFTY", ("naked_pe", "short_strangle"), {"naked_pe": 3, "short_strangle": 0})]
    result = service.replay(
        "expiry", start=day, end=day, config=ExpiryIndexWriterConfig(), pricer=_Flat(),
        scopes=scopes, holidays_=set(), path=env["cache"],
    )
    assert [t.strategy for t in result.trades] == ["naked_pe"]
    assert result.trades[0].lots == 3 and result.trades[0].quantity == 3 * 65
    assert result.lots == {"NIFTY naked_pe": 3, "NIFTY short_strangle": 0}


def test_scalper_replays_say_when_nothing_is_cached(env):
    with pytest.raises(service.NoCachedData):
        service.replay(
            "momentum", start=D(2026, 3, 2), end=D(2026, 3, 6), config=MomentumLongScalperConfig(),
            pricer=ModelPricer(), holidays_=set(), path=env["cache"],
        )


class _Proc:
    def fetch_lot_size(self, *a, **k):
        return 65


def _cache_spot(cache, index="NIFTY", close=25_010.0):
    store.store_candles([{"datetime": "2026-09-11 15:29:00", "close": close, "volume": 0}],
                        stock_code=index, table="spot_candles", path=cache)


def test_fly_lots_come_from_todays_margin_against_the_saved_ceiling(env, monkeypatch):
    _cache_spot(env["cache"])
    monkeypatch.setattr(service, "nearest_expiry_for", lambda proc, s, e, today: "15-Sep-2026")
    seen = {}

    def fake_margin(proc, user_id, **kw):
        seen.update(kw)
        return 7_600.0

    monkeypatch.setattr("icici_breeze_backend.app.services.bots.scalping.margin.margin_for_mixed_legs", fake_margin)
    out = service.price_lots("fly", IronFlyScalperConfig(), "u1", _Proc(), path=env["cache"], today=D(2026, 9, 14))
    assert out["lots"] == 3  # 25,000 // 7,600
    assert sorted(s for _, s, _, _ in seen["legs"]) == [24_850.0, 25_000.0, 25_000.0, 25_150.0]
    assert "7,600" in out["describe"]


def test_a_fly_lot_above_the_ceiling_refuses_the_run(env, monkeypatch):
    _cache_spot(env["cache"])
    monkeypatch.setattr(service, "nearest_expiry_for", lambda proc, s, e, today: "15-Sep-2026")
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.bots.scalping.margin.margin_for_mixed_legs",
        lambda proc, user_id, **kw: 30_000.0,
    )
    with pytest.raises(service.LotPricingError, match="ceiling"):
        service.price_lots("fly", IronFlyScalperConfig(), "u1", _Proc(), path=env["cache"], today=D(2026, 9, 14))


def test_bot2_lots_are_a_share_of_todays_free_margin(env, monkeypatch):
    _cache_spot(env["cache"], close=24_000.0)
    monkeypatch.setattr(service, "nearest_expiry_for", lambda proc, s, e, today: "15-Sep-2026")
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.bots.expiry_index_writer._available_margin",
        lambda proc, user_id: 1_000_000.0,
    )
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.bots.expiry_index_writer.margin_for_legs",
        lambda proc, user_id, **kw: 100_000.0 * len(kw["legs"]) * 0.75 if len(kw["legs"]) > 1 else 100_000.0,
    )
    config = ExpiryIndexWriterConfig(
        indices={"NIFTY": IndexWriterLeg(enabled=True, strategies=["naked_pe", "short_strangle"], margin_pct_cap=30.0)}
    )
    out = service.price_lots("expiry", config, "u1", _Proc(), path=env["cache"], today=D(2026, 9, 14))
    (scope,) = out["scopes"]
    assert scope.lots == {"naked_pe": 3, "short_strangle": 2}  # 300,000 of budget


# --- the jobs ---------------------------------------------------------------------------


def test_fetching_needs_the_live_broker(env, monkeypatch):
    monkeypatch.setattr(jobs.cfg, "ICICI_BROKER_MODE", "mock")
    with pytest.raises(ValueError, match="live"):
        jobs.start_fetch("u1", "momentum", D(2026, 3, 2), D(2026, 3, 6))


def test_fetching_is_refused_in_market_hours(env, monkeypatch):
    monkeypatch.setattr(jobs.cfg, "ICICI_BROKER_MODE", "live")
    monkeypatch.setattr(jobs, "market_hours_reason", lambda now=None: "Refusing to call ICICI at 10:00")
    with pytest.raises(ValueError, match="Refusing"):
        jobs.start_fetch("u1", "momentum", D(2026, 3, 2), D(2026, 3, 6))


def test_a_running_fetch_stops_itself_when_asked_to(env):
    class Sdk:
        def get_historical_data_v2(self, **kw):
            return {"Status": 200, "Success": []}

    fetcher = Fetcher(Sdk(), path=env["cache"], sleep=lambda s: None, stop=lambda: "The market opens at 09:00.")
    with pytest.raises(Stopped, match="market opens"):
        fetcher.call(interval="1minute")


def test_fly_and_bot2_replays_need_the_broker_for_todays_margin(env, monkeypatch):
    monkeypatch.setattr(jobs.cfg, "ICICI_BROKER_MODE", "mock")
    with pytest.raises(ValueError, match="margin"):
        jobs.start_replay("u1", "fly", D(2026, 3, 2), D(2026, 3, 6), model=True)


def test_a_bot2_replay_with_no_enabled_index_is_refused(env, monkeypatch):
    monkeypatch.setattr(jobs.cfg, "ICICI_BROKER_MODE", "live")
    with pytest.raises(ValueError, match="enabled"):
        jobs.start_replay("u1", "expiry", D(2026, 3, 2), D(2026, 3, 6), model=True)


def test_a_replay_runs_in_the_background_and_is_kept_with_its_settings(env, monkeypatch):
    _cache_trending(env["cache"])
    monkeypatch.setattr(jobs.cfg, "ICICI_BROKER_MODE", "mock")
    jobs.start_replay("u1", "momentum", D(2026, 3, 9), D(2026, 3, 9), model=True)
    state = _wait_for_job()
    assert state["status"] == "completed", state
    (run,) = store.list_runs("u1")
    assert run["status"] == "completed" and run["summary"]["cycles"] >= 1
    assert run["params"]["config"]["premium_outlay_inr"] == 25_000.0
    assert store.get_run(run["id"], "u1")["trades"]
    assert store.list_runs("someone-else") == []


def test_one_job_runs_at_a_time_and_can_be_stopped(env):
    release = threading.Event()
    jobs._start("test", lambda: release.wait(5))
    with pytest.raises(jobs.Busy):
        jobs._start("test", lambda: None)
    assert jobs.cancel() is True
    release.set()
    _wait_for_job()


def test_a_restart_fails_runs_left_running(env):
    store.save_run({"id": "r1", "user_id": "u1", "bot": "momentum", "created_at": "2026-09-13T10:00:00",
                    "status": "running", "params": {}})
    jobs.ensure_store()
    (run,) = store.list_runs("u1")
    assert run["status"] == "failed" and "restarted" in run["error"]


# --- the routes -------------------------------------------------------------------------


@pytest.fixture
def client(env, monkeypatch):
    async def _ctx():
        return RequestContext(user_id="u1", username="u1", roles=["trader"], is_authenticated=True, broker_token=None)

    monkeypatch.setattr(jobs, "market_hours_reason", lambda now=None: None)
    app = FastAPI()
    app.include_router(router, prefix="/bots")
    app.dependency_overrides[get_request_context] = _ctx
    with TestClient(app) as c:
        yield c


def test_the_overview_carries_everything_the_page_shows(client):
    body = client.get("/bots/backtest/overview").json()
    assert body["history_start"] == regime.HISTORY_START.isoformat()
    assert {"job", "probe", "coverage", "saved", "runs", "market_hours_block", "live"} <= set(body)
    assert body["saved"]["expiry"]["enabled"] is False
    assert body["coverage"]["nifty_futures"]["days"] == 0


def test_a_fly_replay_off_the_live_broker_is_a_400(client, monkeypatch):
    monkeypatch.setattr(jobs.cfg, "ICICI_BROKER_MODE", "mock")
    r = client.post("/bots/backtest/replay", json={"bot": "fly", "model": True})
    assert r.status_code == 400 and "margin" in r.json()["detail"]


def test_compare_without_simulation_cycles_is_a_404(client):
    assert client.get("/bots/backtest/compare?bot=momentum&date=2026-09-11").status_code == 404


def test_runs_can_be_read_downloaded_and_deleted(client):
    store.save_run({
        "id": "r1", "user_id": "u1", "bot": "momentum", "created_at": "2026-09-13T10:00:00",
        "status": "completed", "params": {"from": "2026-03-02", "to": "2026-03-06"},
        "summary": {"cycles": 1}, "trades": [{"entered_at": "2026-03-02 10:00:00", "net_pnl": 12.5}],
    })
    assert client.get("/bots/backtest/run?id=r1").json()["trades"][0]["net_pnl"] == 12.5
    csv_text = client.get("/bots/backtest/run/csv?id=r1").text
    assert csv_text.splitlines()[0] == "entered_at,net_pnl"
    assert client.delete("/bots/backtest/run?id=r1").status_code == 200
    assert client.get("/bots/backtest/run?id=r1").status_code == 404
