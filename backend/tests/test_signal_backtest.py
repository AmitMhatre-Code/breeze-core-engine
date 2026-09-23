"""Signal backtests: one run, twelve series, one zip; the 30-day gate; the Signals API.

docs/signals-streamline-plan.md sections 4-6.
"""
from __future__ import annotations

import datetime
import io
import json
import math
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from icici_breeze_backend.app.api.v1.route_signals import router
from icici_breeze_backend.app.auth.context import RequestContext, get_request_context
from icici_breeze_backend.app.core.timezone import IST
from icici_breeze_backend.app.services.bots import backtest_jobs as jobs
from icici_breeze_backend.app.services.bots import backtest_service as service
from icici_breeze_backend.app.services.bots.scalping import backtest_store as store
from icici_breeze_backend.app.services.index_signal import backtest as bt
from icici_breeze_backend.app.services.index_signal import gate
from icici_breeze_backend.app.services.index_signal.mechanisms import VERSIONS, all_keys

D = datetime.date
DAYS = (D(2026, 3, 9), D(2026, 3, 10), D(2026, 3, 11))


def _day_rows(day: datetime.date, seed: float, with_oi: bool) -> list[dict]:
    """A full 09:00-15:29 session: pre-open, then a wandering close with bursts of volume."""
    rows = []
    base = datetime.datetime.combine(day, datetime.time(9, 0))
    level = 24_000.0 + seed
    for i in range(390):
        ts = base + datetime.timedelta(minutes=i)
        level += 6.0 * math.sin((i + seed) / 9.0) + (3.0 if (i // 40) % 2 else -2.5)
        volume = 800 + (i * 53) % 700 + (6_000 if i % 23 == 0 else 0)
        row = {
            "datetime": ts.strftime("%Y-%m-%d %H:%M:%S"),
            "open": level - 1, "high": level + 3, "low": level - 4, "close": level,
            "volume": volume,
        }
        if with_oi:
            row["open_interest"] = 18_000_000 + 900 * i
        rows.append(row)
    return rows


@pytest.fixture
def env(tmp_path, monkeypatch):
    import icici_breeze_backend.app.core.config as cfg

    users = str(tmp_path / "users.sqlite3")
    cache = str(tmp_path / "backtest.sqlite3")
    monkeypatch.setattr(cfg, "DATA_PATH", str(tmp_path) + "/")
    monkeypatch.setattr(bt, "_db_path", lambda: users)
    monkeypatch.setattr(store, "db_path", lambda: cache)
    monkeypatch.setattr(jobs, "_store_ready", False)
    monkeypatch.setattr(service, "holidays", lambda: set())
    monkeypatch.setattr(jobs.cfg, "ICICI_BROKER_MODE", "mock")
    monkeypatch.setattr(bt, "now_ist", lambda: datetime.datetime(2026, 9, 17, 18, 0, tzinfo=IST))
    gate.invalidate()
    store.ensure_tables(cache)
    for k, day in enumerate(DAYS):
        store.store_candles(_day_rows(day, 40.0 * k, True), stock_code="NIFTY", path=cache)
        store.store_candles(_day_rows(day, 70.0 * k, False), stock_code="BSESEN", path=cache)
    yield {"users": users, "cache": cache, "tmp": tmp_path}
    if jobs._thread is not None:
        jobs._thread.join(timeout=30)
    gate.invalidate()


def _wait():
    if jobs._thread is not None:
        jobs._thread.join(timeout=60)
    return jobs.state()


def test_one_run_replays_every_series_into_one_zip(env):
    bt.start("u1", "custom", DAYS[1], DAYS[2])
    state = _wait()
    assert state["status"] == "completed", state
    (run,) = bt.list_runs()
    assert run["status"] == "completed" and run["has_zip"]
    assert run["versions"] == VERSIONS
    assert set(run["summary"]) == {k.id for k in all_keys()}
    assert any("mode" in n for n in run["notes"]), "mock mode must say nothing was fetched"

    with zipfile.ZipFile(run["zip_path"]) as zf:
        names = set(zf.namelist())
        assert {"README.txt", "run.json", "summary.csv", "NIFTY/bars.csv", "SENSEX/bars.csv"} <= names
        for key in all_keys():
            folder = f"{'NIFTY' if key.index == 'nifty' else 'SENSEX'}/{key.slug}"
            assert {f"{folder}/readings.csv", f"{folder}/calls.csv", f"{folder}/days.csv"} <= names
        summary = zf.read("summary.csv").decode()
        assert len(summary.strip().splitlines()) == 1 + len(all_keys())
        meta = json.loads(zf.read("run.json"))
        assert meta["from"] == DAYS[1].isoformat() and meta["versions"] == VERSIONS
        readings = zf.read("NIFTY/expansion-15m/readings.csv").decode().splitlines()
        # Two sessions of 09:15-15:14 readings; the day before the range only warms the engines.
        assert len(readings) == 1 + 2 * 360
        assert "fwd_5m_bps" in readings[0] and "c_price_rank" in readings[0]

    nifty = run["summary"]["nifty:momentum:1m"]
    assert nifty["sessions_replayed"] == 2
    assert nifty["calls"] > 0, "the fixture's bursts must produce momentum calls"


def test_an_unknown_period_is_refused(env):
    with pytest.raises(ValueError, match="period"):
        bt.start("u1", "last_year")


def _completed(run_id: str, start: D, end: D, versions=None) -> None:
    bt.create_run(run_id, "u1", "custom", start, end)
    bt.update_run(run_id, status="completed", versions=versions or VERSIONS)


def test_the_gate_needs_thirty_calendar_days_on_the_current_version(env):
    _completed("short", D(2026, 8, 1), D(2026, 8, 29))  # 29 days
    assert not gate.mechanism_availability("expansion", fresh=True)["available"]
    assert "29" in gate.mechanism_availability("expansion", fresh=True)["reason"]

    stale = {**VERSIONS, "momentum": VERSIONS["momentum"] + 1}
    _completed("other-version", D(2026, 7, 1), D(2026, 8, 30), versions=stale)
    assert gate.mechanism_availability("expansion", fresh=True)["available"]
    assert not gate.mechanism_availability("momentum", fresh=True)["available"]

    _completed("month", D(2026, 8, 1), D(2026, 8, 30))  # exactly 30 days
    assert gate.mechanism_availability("momentum", fresh=True)["available"]


def test_a_failed_run_never_opens_the_gate(env):
    bt.create_run("failed", "u1", "custom", D(2026, 6, 1), D(2026, 8, 30))
    bt.update_run("failed", status="failed", error="boom")
    assert not gate.mechanism_availability("expansion", fresh=True)["available"]


def test_a_run_cut_off_by_a_restart_is_closed_at_startup(env):
    """A signal run is a thread of this process, so one still `running` at startup is stale."""
    bt.create_run("cut-off", "u1", "custom", D(2026, 8, 1), D(2026, 8, 30))
    _completed("done", D(2026, 8, 1), D(2026, 8, 30))
    assert bt.get_run("cut-off")["status"] == "running"

    assert bt.reap_orphaned_runs() == 1

    reaped = bt.get_run("cut-off")
    assert reaped["status"] == "failed"
    assert "restart" in (reaped["error"] or "")
    assert reaped["finished_at"]
    assert bt.get_run("done")["status"] == "completed", "a finished run keeps its verdict"
    assert bt.reap_orphaned_runs() == 0, "nothing left to reap on the next restart"


def test_an_interrupted_run_never_opened_the_gate_either(env):
    """The reaper is for the run list; a `running` row must not have counted for the 30 days."""
    bt.create_run("in-flight", "u1", "custom", D(2026, 8, 1), D(2026, 8, 30))
    assert not gate.mechanism_availability("expansion", fresh=True)["available"]


@pytest.fixture
def client(env, monkeypatch):
    async def _ctx():
        return RequestContext(user_id="u1", username="u1", roles=["trader"], is_authenticated=True,
                              broker_token=None)

    app = FastAPI()
    app.include_router(router, prefix="/api/signals")
    app.dependency_overrides[get_request_context] = _ctx
    with TestClient(app) as c:
        yield c


def test_the_overview_has_a_section_per_mechanism_with_six_series(client):
    body = client.get("/api/signals").json()
    assert [m["id"] for m in body["mechanisms"]] == ["expansion", "momentum"]
    for m in body["mechanisms"]:
        assert len(m["series"]) == 6
        assert m["availability"]["available"] is False
        assert all(s["reading"]["state"] == "unavailable" for s in m["series"])
    assert body["navbar_mechanism"] == "expansion" and body["navbar_duration"] == 15


def test_the_navbar_choice_is_saved(client):
    assert client.put("/api/signals/navbar", json={"mechanism": "momentum"}).json() == {
        "navbar_mechanism": "momentum"
    }
    assert client.get("/api/signals").json()["navbar_mechanism"] == "momentum"
    assert client.put("/api/signals/navbar", json={"mechanism": "wobi"}).status_code == 422


def test_runs_are_listed_and_their_zip_downloads(client):
    assert client.post("/api/signals/backtest", json={"period": "custom", "from_date": "2026-03-10",
                                                      "to_date": "2026-03-11"}).status_code == 200
    _wait()
    runs = client.get("/api/signals/backtest/runs").json()["runs"]
    assert len(runs) == 1 and runs[0]["status"] == "completed" and runs[0]["has_zip"]
    assert runs[0]["counts_for_gate"] is False
    res = client.get(f"/api/signals/backtest/runs/{runs[0]['id']}/zip")
    assert res.status_code == 200 and res.headers["content-type"] == "application/zip"
    assert "summary.csv" in zipfile.ZipFile(io.BytesIO(res.content)).namelist()
    assert client.get("/api/signals/backtest/runs/nope/zip").status_code == 404
