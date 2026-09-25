"""Settings -> Storage (#44): the volume reading, the threshold, deletes by date, and the halt.

The volume is faked through `shutil.disk_usage`, so every test decides how full the disk is.
"""
from __future__ import annotations

import collections
import datetime
import json
import os
import sqlite3
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.api.deps import get_current_user
from icici_breeze_backend.app.auth.context import RequestContext
from icici_breeze_backend.app.services.bots.scalping import backtest_store as store
from icici_breeze_backend.app.services.storage import cleanup, elements, usage

GB = 1024**3
_DU = collections.namedtuple("usage", "total used free")


@pytest.fixture
def data(tmp_path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setattr(cfg, "DATA_PATH", str(root) + os.sep)
    usage.ensure_storage_settings_table()
    store.ensure_tables()
    elements.invalidate_cache_memo()
    monkeypatch.setattr(elements, "protected_futures_from", lambda today=None: datetime.date(2026, 9, 23))
    return root


def _disk(monkeypatch, used_pct: float, total: int = 16 * GB) -> None:
    used = int(total * used_pct / 100)
    monkeypatch.setattr(usage.shutil, "disk_usage", lambda _p: _DU(total, used, total - used))


def _rng(a: str, b: str) -> elements.DateRange:
    return elements.DateRange(datetime.date.fromisoformat(a), datetime.date.fromisoformat(b))


def _bars(table: str, code: str, days: list[str]) -> None:
    with sqlite3.connect(store.db_path()) as conn:
        for day in days:
            for minute in range(3):
                conn.execute(
                    f"INSERT INTO {table} (stock_code, ts, open, high, low, close, volume) VALUES (?,?,1,1,1,1,1)",
                    (code, f"{day} 09:1{minute}:00"),
                )


def _count(sql: str, *args) -> int:
    with sqlite3.connect(store.db_path()) as conn:
        return conn.execute(sql, args).fetchone()[0]


# --------------------------------------------------------------------------------------
# Volume and threshold
# --------------------------------------------------------------------------------------


class TestThreshold:
    def test_defaults_to_85(self, data):
        assert usage.get_threshold_pct() == 85

    def test_set_and_bounds(self, data):
        assert usage.set_threshold_pct(70) == 70
        assert usage.get_threshold_pct() == 70
        for bad in (49, 99, "x"):
            with pytest.raises(ValueError):
                usage.set_threshold_pct(bad)

    def test_status_under_and_over(self, data, monkeypatch):
        _disk(monkeypatch, 60)
        s = usage.status()
        assert s["over_threshold"] is False and s["message"] is None
        _disk(monkeypatch, 90)
        s = usage.status()
        assert s["over_threshold"] is True
        assert "90% full" in s["message"] and "Settings → Storage" in s["message"]
        assert usage.halt_reason() == s["message"]

    def test_threshold_is_inclusive(self, data, monkeypatch):
        _disk(monkeypatch, 85)
        assert usage.status()["over_threshold"] is True

    def test_unreadable_volume_never_halts(self, data, monkeypatch):
        def boom(_p):
            raise OSError("no statvfs")

        monkeypatch.setattr(usage.shutil, "disk_usage", boom)
        assert usage.halt_reason() is None
        assert usage.status()["available"] is False


# --------------------------------------------------------------------------------------
# The halt, in the backtest job runner
# --------------------------------------------------------------------------------------


class TestBacktestHalt:
    def test_stop_reason_carries_the_storage_message(self, data, monkeypatch):
        from icici_breeze_backend.app.services.bots import backtest_jobs as jobs

        monkeypatch.setattr(jobs, "market_hours_reason", lambda now=None: None)
        _disk(monkeypatch, 50)
        assert jobs._stop_reason() is None
        _disk(monkeypatch, 95)
        assert "95% full" in jobs._stop_reason()

    def test_new_backtests_are_refused_over_threshold(self, data, monkeypatch):
        from icici_breeze_backend.app.services.bots import backtest_jobs as jobs

        _disk(monkeypatch, 95)
        with pytest.raises(usage.StorageFull):
            jobs.refuse_if_storage_blocked()
        with pytest.raises(usage.StorageFull):
            jobs.start_replay("u1", "momentum", datetime.date(2026, 9, 1), datetime.date(2026, 9, 2), model=True)

    def test_backtest_waits_for_a_cache_cleanup(self, data, monkeypatch):
        from icici_breeze_backend.app.services.bots import backtest_jobs as jobs

        _disk(monkeypatch, 10)
        monkeypatch.setattr(cleanup, "touches_cache", lambda: True)
        with pytest.raises(jobs.Busy):
            jobs.refuse_if_storage_blocked()

    def test_signal_zip_writing_halts_and_leaves_no_partial(self, data, tmp_path):
        from icici_breeze_backend.app.services.index_signal import backtest as signal_backtest

        out = tmp_path / "zips"
        out.mkdir()
        with pytest.raises(usage.StorageFull, match="full"):
            signal_backtest.run_backtest(
                datetime.date(2026, 9, 1), datetime.date(2026, 9, 2), run_id="r" * 36, period="custom",
                holidays=set(), notes=[], out_dir=str(out), halted=lambda: "Storage is 95% full",
            )
        assert os.listdir(out) == []


# --------------------------------------------------------------------------------------
# Deletes
# --------------------------------------------------------------------------------------


class TestDeletes:
    def test_futures_delete_keeps_the_warmup_sessions(self, data):
        _bars("futures_candles", "NIFTY", ["2026-09-18", "2026-09-22", "2026-09-23", "2026-09-24"])
        out = elements.delete("futures_bars", _rng("2026-09-01", "2026-09-30"))
        assert out["deleted"] == 6
        assert "kept for the live signal" in out["note"]
        left = _count("SELECT COUNT(DISTINCT substr(ts,1,10)) FROM futures_candles")
        assert left == 2  # 23rd and 24th

    def test_futures_delete_wholly_inside_the_guard_is_refused(self, data):
        _bars("futures_candles", "NIFTY", ["2026-09-24"])
        out = elements.delete("futures_bars", _rng("2026-09-23", "2026-09-25"))
        assert out["deleted"] == 0 and "Nothing deleted" in out["note"]

    def test_spot_delete_is_by_bar_date_inclusive(self, data):
        _bars("spot_candles", "NIFTY", ["2026-09-01", "2026-09-02", "2026-09-03"])
        assert elements.delete("spot_bars", _rng("2026-09-02", "2026-09-03"))["deleted"] == 6
        assert _count("SELECT COUNT(*) FROM spot_candles") == 3

    def test_option_delete_forgets_overlapping_fetches(self, data):
        with sqlite3.connect(store.db_path()) as conn:
            for ts in ("2026-09-01 10:00:00", "2026-09-05 10:00:00"):
                conn.execute(
                    "INSERT INTO option_candles VALUES ('NIFTY','2026-09-09',25000,'call','1minute',?,1,1,1,1,1,1)",
                    (ts,),
                )
            fetches = [
                ("2026-08-25 09:15:00", "2026-09-01 15:30:00"),  # overlaps -> forgotten
                ("2026-09-04 09:15:00", "2026-09-05 15:30:00"),  # after -> kept
            ]
            for a, b in fetches:
                conn.execute(
                    "INSERT INTO option_fetches VALUES ('NIFTY','2026-09-09',25000,'call','1minute',?,?,1,'x')",
                    (a, b),
                )
        out = elements.delete("option_bars", _rng("2026-09-01", "2026-09-01"))
        assert out["deleted"] == 1
        assert _count("SELECT COUNT(*) FROM option_candles") == 1
        assert _count("SELECT COUNT(*) FROM option_fetches") == 1
        assert _count("SELECT COUNT(*) FROM option_fetches WHERE start_ts LIKE '2026-09-04%'") == 1

    def test_bot_runs_delete_rows_and_trails_but_never_a_running_one(self, data):
        from icici_breeze_backend.audit import bot_audit

        for rid, day, status in (("a" * 36, "2026-09-10", "completed"), ("b" * 36, "2026-09-10", "running"),
                                 ("c" * 36, "2026-09-20", "completed")):
            store.save_run({"id": rid, "user_id": "u1", "bot": "fly", "created_at": f"{day}T18:00:00+05:30",
                            "status": status, "params": {}})
            open(os.path.join(bot_audit.backtest_dir(), bot_audit.backtest_zip_name("u1", "fly", rid)), "wb").close()
        out = elements.delete("bot_backtest_runs", _rng("2026-09-01", "2026-09-15"))
        assert out["deleted"] == 1
        with sqlite3.connect(store.db_path()) as conn:
            ids = sorted(r[0] for r in conn.execute("SELECT id FROM backtest_runs"))
        assert ids == ["b" * 36, "c" * 36]
        left = os.listdir(bot_audit.backtest_dir())
        assert not any("a" * 36 in n for n in left) and len(left) == 2

    def test_signal_zip_delete_keeps_the_row(self, data):
        from icici_breeze_backend.app.services.index_signal import backtest as signal_backtest

        signal_backtest.create_run("s1", "u1", "custom", datetime.date(2026, 8, 1), datetime.date(2026, 8, 30))
        zpath = os.path.join(signal_backtest.runs_dir(), "s1.zip")
        open(zpath, "wb").write(b"z" * 100)
        signal_backtest.update_run("s1", zip_path=zpath)
        day = signal_backtest.get_run("s1")["triggered_at"][:10]
        out = elements.delete("signal_backtest_zips", _rng(day, day))
        assert out["deleted"] == 1
        assert not os.path.exists(zpath)
        row = signal_backtest.get_run("s1")
        assert row is not None and row["zip_path"] is None

    def test_app_logs_keep_the_live_files(self, data):
        from icici_breeze_backend.app.core import log_sink

        os.makedirs(log_sink.logs_dir(), exist_ok=True)
        for name in ("backend.jsonl", "backend.jsonl.1", "chain-builder.jsonl.2"):
            open(os.path.join(log_sink.logs_dir(), name), "w").write("x")
        today = datetime.date.today().isoformat()
        out = elements.delete("app_logs", _rng("2000-01-01", today))
        assert out["deleted"] == 2
        assert os.listdir(log_sink.logs_dir()) == ["backend.jsonl"]

    def test_bot_audit_keeps_today(self, data, monkeypatch):
        from icici_breeze_backend.audit import bot_audit

        monkeypatch.setattr(elements, "now_ist", lambda: datetime.datetime(2026, 9, 25, 12, 0))
        for day in ("2026-09-24", "2026-09-25"):
            open(os.path.join(bot_audit.audit_dir(), f"u1__fly__{day}.jsonl"), "w").write("x")
        out = elements.delete("bot_audit_logs", _rng("2026-09-01", "2026-09-30"))
        assert out["deleted"] == 1
        assert [n for n in os.listdir(bot_audit.audit_dir()) if n.endswith(".jsonl")] == ["u1__fly__2026-09-25.jsonl"]

    def test_strategy_audit_by_file_date(self, data):
        from icici_breeze_backend.audit import strategy_builder_audit

        d = strategy_builder_audit.audit_log_dir()
        for name in ("20260701T101010Z_u_NIFTY_a.json", "20260910T101010Z_u_NIFTY_b.json"):
            open(os.path.join(d, name), "w").write("{}")
        assert elements.delete("strategy_audit_logs", _rng("2026-07-01", "2026-07-31"))["deleted"] == 1
        assert os.listdir(d) == ["20260910T101010Z_u_NIFTY_b.json"]

    def test_read_only_elements_refuse(self, data):
        with pytest.raises(ValueError):
            elements.delete("users_db", _rng("2026-01-01", "2026-01-02"))

    def test_backwards_range_is_refused(self):
        with pytest.raises(ValueError):
            _rng("2026-09-05", "2026-09-01")


# --------------------------------------------------------------------------------------
# Inventory
# --------------------------------------------------------------------------------------


class TestInventory:
    def test_elements_add_up_to_the_volume(self, data, monkeypatch):
        _bars("futures_candles", "NIFTY", ["2026-09-01", "2026-09-02"])
        _bars("futures_candles", "SENSEX", ["2026-09-02"])
        _bars("spot_candles", "NIFTY", ["2026-09-01"])
        open(os.path.join(cfg.DATA_PATH, cfg.USERS_DB), "ab").close()
        _disk(monkeypatch, 40)
        inv = elements.inventory()
        by_key = {e["key"]: e for e in inv["elements"]}
        assert sum(e["bytes"] for e in inv["elements"]) == usage.volume()["used_bytes"]
        fut = by_key["futures_bars"]
        assert fut["approx"] is True and fut["deletable"] is True
        assert (fut["from"], fut["to"]) == ("2026-09-01", "2026-09-02")
        assert {s["name"]: s["days"] for s in fut["series"]} == {"NIFTY": 2, "SENSEX": 1}
        assert by_key["users_db"]["deletable"] is False
        assert by_key["span_archives"]["deletable"] is False

    def _options(self, n_days: int = 20) -> None:
        with sqlite3.connect(store.db_path()) as conn:
            for d in range(1, n_days + 1):
                for m in range(30):
                    conn.execute(
                        "INSERT INTO option_candles VALUES ('NIFTY','2026-09-29',25000,'call','1minute',?,1,1,1,1,1,1)",
                        (f"2026-08-{d:02d} 10:{m:02d}:00",),
                    )
            conn.execute(
                "INSERT INTO option_fetches VALUES ('NIFTY','2026-09-29',25000,'call','1minute',"
                "'2026-08-01 09:15:00','2026-08-20 15:30:00',600,'x')"
            )
            conn.execute(  # an empty window is not coverage
                "INSERT INTO option_fetches VALUES ('NIFTY','2026-09-29',26000,'put','1minute',"
                "'2026-07-01 09:15:00','2026-07-02 15:30:00',0,'x')"
            )

    def test_option_candles_are_never_scanned(self, data, monkeypatch):
        # On a deployment this table is ~2 GB with no index on ts: one scan in the request outlasted
        # nginx's 60 s timeout. Only a one-row existence probe may touch it.
        self._options()
        seen: list[str] = []
        real_connect = sqlite3.connect

        def traced(*args, **kwargs):
            conn = real_connect(*args, **kwargs)
            conn.set_trace_callback(seen.append)
            return conn

        monkeypatch.setattr(elements.sqlite3, "connect", traced)
        elements.invalidate_cache_memo()
        elements.cache_breakdown()
        touching = [q for q in seen if "option_candles" in q]
        assert touching == ["SELECT 1 FROM option_candles LIMIT 1"], touching

    def test_option_candles_take_the_remainder_and_date_from_fetches(self, data):
        self._options()
        b = elements.cache_breakdown()
        est = sum(t["bytes"] for t in b["tables"].values())
        assert est + b["free_bytes"] == b["file_bytes"]
        others = sum(t["bytes"] for k, t in b["tables"].items() if k != "option_candles")
        assert b["tables"]["option_candles"]["bytes"] == b["file_bytes"] - b["free_bytes"] - others > 0
        cov = b["coverage"]["option_candles"]
        assert (cov["from"], cov["to"], cov["contracts"]) == ("2026-08-01", "2026-08-20", 1)

    def test_concurrent_requests_share_one_measurement(self, data, monkeypatch):
        import threading

        calls = []
        real = elements._cache_coverage

        def slow(conn, present):
            calls.append(1)
            time.sleep(0.2)
            return real(conn, present)

        monkeypatch.setattr(elements, "_cache_coverage", slow)
        elements.invalidate_cache_memo()
        threads = [threading.Thread(target=elements.cache_breakdown) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(calls) == 1

    def test_cache_estimates_add_up_to_the_file(self, data):
        _bars("futures_candles", "NIFTY", [f"2026-08-{d:02d}" for d in range(1, 29)])
        b = elements.cache_breakdown()
        est = sum(t["bytes"] for t in b["tables"].values())
        assert abs((est + b["free_bytes"]) - b["file_bytes"]) <= len(b["tables"])  # int truncation


# --------------------------------------------------------------------------------------
# The cleanup job and its routes
# --------------------------------------------------------------------------------------


def _wait_for_job(timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = cleanup.state()
        if s and not s["running"]:
            return s
        time.sleep(0.02)
    raise AssertionError("cleanup job did not finish")


class TestCleanupJob:
    def test_cache_delete_compacts(self, data, monkeypatch):
        _disk(monkeypatch, 10)
        _bars("spot_candles", "NIFTY", [f"2026-08-{d:02d}" for d in range(1, 29)])
        cleanup.start("spot_bars", _rng("2026-08-01", "2026-08-31"))
        job = _wait_for_job()
        assert job["status"] == "completed", job
        assert job["compacted"] is True
        assert elements.cache_free_bytes() == 0

    def test_too_full_to_compact_still_deletes(self, data, monkeypatch):
        total = 16 * GB
        monkeypatch.setattr(usage.shutil, "disk_usage", lambda _p: _DU(total, total - 10, 10))
        _bars("spot_candles", "NIFTY", ["2026-08-01"])
        cleanup.start("spot_bars", _rng("2026-08-01", "2026-08-01"))
        job = _wait_for_job()
        assert job["status"] == "completed" and job["compacted"] is False
        assert "too full to compact" in job["message"]
        assert _count("SELECT COUNT(*) FROM spot_candles") == 0

    def test_cache_delete_refused_while_a_backtest_runs(self, data, monkeypatch):
        from icici_breeze_backend.app.services.bots import backtest_jobs as jobs

        monkeypatch.setattr(jobs, "is_running", lambda: True)
        with pytest.raises(ValueError, match="backtest is running"):
            cleanup.start("option_bars", _rng("2026-08-01", "2026-08-01"))


def _client() -> TestClient:
    from icici_breeze_backend.app.api.v1 import route_storage

    app = FastAPI()
    app.include_router(route_storage.router, prefix="/api/settings/storage")

    async def _user():
        return RequestContext(user_id="u1", username="u1", roles=["trader"], is_authenticated=True,
                              broker_token="t")

    app.dependency_overrides[get_current_user] = _user
    return TestClient(app)


class TestRoutes:
    def test_status_threshold_and_delete(self, data, monkeypatch):
        _disk(monkeypatch, 80)
        client = _client()
        s = client.get("/api/settings/storage/status").json()
        assert s["over_threshold"] is False and s["threshold_pct"] == 85
        s = client.put("/api/settings/storage/threshold", json={"threshold_pct": 75}).json()
        assert s["over_threshold"] is True
        assert client.put("/api/settings/storage/threshold", json={"threshold_pct": 10}).status_code == 400
        inv = client.get("/api/settings/storage").json()
        assert inv["threshold_bounds"] == {"min": 50, "max": 98, "default": 85}
        assert any(e["key"] == "option_bars" for e in inv["elements"])
        r = client.post("/api/settings/storage/delete",
                        json={"element": "daily_vix", "from_date": "2026-01-01", "to_date": "2026-01-31"})
        assert r.status_code == 200
        _wait_for_job()
        assert client.get("/api/settings/storage/job").json()["job"]["status"] == "completed"
        bad = client.post("/api/settings/storage/delete",
                          json={"element": "daily_vix", "from_date": "2026-02-01", "to_date": "2026-01-01"})
        assert bad.status_code == 400
        json.dumps(inv)  # the whole inventory is plain JSON
