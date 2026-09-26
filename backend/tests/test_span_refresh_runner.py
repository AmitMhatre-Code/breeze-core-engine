"""SPAN refresh in a child process (design-decisions #46): the runner, the worker, and the
streamed publish that no longer keeps every sheet resident in the API process."""
from __future__ import annotations

import json
import sqlite3
import subprocess

import pytest

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.db import redis_client
from icici_breeze_backend.app.services import nsccl_baseline
from icici_breeze_backend.app.services.reference_data import span_baseline_store, span_refresh_runner
from icici_breeze_backend.workers import span_refresh

_OK = {
    "nse": {"Status": 200, "Error": "", "Success": {"skipped": False, "inserted_rows": 3}},
    "bse": {"Status": 400, "Error": "Could not find a published BSE SPAN archive", "Success": None},
}


@pytest.fixture
def real_redis(monkeypatch):
    """Pretend a real Redis is connected, so the runner takes the child-process path."""
    monkeypatch.setattr(redis_client, "redis_using_memory_fallback", lambda: False)


@pytest.fixture
def mirror_resets(monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(span_baseline_store, "reset_local_mirror", lambda: calls.append(1))
    return calls


def _fake_run(returncode: int, write: dict | None = None, seen: list | None = None):
    def run(cmd, **kwargs):
        if seen is not None:
            seen.append(cmd)
        if write is not None:
            with open(cmd[cmd.index("--result-file") + 1], "w", encoding="utf-8") as fh:
                json.dump(write, fh)
        return subprocess.CompletedProcess(cmd, returncode)

    return run


def test_child_result_is_returned_and_the_mirror_is_reset(real_redis, mirror_resets, monkeypatch):
    seen: list = []
    monkeypatch.setattr(subprocess, "run", _fake_run(0, write=_OK, seen=seen))

    out = span_refresh_runner.refresh_all_span_baselines(force=True)

    assert out == _OK
    assert seen[0][1:3] == ["-m", span_refresh_runner.WORKER_MODULE]
    assert "--force" in seen[0]
    # The child published into the live version under the same keys; cached sheets are stale.
    assert mirror_resets == [1]


def test_an_oom_killed_child_fails_both_markets_but_not_the_caller(real_redis, mirror_resets, monkeypatch):
    """The point of the child: the kernel takes it instead of uvicorn."""
    monkeypatch.setattr(subprocess, "run", _fake_run(-9))

    out = span_refresh_runner.refresh_all_span_baselines()

    assert set(out) == {"nse", "bse"}
    assert all(r["Status"] == 500 and "out of memory" in r["Error"] for r in out.values())
    assert mirror_resets == [1]


def test_a_hung_child_times_out(real_redis, mirror_resets, monkeypatch):
    def run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))

    monkeypatch.setattr(subprocess, "run", run)

    out = span_refresh_runner.refresh_all_span_baselines()

    assert all("timed out" in r["Error"] for r in out.values())


def test_a_child_that_leaves_no_result_is_a_failure(real_redis, mirror_resets, monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run(0))

    out = span_refresh_runner.refresh_all_span_baselines()

    assert all(r["Status"] == 500 for r in out.values())


def test_memory_fallback_refreshes_in_process(monkeypatch):
    """A child's publish would land in its own in-memory store and vanish with it."""
    monkeypatch.setattr(redis_client, "redis_using_memory_fallback", lambda: True)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("must not spawn"))
    monkeypatch.setattr(nsccl_baseline, "refresh_all_span_baselines_in_process", lambda force=False: _OK)

    assert span_refresh_runner.refresh_all_span_baselines() == _OK


def test_worker_writes_its_result_for_the_parent(tmp_path, monkeypatch):
    import icici_breeze_backend.app.core.logging as app_logging

    forced: list[bool] = []
    # All three touch the test process itself: .env into os.environ, a log sink, and nice(10).
    monkeypatch.setattr(span_refresh, "load_env", lambda: None)
    monkeypatch.setattr(app_logging, "configure_logging", lambda **kw: None)
    monkeypatch.setattr(span_refresh, "_deprioritise", lambda: None)
    monkeypatch.setattr(
        nsccl_baseline,
        "refresh_all_span_baselines_in_process",
        lambda force=False: (forced.append(force), _OK)[1],
    )
    result_file = tmp_path / "result.json"

    assert span_refresh.main(["--result-file", str(result_file), "--force"]) == 0

    assert json.loads(result_file.read_text()) == _OK
    assert forced == [True]


# --- streamed publish ---------------------------------------------------------------------


@pytest.fixture
def baseline_db(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "DATA_PATH", str(tmp_path) + "/")
    monkeypatch.setattr(cfg, "SCRIP_DB", "scrips.sqlite3")
    conn = sqlite3.connect(tmp_path / "scrips.sqlite3")
    conn.execute("CREATE TABLE IF NOT EXISTS scrip_master (ShortName TEXT, ExchangeCode TEXT)")
    conn.commit()
    conn.close()
    nsccl_baseline.ensure_exchange_margin_baseline_table()
    return tmp_path


def _insert(short_name: str, strike: float, option_type: str, margin: float) -> None:
    with sqlite3.connect(cfg.DATA_PATH + cfg.SCRIP_DB) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO exchange_margin_baseline (
                exchange_code, short_name, expiry_date, strike_price, option_type,
                margin_per_lot, lot_size, risk_array, source_file, source_date,
                source_version, refreshed_at
            ) VALUES ('NFO', ?, '29-Sep-2026', ?, ?, ?, 75, NULL,
                      'nsccl.20260925.i5.zip:x.spn', '20260925', 5, datetime('now'))
            """,
            (short_name, strike, option_type, margin),
        )
        conn.commit()


def test_publish_keeps_no_sheet_resident_yet_lookups_still_resolve(baseline_db):
    _insert("NIFTY", 24000, "CE", 1000.0)
    _insert("NIFTY", 24000, "PE", 900.0)
    _insert("RELIANCE", 1400, "CE", 500.0)

    span_baseline_store.publish_span_baseline_from_db()

    assert span_baseline_store._local["by_sheet"] == {}
    sheet = span_baseline_store.get_span_baseline_sheet("NFO", "NIFTY", "29-Sep-2026")
    assert sheet["found"] is True
    assert set(sheet["contracts"]) == {"24000:CE", "24000:PE"}


def test_a_sheet_split_by_stored_spelling_is_published_whole(baseline_db):
    """The stream is ordered by the stored name, the sheet key by the normalised one: a row
    stored as 'nifty' sorts after 'RELIANCE' and reopens the NIFTY sheet."""
    _insert("NIFTY", 24000, "CE", 1000.0)
    _insert("RELIANCE", 1400, "CE", 500.0)
    _insert("nifty", 24100, "CE", 950.0)

    span_baseline_store.publish_span_baseline_from_db()

    sheet = span_baseline_store.get_span_baseline_sheet("NFO", "NIFTY", "29-Sep-2026")
    assert set(sheet["contracts"]) == {"24000:CE", "24100:CE"}
