"""`/api/condor/backtest/*`: defaults, refusals, and a run that stops for data it cannot fetch.

On a mock instance nothing can be fetched, so a replay over uncached option prices stops at
its first check and is recorded as `partial` with a note saying where and why -- never as a
completed run with a silent gap.
"""
from __future__ import annotations

import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from icici_breeze_backend.app.api.v1.route_condor import router
from icici_breeze_backend.app.auth.context import RequestContext, get_request_context
from icici_breeze_backend.app.services import backtest_budget as budget_mod
from icici_breeze_backend.app.services.bots import backtest_jobs as jobs
from icici_breeze_backend.app.services.bots import backtest_service as service
from icici_breeze_backend.app.services.bots import charges as charges_mod
from icici_breeze_backend.app.services.bots.scalping import backtest_store as store
from icici_breeze_backend.app.services.bots.scalping import spreads

_TS = "%Y-%m-%d %H:%M:%S"


@pytest.fixture
def client(tmp_path, monkeypatch):
    users = str(tmp_path / "users.sqlite3")
    cache = str(tmp_path / "backtest.sqlite3")
    monkeypatch.setattr(budget_mod, "_db_path", lambda: users)
    monkeypatch.setattr(spreads, "_db_path", lambda: users)
    monkeypatch.setattr(charges_mod, "_db_path", lambda: users)
    monkeypatch.setattr(store, "db_path", lambda: cache)
    monkeypatch.setattr(jobs, "_store_ready", False)
    monkeypatch.setattr(service, "holidays", lambda: set())
    monkeypatch.setattr(jobs, "broker_live", lambda: False)
    store.ensure_tables(cache)
    # NIFTY index bars around both checks of three sessions.
    rows = []
    for day in (datetime.date(2026, 3, 16), datetime.date(2026, 3, 17), datetime.date(2026, 3, 18)):
        for hh, mm in ((10, 20), (15, 20)):
            base = datetime.datetime.combine(day, datetime.time(hh, mm))
            for i in range(15):
                ts = base + datetime.timedelta(minutes=i)
                rows.append({"datetime": ts.strftime(_TS), "open": 24200, "high": 24200, "low": 24200, "close": 24200, "volume": 0})
    store.store_candles(rows, stock_code="NIFTY", table="spot_candles", path=cache)

    app = FastAPI()
    app.include_router(router, prefix="/api/condor")
    app.dependency_overrides[get_request_context] = lambda: RequestContext(user_id="u1", username="u1", roles=[], is_authenticated=True)
    yield TestClient(app)
    if jobs._thread is not None:
        jobs._thread.join(timeout=30)


def _wait():
    if jobs._thread is not None:
        jobs._thread.join(timeout=30)


def _body(**over):
    body = {
        "settings": {"margin_ceiling_inr": 10_00_000},
        "from_date": "2026-03-16",
        "to_date": "2026-03-18",
        "exit_action": "time_roll",
        "lots_per_tranche": 2,
    }
    body.update(over)
    return body


def test_defaults_carry_the_settings_and_history_start(client):
    r = client.get("/api/condor/backtest/defaults")
    assert r.status_code == 200
    d = r.json()
    assert d["settings"]["short_delta"] == 0.2 and d["settings"]["entry_dte"] == 45
    assert d["history_start"] == "2026-01-01"
    assert d["live"] is False


def test_mock_instance_needs_lots_entered(client):
    r = client.post("/api/condor/backtest/start", json=_body(lots_per_tranche=None))
    assert r.status_code == 400
    assert "lots per tranche" in r.json()["detail"]


def test_incoherent_settings_are_refused(client):
    r = client.post("/api/condor/backtest/start", json=_body(settings={"margin_ceiling_inr": 1, "exit_dte": 31}))
    assert r.status_code == 400
    assert "entry dte" in r.json()["detail"].lower()


def test_before_history_is_refused(client):
    r = client.post("/api/condor/backtest/start", json=_body(from_date="2025-12-01"))
    assert r.status_code == 400


def test_uncached_run_is_partial_and_says_where_it_stopped(client):
    r = client.post("/api/condor/backtest/start", json=_body())
    assert r.status_code == 200
    run_id = r.json()["run_id"]
    _wait()
    runs = client.get("/api/condor/backtest/runs").json()["runs"]
    assert [x["id"] for x in runs] == [run_id]
    run = client.get(f"/api/condor/backtest/run?id={run_id}").json()
    assert run["status"] == "partial"
    notes = " ".join(run["summary"]["notes"])
    assert "Nothing fetched" in notes
    assert "108 of 108 combination(s) stopped part-way" in notes
    assert "yours at the 16 Mar 2026 10:30 check" in notes
    assert run["params"]["settings"]["margin_ceiling_inr"] == 10_00_000
    # Every combination is stored, each partial and saying where it stopped.
    rows = run["summary"]["comparison"]
    assert run["params"]["combinations"] == len(rows) == 108
    assert all(r["status"] == "partial" and r["stopped_at"] for r in rows)
    assert sum(r["is_saved"] for r in rows) == 1


def test_a_bot_run_is_not_a_condor_run(client):
    store.save_run({
        "id": "other", "user_id": "u1", "bot": "fly", "created_at": "2026-03-19T00:00:00",
        "status": "completed", "params": {}, "summary": {}, "trades": [],
    })
    assert client.get("/api/condor/backtest/runs").json()["runs"] == []
    assert client.get("/api/condor/backtest/run?id=other").status_code == 404


def test_a_range_without_index_bars_is_partial_not_a_completed_zero(client):
    r = client.post("/api/condor/backtest/start", json=_body(from_date="2026-04-01", to_date="2026-04-03"))
    assert r.status_code == 200
    _wait()
    run = client.get(f"/api/condor/backtest/run?id={r.json()['run_id']}").json()
    assert run["status"] == "partial"
    assert "had no cached NIFTY index price" in " ".join(run["summary"]["notes"])
