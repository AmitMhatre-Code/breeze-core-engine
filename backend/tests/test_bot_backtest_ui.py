"""Bots -> Backtest: the shared service, the background jobs and the routes (plan 8.11).

What these pin: fetching is refused off the live broker and in market hours, and a running
fetch stops itself when the market opens; one job runs at a time; the fly and Bot 2 size from
one lot of today's margin against their saved ceiling or share; Bot 2 runs only the saved
shortlist of enabled indices; every run is kept with the settings it ran on, and a restart
never leaves one spinning.
"""
from __future__ import annotations

import datetime
import os
import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from icici_breeze_backend.app.api.v1.route_bots_backtest import router
from icici_breeze_backend.app.auth.context import RequestContext, get_request_context
from icici_breeze_backend.app.db.bots_migrate import ensure_bots_tables
from icici_breeze_backend.app.domain.bots import (
    CasBingoConfig,
    ExpiryIndexWriterConfig,
    IndexWriterLeg,
    IronFlyScalperConfig,
    MomentumLongScalperConfig,
)
from icici_breeze_backend.app.repositories import bots as repo
from icici_breeze_backend.app.services import backtest_budget as budget_mod
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
    monkeypatch.setattr(budget_mod, "_db_path", lambda: users)
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


def _save_momentum_run(cache, run_id, config, *, created_at, status="completed"):
    store.save_run(
        {
            "id": run_id,
            "user_id": "u1",
            "bot": "momentum",
            "created_at": created_at,
            "status": status,
            "params": {
                "bot": "momentum",
                "from": "2026-06-16",
                "to": "2026-09-12",
                "model": False,
                "config": config.model_dump(mode="json"),
            },
            "summary": {
                "days_replayed": 60,
                "cycles": 42,
                "net_pnl": 18_450.0,
                "friction": 3_600.0,
                "win_rate_pct": 55.0,
                "price_source": "real",
            },
        },
        path=cache,
    )


def test_backtest_evidence_is_only_the_run_on_these_exact_settings(env):
    """The Live dialog may only cite a backtest of the bot being armed.

    A run on other settings describes a different bot, and an unfinished one has no result --
    either would put a number in front of the user that their click is not actually about.
    """
    bot_type = service.BOT_TYPES["momentum"]
    config = MomentumLongScalperConfig()
    other = config.model_copy(update={"premium_outlay_inr": config.premium_outlay_inr + 5_000})

    # Both decoys are NEWER, so recency alone would pick the wrong one.
    _save_momentum_run(env["cache"], "r-other-settings", other, created_at="2026-09-16T09:00:00")
    _save_momentum_run(env["cache"], "r-running", config, created_at="2026-09-16T10:00:00", status="running")
    _save_momentum_run(env["cache"], "r-mine", config, created_at="2026-09-15T20:00:00")

    found = service.backtest_evidence("u1", bot_type, config, path=env["cache"])
    assert found is not None
    assert found["run_id"] == "r-mine"
    assert found["net_pnl"] == 18_450.0
    assert found["cycles"] == 42
    # No fill check has been run, and the dialog must not imply one.
    assert found["compare_median_entry_gap"] is None

    assert service.backtest_evidence("u1", bot_type, other, path=env["cache"])["run_id"] == "r-other-settings"


def test_a_recorded_fill_check_travels_with_the_backtest_it_belongs_to(env):
    """`compare` is keyed by settings, so editing any of them drops the stale check."""
    from icici_breeze_backend.app.services.bots.scalping.evidence import material_config_hash

    bot_type = service.BOT_TYPES["momentum"]
    config = MomentumLongScalperConfig()
    _save_momentum_run(env["cache"], "r-mine", config, created_at="2026-09-15T20:00:00")

    service.record_compare(
        "momentum",
        material_config_hash(bot_type, config.model_dump(mode="json")),
        {"day": "2026-09-11", "median_abs_entry_diff": 1.25, "pairs": [object(), object()]},
        path=env["cache"],
    )

    found = service.backtest_evidence("u1", bot_type, config, path=env["cache"])
    assert found["compare_day"] == "2026-09-11"
    assert found["compare_median_entry_gap"] == 1.25
    assert found["compare_pairs"] == 2

    # The same run, judged for settings the check was not made on: the run still matches on its
    # own hash, but its fill check does not follow.
    changed = config.model_copy(update={"premium_outlay_inr": config.premium_outlay_inr + 5_000})
    _save_momentum_run(env["cache"], "r-changed", changed, created_at="2026-09-15T21:00:00")
    assert service.backtest_evidence("u1", bot_type, changed, path=env["cache"])["compare_median_entry_gap"] is None


