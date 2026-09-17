"""Activity-log bundling (services.bots.run_bundles) and the date-bounded run-log routes."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from icici_breeze_backend.app.api.deps_license import require_trading_not_revoked
from icici_breeze_backend.app.api.v1.route_bots import router
from icici_breeze_backend.app.auth.context import RequestContext, get_request_context
from icici_breeze_backend.app.db.bots_migrate import (
    BOT_EXPIRY_INDEX_WRITER,
    BOT_MOMENTUM_LONG_SCALPER,
    ensure_bots_tables,
)
from icici_breeze_backend.app.domain.bots import BotRunRecord
from icici_breeze_backend.app.repositories import bots as repo
from icici_breeze_backend.app.services.bots.run_bundles import bundle_runs
from icici_breeze_backend.audit import bot_audit

W = BOT_EXPIRY_INDEX_WRITER
S = BOT_MOMENTUM_LONG_SCALPER


def _run(n, bot, started, trigger="schedule", status="skipped", code="quote_unavailable"):
    return BotRunRecord(
        id=f"r{n}",
        bot_type=bot,
        trigger=trigger,
        status=status,
        reason_code=code,
        reason_text=code,
        started_at=started,
    )


def _newest_first(runs):
    return list(reversed(runs))


def test_back_to_back_runs_bundle_and_a_different_run_splits_them():
    """The screenshot's day: a manual proposal between scheduled skips makes three bundles."""
    runs = [
        _run(1, W, "2026-09-17 09:42:12"),
        _run(2, W, "2026-09-17 10:00:25"),
        _run(3, W, "2026-09-17 10:01:11", trigger="manual", status="proposed", code="proposal_ready"),
        _run(4, W, "2026-09-17 10:16:27"),
        _run(5, W, "2026-09-17 10:24:33"),
    ]
    bundles = bundle_runs(_newest_first(runs))
    assert [(b.trigger, b.status, b.count) for b in bundles] == [
        ("schedule", "skipped", 2),
        ("manual", "proposed", 1),
        ("schedule", "skipped", 2),
    ]
    assert bundles[0].latest.id == "r5"
    assert bundles[0].first_started_at == "2026-09-17 10:16:27"
    assert bundles[2].last_started_at == "2026-09-17 10:00:25"


def test_another_bots_runs_in_between_do_not_split_a_bundle():
    runs = [
        _run(1, W, "2026-09-17 09:42:00"),
        _run(2, S, "2026-09-17 09:42:30", trigger="session", status="running", code="not_warm"),
        _run(3, W, "2026-09-17 09:44:00"),
        _run(4, S, "2026-09-17 09:45:00", trigger="session", status="completed", code="done"),
        _run(5, W, "2026-09-17 09:46:00"),
    ]
    bundles = bundle_runs(_newest_first(runs))
    assert [(b.bot_type, b.status, b.count) for b in bundles] == [
        (W, "skipped", 3),
        (S, "completed", 1),
        (S, "running", 1),
    ]


def test_a_new_day_starts_a_new_bundle():
    runs = [_run(1, W, "2026-09-16 15:20:00"), _run(2, W, "2026-09-17 09:16:00")]
    bundles = bundle_runs(_newest_first(runs))
    assert [(b.date, b.count) for b in bundles] == [("2026-09-17", 1), ("2026-09-16", 1)]


def test_distinct_reasons_are_counted():
    runs = [
        _run(1, W, "2026-09-17 09:42:00", code="quote_unavailable"),
        _run(2, W, "2026-09-17 09:44:00", code="no_signal"),
        _run(3, W, "2026-09-17 09:46:00", code="quote_unavailable"),
    ]
    (bundle,) = bundle_runs(_newest_first(runs))
    assert bundle.count == 3 and bundle.distinct_reasons == 2


def test_audit_link_is_per_bundle_for_live_runs_but_per_run_for_backtests():
    live = [_run(1, W, "2026-09-17 09:42:00"), _run(2, W, "2026-09-17 09:44:00")]
    for r in live:
        r.audit_log = "day.jsonl"
    backtests = [
        _run(3, S, "2026-09-17 09:50:00", trigger="backtest", status="completed", code="ok"),
        _run(4, S, "2026-09-17 09:55:00", trigger="backtest", status="completed", code="ok"),
    ]
    backtests[0].audit_log, backtests[1].audit_log = "bt3.jsonl", "bt4.jsonl"
    by_bot = {b.bot_type: b for b in bundle_runs(_newest_first(live + backtests))}
    assert by_bot[W].audit_log == "day.jsonl"
    assert by_bot[S].count == 2 and by_bot[S].audit_log is None
    (lone,) = bundle_runs([backtests[1]])
    assert lone.audit_log == "bt4.jsonl"