def _cache_trending(cache, day=D(2026, 3, 9)):
    closes = [24_000.0] * 25 + [24_000.0 + 12 * i for i in range(1, 40)]
    volumes = [1_000] * 25 + [90_000] * 39
    store.store_candles(_rows(day, closes, volumes), path=cache)


MOMENTUM_1M = {"mechanism": "momentum", "duration": 1, "direction": "follow"}


@pytest.fixture
def audit(tmp_path, monkeypatch):
    from icici_breeze_backend.audit import bot_audit

    root = tmp_path / "bots-audit"
    root.mkdir()
    monkeypatch.setattr(bot_audit, "audit_dir", lambda: str(root))
    return bot_audit


class _StubFetcher:
    """Stands in for `jobs._fetcher`: records the budget it was handed, fetches nothing."""

    def __init__(self, planned, *, on_underlying=None):
        self.calls = 0
        self.path = None
        self.holidays = set()
        self._planned = planned
        self._on_underlying = on_underlying
        self.log = lambda line: None

    @property
    def max_calls(self):
        return self._planned.get("max_calls")

    @max_calls.setter
    def max_calls(self, value):
        self._planned["max_calls"] = value

    def fetch_futures(self, *a, **kw):
        if self._on_underlying:
            self._on_underlying()

    def fetch_spot(self, *a, **kw):
        if self._on_underlying:
            self._on_underlying()

    def fetch_vix(self, *a, **kw):
        pass

    def fetch_needs(self, needs):
        return {"fetched": 0, "bars": 0, "empty": 0, "errors": 0}


def _wait_for_job():
    if jobs._thread is not None:
        jobs._thread.join(timeout=30)
    return jobs.state()


# --- the service ------------------------------------------------------------------------


def test_the_range_is_not_floored_and_defaults_to_yesterday():
    """#36: backtest periods are unrestricted; HISTORY_START is only the default start."""
    assert service.clip_range(None, None, D(2026, 9, 14)) == (regime.HISTORY_START, D(2026, 9, 13))
    assert service.clip_range(D(2025, 6, 1), D(2026, 2, 1), D(2026, 9, 14))[0] == D(2025, 6, 1)
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
    # The trending day is built to fire the 1-minute momentum series; Bot 3's default is the
    # 15-minute expansion fade, which needs more history to warm than one cached day.
    repo.update_bot("u1", "momentum_long_scalper", config={"signal": MOMENTUM_1M})
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


def test_every_bot_with_a_replay_can_be_started_from_its_card(client, monkeypatch):
    # The route once listed only the retired page's bots, so CAS Bingo's card got a bare 422.
    monkeypatch.setattr(jobs, "start_bot_backtest", lambda user_id, bot, *a: {"bot": bot})
    for bot in service.BOT_TYPES:
        r = client.post("/bots/backtest/start", json={"bot": bot, "period": "last_day"})
        assert r.status_code == 200 and r.json()["bot"] == bot, (bot, r.text)


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


# --- the card's one-click backtest (#35, #36) ---------------------------------------------

IST_ = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


def _at(y, m, d, hh, mm):
    return datetime.datetime(y, m, d, hh, mm, tzinfo=IST_)