# --- routes ----------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = str(tmp_path / "users_test.sqlite3")
    monkeypatch.setattr(repo, "_db_path", lambda: path)
    ensure_bots_tables(path)
    monkeypatch.setattr(bot_audit, "find_for_run", lambda _u, bot, started: f"{bot}-{started[:10]}")

    async def _ctx():
        return RequestContext(
            user_id="user1", username="user1", roles=["trader"], is_authenticated=True,
            broker_token=None,
        )

    app = FastAPI()
    app.include_router(router, prefix="/bots")
    app.dependency_overrides[get_request_context] = _ctx
    app.dependency_overrides[require_trading_not_revoked] = lambda: None
    with TestClient(app) as c:
        yield c, path


def _insert(path, n, bot, started, trigger="schedule", status="skipped"):
    import sqlite3

    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO bot_runs (id, user_id, bot_type, trigger, status, reason_code, "
            "reason_text, started_at) VALUES (?, 'user1', ?, ?, ?, 'quote_unavailable', 'x', ?)",
            (f"r{n}", bot, trigger, status, started),
        )


def test_date_range_is_inclusive_and_ignores_the_row_limit(client):
    c, path = client
    _insert(path, 0, W, "2026-09-09 23:59:59")
    for i in range(60):
        _insert(path, i + 1, W, f"2026-09-10 09:{i:02d}:00")
    _insert(path, 99, W, "2026-09-17 23:59:59")
    _insert(path, 100, W, "2026-09-18 00:00:00")

    r = c.get("/bots/runs?date_from=2026-09-10&date_to=2026-09-17")
    assert r.status_code == 200
    ids = [row["id"] for row in r.json()]
    assert len(ids) == 61 and "r0" not in ids and "r100" not in ids
    assert ids[0] == "r99"
    assert r.json()[0]["audit_log"] == f"{W}-2026-09-17"
    # Unbounded calls keep their old default cap.
    assert len(c.get("/bots/runs").json()) == 50


def test_started_window_fetches_exactly_a_bundles_runs(client):
    c, path = client
    _insert(path, 1, W, "2026-09-17 09:42:00")
    _insert(path, 2, W, "2026-09-17 09:44:00", trigger="manual", status="proposed")
    _insert(path, 3, W, "2026-09-17 09:46:00")
    _insert(path, 4, S, "2026-09-17 09:44:00")

    r = c.get(
        f"/bots/runs?bot_type={W}&trigger=schedule&status=skipped"
        "&started_from=2026-09-17 09:44:00&started_to=2026-09-17 09:46:00"
    )
    assert [row["id"] for row in r.json()] == ["r3"]


def test_range_validation(client):
    c, _ = client
    assert c.get("/bots/runs?date_from=2026-09-10").status_code == 400
    assert c.get("/bots/runs?date_from=2026-09-10&date_to=2026-09-09").status_code == 400
    assert c.get("/bots/runs?date_from=2026-08-01&date_to=2026-09-01").status_code == 400
    assert c.get("/bots/runs?date_from=2026-08-02&date_to=2026-09-01").status_code == 200
    assert c.get("/bots/runs?started_from=bad&started_to=bad").status_code == 400
    assert c.get("/bots/runs/bundles?date_from=2026-08-01").status_code == 422


def test_bundles_route_bundles_server_side(client):
    c, path = client
    for i in range(40):
        _insert(path, i, W, f"2026-09-{1 + i % 20:02d} 10:00:00")
    _insert(path, 99, W, "2026-09-17 10:05:00", trigger="manual", status="proposed")
    _insert(path, 100, W, "2026-09-17 10:10:00")

    r = c.get("/bots/runs/bundles?date_from=2026-08-19&date_to=2026-09-17")
    assert r.status_code == 200
    body = r.json()
    assert [(b["date"], b["trigger"], b["count"]) for b in body[:3]] == [
        ("2026-09-17", "schedule", 1),
        ("2026-09-17", "manual", 1),
        ("2026-09-17", "schedule", 2),
    ]
    assert body[0]["audit_log"] == f"{W}-2026-09-17"
    # Sept 18-20 fall outside the range: 34 of the 40 dated rows plus the two on the 17th.
    assert sum(b["count"] for b in body) == 36