class TestPeriods:
    def test_last_day_is_today_only_after_the_close(self):
        # Thursday 2026-09-17: before 15:30 the last completed session is Wednesday.
        assert service.resolve_period("last_day", None, None, _at(2026, 9, 17, 11, 0), set()) == (D(2026, 9, 16), D(2026, 9, 16))
        assert service.resolve_period("last_day", None, None, _at(2026, 9, 17, 16, 0), set()) == (D(2026, 9, 17), D(2026, 9, 17))

    def test_last_day_skips_weekends_and_holidays(self):
        monday_morning = _at(2026, 9, 14, 10, 0)
        assert service.resolve_period("last_day", None, None, monday_morning, set())[1] == D(2026, 9, 11)
        assert service.resolve_period("last_day", None, None, monday_morning, {D(2026, 9, 11)})[1] == D(2026, 9, 10)

    def test_last_week_is_five_trading_sessions_on_the_exchange_calendar(self):
        start, end = service.resolve_period("last_week", None, None, _at(2026, 9, 17, 16, 0), {D(2026, 9, 15)})
        # Five sessions ending Thursday, with Tuesday a holiday: Wed 10 .. Thu 17.
        assert (start, end) == (D(2026, 9, 10), D(2026, 9, 17))
        assert len(regime.trading_days(start, end, {D(2026, 9, 15)})) == 5

    def test_last_month_runs_from_the_day_after_the_same_date_a_month_back(self):
        assert service.resolve_period("last_month", None, None, _at(2026, 3, 31, 16, 0), set()) == (D(2026, 3, 1), D(2026, 3, 31))

    def test_custom_is_not_floored_and_is_clipped_to_the_last_completed_session(self):
        start, end = service.resolve_period("custom", D(2024, 6, 3), D(2026, 12, 31), _at(2026, 9, 17, 11, 0), set())
        assert start == D(2024, 6, 3)  # unrestricted: before HISTORY_START is fine
        assert end == D(2026, 9, 16)   # never into a session still open

    def test_custom_needs_both_dates(self):
        with pytest.raises(ValueError, match="both"):
            service.resolve_period("custom", D(2026, 9, 1), None, _at(2026, 9, 17, 11, 0), set())


class TestBudgetAndCap:
    def test_calls_accumulate_per_day_against_the_budget(self, env):
        day = D(2026, 9, 17)
        assert store.calls_remaining(day) == store.DAILY_CALL_BUDGET
        store.add_calls(day, 300)
        store.add_calls(day, 200)
        assert store.calls_spent(day) == 500
        assert store.calls_remaining(day) == store.DAILY_CALL_BUDGET - 500
        assert store.calls_remaining(D(2026, 9, 18)) == store.DAILY_CALL_BUDGET  # a new day resets

    def test_the_budget_is_a_setting_and_the_ledger_is_measured_against_it(self, env):
        day = D(2026, 9, 17)
        # Unset, it is the shipped default.
        assert store.daily_call_budget() == budget_mod.DEFAULT_DAILY_CALL_BUDGET
        budget_mod.set_daily_call_budget(2000, env["users"])
        assert store.daily_call_budget() == 2000
        store.add_calls(day, 900)
        # The ledger is untouched by the change; only the ceiling moved.
        assert store.calls_spent(day) == 900
        assert store.calls_remaining(day) == 1100
        # Lowering it below what today already spent leaves nothing, never a negative.
        budget_mod.set_daily_call_budget(500, env["users"])
        assert store.calls_remaining(day) == 0

    def test_the_budget_rejects_values_outside_icicis_own_daily_allowance(self, env):
        for bad in (-1, budget_mod.MAX_DAILY_CALL_BUDGET + 1):
            with pytest.raises(ValueError, match="between"):
                budget_mod.set_daily_call_budget(bad, env["users"])

    def test_reading_the_budget_never_creates_the_table(self, tmp_path):
        """A getter that ran DDL would have every replay writing to the app DB -- including a
        test with its own temp copy."""
        fresh = str(tmp_path / "untouched.sqlite3")
        assert budget_mod.get_daily_call_budget(fresh) == budget_mod.DEFAULT_DAILY_CALL_BUDGET
        assert not os.path.exists(fresh)

    def test_the_cap_evicts_option_history_oldest_expiry_first_and_never_the_underlying(self, env):
        from icici_breeze_backend.app.services.bots.scalping.backtest_store import OptionKey

        cache = env["cache"]
        store.store_candles(_rows(D(2026, 3, 9), [24_000.0] * 50, [10] * 50), path=cache)
        bars = _rows(D(2026, 3, 9), [100.0] * 300, [5] * 300)
        for expiry in (D(2026, 3, 10), D(2026, 3, 17), D(2026, 3, 24)):
            store.store_option_candles(bars, OptionKey("NIFTY", expiry, 24_000.0, "call"), store.INTERVAL_MINUTE, path=cache)

        out = store.enforce_cache_cap(max_bytes=store.cache_bytes(cache) - 1, path=cache)
        assert out["evicted_expiries"][0] == "2026-03-10"
        assert store.load_candles(path=cache), "futures bars must never be evicted"

    def test_a_cache_under_the_cap_is_left_alone(self, env):
        assert store.enforce_cache_cap(path=env["cache"])["evicted_expiries"] == []


class TestOneClickBacktest:
    def test_a_run_on_cached_data_records_an_activity_row_and_its_own_trail(self, env, audit, monkeypatch):
        _cache_trending(env["cache"])
        monkeypatch.setattr(jobs.cfg, "ICICI_BROKER_MODE", "mock")
        monkeypatch.setattr(jobs, "now_ist", lambda: _at(2026, 3, 9, 18, 0))

        jobs.start_bot_backtest("u1", "momentum", "last_day")
        state = _wait_for_job()
        assert state["status"] == "completed", state

        (row,) = [r for r in repo.list_runs("u1") if r.trigger == "backtest"]
        assert row.status == "completed"
        assert row.detail["from"] == row.detail["to"] == "2026-03-09"
        # One id ties the Activity row to the stored run with its trades.
        assert store.get_run(row.id, "u1")["status"] == "completed"
        # Mock mode: nothing fetched, and the row says so rather than pretending it did.
        assert any("mode" in n for n in row.detail["summary"]["notes"])

        # Bot 3 is compared across every signal setting it could trade (plan section 8).
        comparison = row.detail["summary"]["comparison"]
        assert len(comparison) == 12 and sum(1 for c in comparison if c["is_saved"]) == 1

        # The row downloads one zip: the comparison, each setting's files, and the trail.
        import json
        import zipfile

        name = audit.find_for_backtest_run("u1", row.bot_type, row.id)
        assert name and name.endswith(".zip") and audit.resolve_backtest_file_for_user(name, "u1")
        with zipfile.ZipFile(audit.resolve_backtest_file_for_user(name, "u1")) as zf:
            names = set(zf.namelist())
            assert {"README.txt", "run.json", "summary.csv", "audit.jsonl"} <= names
            assert {"momentum-1m-follow/trades.csv", "momentum-1m-follow/daily.csv",
                    "momentum-1m-follow/decisions.csv"} <= names
            assert len(zf.read("summary.csv").decode().strip().splitlines()) == 13
            events = [json.loads(line)["event"] for line in zf.read("audit.jsonl").decode().splitlines()]
        assert events[0] == "backtest_started" and events[-1] == "backtest_finished"

    def test_the_row_never_stands_a_live_session_down(self, env, audit, monkeypatch):
        _cache_trending(env["cache"])
        monkeypatch.setattr(jobs.cfg, "ICICI_BROKER_MODE", "mock")
        jobs.start_bot_backtest("u1", "momentum", "custom", D(2026, 3, 9), D(2026, 3, 9))
        _wait_for_job()
        bot_type = service.BOT_TYPES["momentum"]
        assert repo.has_terminal_run_today("u1", bot_type) is False
        assert repo.has_committed_run_today("u1", bot_type) is False

    def test_a_trail_belongs_to_its_user_and_rejects_traversal(self, env, audit):
        name = audit.write_backtest_audit("u1", "momentum_long_scalper", "run-1", [{"event": "x"}])
        assert audit.resolve_backtest_file_for_user(name, "u1")
        assert audit.resolve_backtest_file_for_user(name, "u2") is None
        assert audit.resolve_backtest_file_for_user("../../etc/passwd", "u1") is None

    def test_a_restart_closes_a_backtest_left_running(self, env):
        run_id = repo.start_run("u1", service.BOT_TYPES["momentum"], "backtest")
        assert repo.reap_orphaned_backtests() == 1
        (row,) = [r for r in repo.list_runs("u1") if r.id == run_id]
        assert row.status == "failed" and row.reason_code == "backtest_interrupted"

    def test_a_setting_is_written_to_the_zip_as_it_finishes_not_held_to_the_end(
        self, env, audit, monkeypatch
    ):
        """#41: decisions are the biggest thing a run makes, so they leave memory as they are made.

        Pinned by watching the zip grow: every setting's decisions.csv is in the file before the
        run's own members (README, summary, audit) are written at the end.
        """
        _cache_trending(env["cache"])
        monkeypatch.setattr(jobs.cfg, "ICICI_BROKER_MODE", "mock")
        monkeypatch.setattr(jobs, "now_ist", lambda: _at(2026, 3, 9, 18, 0))

        written: list[str] = []
        add = audit.BacktestZipWriter.add
        monkeypatch.setattr(
            audit.BacktestZipWriter, "add",
            lambda self, member, content: (written.append(member), add(self, member, content))[1],
        )

        jobs.start_bot_backtest("u1", "momentum", "last_day")
        assert _wait_for_job()["status"] == "completed"

        decisions = [m for m in written if m.endswith("/decisions.csv")]
        assert len(decisions) == 12, written
        assert written.index(decisions[-1]) < written.index("summary.csv")

    def test_a_run_that_fails_leaves_no_zip_behind(self, env, audit, monkeypatch):
        _cache_trending(env["cache"])
        monkeypatch.setattr(jobs.cfg, "ICICI_BROKER_MODE", "mock")
        monkeypatch.setattr(jobs, "now_ist", lambda: _at(2026, 3, 9, 18, 0))
        monkeypatch.setattr(
            jobs.service, "trade_rows",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        jobs.start_bot_backtest("u1", "momentum", "last_day")
        assert _wait_for_job()["status"] == "failed"

        (row,) = [r for r in repo.list_runs("u1") if r.trigger == "backtest"]
        assert row.status == "failed"
        left = os.listdir(audit.backtest_dir())
        assert not [n for n in left if n.endswith((".zip", ".partial"))], left


    def test_the_fly_still_needs_the_broker_for_todays_margin(self, env, monkeypatch):
        monkeypatch.setattr(jobs.cfg, "ICICI_BROKER_MODE", "mock")
        with pytest.raises(ValueError, match="margin"):
            jobs.start_bot_backtest("u1", "fly", "last_week")


class TestTheTwoBudgets:
    """#42: a fetch is planned against the smaller of its own budget and the day's headroom.

    The run this came from was told it had 3,998 calls when the deployment had ~1,470 left
    before the reserve, so it planned a fetch that the app itself refused most of the way
    through.
    """

    @pytest.fixture
    def live(self, env, monkeypatch):
        _cache_trending(env["cache"])
        monkeypatch.setattr(jobs, "broker_live", lambda: True)
        monkeypatch.setattr(jobs, "market_hours_reason", lambda now=None: None)
        monkeypatch.setattr(jobs, "now_ist", lambda: _at(2026, 3, 9, 18, 0))
        return env

    def _notes(self):
        (row,) = [r for r in repo.list_runs("u1") if r.trigger == "backtest"]
        return " ".join(row.detail["summary"]["notes"])

    def test_the_smaller_budget_is_the_one_planned_against(self, live, monkeypatch):
        planned = {}
        monkeypatch.setattr(jobs.store, "calls_remaining", lambda d: 3998)
        monkeypatch.setattr(jobs.api_usage, "advisory_headroom", lambda uid: 1470)
        monkeypatch.setattr(jobs, "_fetcher", lambda uid: _StubFetcher(planned))

        jobs.start_bot_backtest("u1", "momentum", "last_day")
        assert _wait_for_job()["status"] == "completed"
        assert planned["max_calls"] == 1470, "the day's headroom, not the backtest budget"
        assert "leaves room for 1470" in self._notes()

    def test_the_backtest_budget_still_binds_when_it_is_smaller(self, live, monkeypatch):
        planned = {}
        monkeypatch.setattr(jobs.store, "calls_remaining", lambda d: 200)
        monkeypatch.setattr(jobs.api_usage, "advisory_headroom", lambda uid: 1470)
        monkeypatch.setattr(jobs, "_fetcher", lambda uid: _StubFetcher(planned))

        jobs.start_bot_backtest("u1", "momentum", "last_day")
        assert _wait_for_job()["status"] == "completed"
        assert planned["max_calls"] == 200
        assert "leaves room for" not in self._notes(), "nothing to explain; its own budget binds"

    def test_no_headroom_says_so_instead_of_blaming_the_backtest_budget(self, live, monkeypatch):
        monkeypatch.setattr(jobs.store, "calls_remaining", lambda d: 3998)
        monkeypatch.setattr(jobs.api_usage, "advisory_headroom", lambda uid: 0)

        jobs.start_bot_backtest("u1", "momentum", "last_day")
        assert _wait_for_job()["status"] == "completed"
        notes = self._notes()
        assert "holding the rest back" in notes
        assert "Raising the backtest budget will not help" in notes

    def test_a_spent_allowance_mid_fetch_is_its_own_note(self, live, monkeypatch):
        monkeypatch.setattr(jobs.store, "calls_remaining", lambda d: 3998)
        monkeypatch.setattr(jobs.api_usage, "advisory_headroom", lambda uid: 1470)

        def blow_up(*a, **kw):
            raise jobs.AllowanceSpent("this deployment has used 4500 of its 5000 ICICI calls.")

        monkeypatch.setattr(jobs, "_fetcher", lambda uid: _StubFetcher({}, on_underlying=blow_up))

        jobs.start_bot_backtest("u1", "momentum", "last_day")
        assert _wait_for_job()["status"] == "completed", "a spent allowance is a gap, not a failure"
        notes = self._notes()
        assert "Fetch stopped early: this deployment has used 4500" in notes
        assert "Replayed on what was cached" not in notes, "not dressed up as a generic stop"


class TestTheReadingsCacheIsBounded:
    """#41: a run holds the last few signal series, not all six.

    The bound is only free because of an ordering fact -- the settings that share a series run
    back to back -- so these pin the fact as hard as they pin the cache.
    """

    def test_it_keeps_the_last_series_and_drops_the_oldest(self):
        cache = service.ReadingsCache(keep=2)
        for n, name in enumerate(("a", "b", "c")):
            cache[name] = {n: name}
        assert len(cache) == 2
        assert "a" not in cache and "b" in cache and "c" in cache

    def test_reading_a_series_keeps_it(self):
        cache = service.ReadingsCache(keep=2)
        cache["a"], cache["b"] = {1: "a"}, {2: "b"}
        assert cache["a"] == {1: "a"}   # "a" is now the most recent, so "b" is the one to go
        cache["c"] = {3: "c"}
        assert "a" in cache and "c" in cache and "b" not in cache

    def test_a_series_built_twice_is_counted(self):
        cache = service.ReadingsCache(keep=1)
        cache["a"], cache["b"] = {}, {}
        assert cache.rebuilt == 0
        cache["a"] = {}                 # dropped by "b", and now paid for again
        assert cache.rebuilt == 1

    def test_room_is_made_before_a_build_not_after(self):
        """The peak is while the new series is being built, so the old one goes first."""
        cache = service.ReadingsCache(keep=2)
        cache["a"], cache["b"] = {}, {}
        cache.reserve()
        assert len(cache) == 1 and "a" not in cache, "the oldest goes, and only the oldest"
        cache["c"] = {}
        assert len(cache) == 2 and cache.rebuilt == 0

    @pytest.mark.parametrize(
        "bot, config",
        [
            ("momentum", MomentumLongScalperConfig()),
            ("cas", CasBingoConfig()),
            ("fly", IronFlyScalperConfig()),
        ],
    )
    def test_no_bot_ever_rebuilds_a_series_it_already_built(self, bot, config):
        """The claim KEEP_SERIES rests on, replayed against the real combo order.

        CAS Bingo is the one that needs two: a single replay of it walks both enabled indices
        and asks for that setting's series once per index.
        """
        from icici_breeze_backend.app.services.bots import backtest_combos
        from icici_breeze_backend.app.services.bots.cas_bingo.market import SIGNAL_LABEL

        def asked_for(combo):
            """The series ids one replay of `combo` requests, in order."""
            if bot == "momentum":
                return [service._series_key(combo.config.signal, "nifty").id]
            if bot == "cas":
                return [service._series_key(combo.config.signal, SIGNAL_LABEL[i]).id
                        for i in combo.config.enabled_indices()]
            entry = combo.config.entry_filter
            return ([service._series_key(entry.signal, "nifty").id]
                    if entry.kind == "signal_quiet" else [])

        cache = service.ReadingsCache()
        seen = set()
        for combo in backtest_combos.combos_for(bot, config):
            for key_id in asked_for(combo):
                # Exactly what `_series_readings` does, `reserve()` included -- the eviction
                # before the build is part of what has to stay rebuild-free.
                if key_id not in cache:
                    cache.reserve()
                    cache[key_id] = {"series": key_id}
                seen.add(key_id)

        assert cache.rebuilt == 0, f"{bot}: the combo order no longer suits KEEP_SERIES"
        assert len(seen) > len(cache), f"{bot}: nothing was bounded, so this proves nothing"

    def test_a_real_run_builds_each_series_once(self, env, audit, monkeypatch):
        """End to end: twelve settings, six series, six builds."""
        from icici_breeze_backend.app.services.bots.scalping import backtest_common

        built: list[str] = []
        real = backtest_common.series_readings
        monkeypatch.setattr(
            backtest_common, "series_readings",
            lambda bars, key, **kw: (built.append(key.id), real(bars, key, **kw))[1],
        )
        _cache_trending(env["cache"])
        monkeypatch.setattr(jobs.cfg, "ICICI_BROKER_MODE", "mock")
        monkeypatch.setattr(jobs, "now_ist", lambda: _at(2026, 3, 9, 18, 0))

        jobs.start_bot_backtest("u1", "momentum", "last_day")
        assert _wait_for_job()["status"] == "completed"
        assert len(built) == 6 and len(set(built)) == 6, built


class TestTheMemoryCeiling:
    """#41: a backtest stops itself rather than being killed part-way with nothing to show."""

    @staticmethod
    def _usage(*readings):
        """Successive `memory.usage()` answers; the last one repeats."""
        seen = iter(readings)
        last = [readings[-1]]

        def usage():
            try:
                last[0] = next(seen)
            except StopIteration:
                pass
            return last[0]

        return usage

    def test_a_reading_that_cannot_be_taken_is_not_a_refusal(self, monkeypatch):
        monkeypatch.setattr(jobs.memory, "usage", lambda: None)
        jobs._memory_check("anywhere")  # a dev machine has no cgroup, and still runs backtests

    def test_room_to_spare_is_not_a_refusal(self, monkeypatch):
        monkeypatch.setattr(jobs.memory, "usage", lambda: (100_000_000, 1_000_000_000))
        jobs._memory_check("anywhere")

    def test_the_ceiling_stops_a_run_before_it_starts(self, env, monkeypatch):
        monkeypatch.setattr(jobs.cfg, "ICICI_BROKER_MODE", "mock")
        monkeypatch.setattr(jobs.memory, "usage", lambda: (900_000_000, 1_000_000_000))
        with pytest.raises(jobs.OutOfMemory, match="shorter period"):
            jobs.start_bot_backtest("u1", "momentum", "last_day")
        # Refused before the row was opened, so nothing is left looking like a run that died.
        assert [r for r in repo.list_runs("u1") if r.trigger == "backtest"] == []

    def test_the_ceiling_stops_a_run_part_way_and_says_where(self, env, audit, monkeypatch):
        _cache_trending(env["cache"])
        monkeypatch.setattr(jobs.cfg, "ICICI_BROKER_MODE", "mock")
        monkeypatch.setattr(jobs, "now_ist", lambda: _at(2026, 3, 9, 18, 0))
        # Room at the pre-flight and at the first setting; none at the second.
        monkeypatch.setattr(
            jobs.memory, "usage",
            self._usage((100_000_000, 1_000_000_000), (100_000_000, 1_000_000_000), (900_000_000, 1_000_000_000)),
        )

        jobs.start_bot_backtest("u1", "momentum", "last_day")
        state = _wait_for_job()
        assert state["status"] == "failed"
        assert "after 1 of 12 signal setting(s)" in state["error"]

        (row,) = [r for r in repo.list_runs("u1") if r.trigger == "backtest"]
        assert row.status == "failed" and row.reason_code == "backtest_failed"
        assert "0.90 GB of the 1.00 GB" in row.reason_text
        # The half-built zip goes with it; the trail it got as far as is still written.
        left = os.listdir(audit.backtest_dir())
        assert not [n for n in left if n.endswith((".zip", ".partial"))], left
        assert [n for n in left if n.endswith(".jsonl")]

    def test_the_route_calls_it_a_refusal_not_a_crash(self, env, client, monkeypatch):
        monkeypatch.setattr(jobs.cfg, "ICICI_BROKER_MODE", "mock")
        monkeypatch.setattr(jobs.memory, "usage", lambda: (900_000_000, 1_000_000_000))
        r = client.post("/bots/backtest/start", json={"bot": "momentum", "period": "last_day"})
        assert r.status_code == 503 and "restart the app" in r.json()["detail"]
