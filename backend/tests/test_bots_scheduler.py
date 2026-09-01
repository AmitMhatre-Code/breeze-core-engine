"""The sweep that drives Bot 2 (services.bots.scheduler).

`decide()` has its own tests; these cover what the scheduler adds — idempotency across
ticks, the read-only gate, and that a fired-but-unprotected position is never reported as
a clean success.
"""
from __future__ import annotations

import datetime

import pytest

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.db.bots_migrate import (
    BOT_EXPIRY_INDEX_WRITER,
    ensure_bots_tables,
)
from icici_breeze_backend.app.domain.bots import IndexWriterLeg, ReasonCode
from icici_breeze_backend.app.repositories import bots as repo
from icici_breeze_backend.app.services.bots import expiry_index_writer as bot2
from icici_breeze_backend.app.services.bots import scheduler


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = str(tmp_path / "users_test.sqlite3")
    monkeypatch.setattr(repo, "_db_path", lambda: path)
    ensure_bots_tables(path)
    monkeypatch.setattr(scheduler, "_last_nag", {})
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.deployment_license_status.trading_mutations_allowed",
        lambda: True,
    )
    return path


class FakeProc:
    def __init__(self, session=True, available=1_000_000.0):
        self._session = session
        self.available = available

    def fetch_stock_codes(self, exchange_code=cfg.NFO):
        return []

    def get_session_breeze(self, user_id):
        return object() if self._session else None

    def get_margin_situation(self, user_id, target):
        return {"Status": 200, "Success": {"actual_margin_avl": self.available}}

    def get_strategy_builder_margin_source(self, user_id):
        return "breeze_api"


def enable_bot(user_id="u1", **cfg_kw):
    indices = cfg_kw.pop("indices", {"NIFTY": IndexWriterLeg(enabled=True, priority=1).model_dump()})
    repo.update_bot(user_id, BOT_EXPIRY_INDEX_WRITER,
                    enabled=True, config={"indices": indices, **cfg_kw})


def patch_decision(monkeypatch, decision, expiring=None):
    monkeypatch.setattr(scheduler, "_expiring_today", lambda proc: expiring or {"NIFTY": "03-Sep-2026"})
    monkeypatch.setattr(bot2, "decide", lambda ctx: decision)


def test_a_disabled_bot_is_never_swept(db, monkeypatch):
    called = []
    monkeypatch.setattr(scheduler, "_expiring_today", lambda proc: called.append(1) or {})
    scheduler.tick(FakeProc())
    assert called == [], "no enabled bots means no scrip-master work at all"
    assert repo.list_runs("u1") == []


def test_a_skip_is_logged_once_and_not_repeated(db, monkeypatch):
    """The sweep runs every 30s; a resolved day must not re-log on every tick."""
    enable_bot()
    patch_decision(
        monkeypatch,
        bot2.TickDecision("skip", ReasonCode.NOT_AN_EXPIRY_DAY, "No expiry today."),
    )
    scheduler.tick(FakeProc())
    scheduler.tick(FakeProc())
    scheduler.tick(FakeProc())

    runs = repo.list_runs("u1")
    assert len(runs) == 1
    assert runs[0].reason_code == ReasonCode.NOT_AN_EXPIRY_DAY


def test_idle_writes_nothing(db, monkeypatch):
    enable_bot()
    patch_decision(monkeypatch, bot2.TickDecision("idle"))
    scheduler.tick(FakeProc())
    assert repo.list_runs("u1") == []


def test_a_nag_is_sent_and_rate_limited_by_the_decision_layer(db, monkeypatch):
    enable_bot()
    sent = []
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.telegram_alerts.notify_bot_needs_login",
        lambda user_id, text: sent.append((user_id, text)),
    )
    patch_decision(
        monkeypatch,
        bot2.TickDecision("nag", ReasonCode.NO_BROKER_SESSION, "Log in please.", ("NIFTY",)),
    )
    scheduler.tick(FakeProc(session=False))
    assert sent == [("u1", "Log in please.")]
    # A nag is not a resolution -- the day must stay open so the bot can still fire.
    assert repo.list_runs("u1") == []
    assert scheduler._last_nag["u1"] is not None


def test_read_only_mode_blocks_the_fire_with_its_own_reason(db, monkeypatch):
    enable_bot()
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.deployment_license_status.trading_mutations_allowed",
        lambda: False,
    )
    patch_decision(monkeypatch, bot2.TickDecision("fire", None, None, ("NIFTY",)))
    fired = []
    monkeypatch.setattr(bot2, "fire_index", lambda *a, **k: fired.append(1))

    scheduler.tick(FakeProc())
    assert fired == [], "nothing may be traded in read-only mode"
    run = repo.list_runs("u1")[0]
    assert run.status == "skipped"
    assert run.reason_code == ReasonCode.TRADING_READ_ONLY


def test_a_successful_fire_is_logged_with_its_legs(db, monkeypatch):
    enable_bot()
    patch_decision(monkeypatch, bot2.TickDecision("fire", None, None, ("NIFTY",)))
    result = bot2.FireResult(
        index_code="NIFTY", exchange_code=cfg.NFO, expiry_display="03-Sep-2026",
        right="put", strike_price=23500.0, lots=2, quantity=150, entry_price=42.0,
        order_ids=["OID1"], rule_id="rule-1",
    )
    monkeypatch.setattr(bot2, "fire_index", lambda *a, **k: result)

    scheduler.tick(FakeProc())
    run = repo.list_runs("u1")[0]
    assert run.status == "completed"
    assert run.reason_code == ReasonCode.ORDERS_PLACED
    assert "Sold 2 lot(s) NIFTY 23500 PE" in run.reason_text
    assert run.detail["legs"][0]["rule_id"] == "rule-1"


def test_a_position_left_without_a_stop_is_never_a_clean_success(db, monkeypatch):
    """The worst state this bot can leave behind — it must be loud in the log."""
    enable_bot()
    patch_decision(monkeypatch, bot2.TickDecision("fire", None, None, ("NIFTY",)))
    result = bot2.FireResult(
        index_code="NIFTY", exchange_code=cfg.NFO, expiry_display="03-Sep-2026",
        right="put", strike_price=23500.0, lots=2, quantity=150, entry_price=42.0,
        order_ids=["OID1"], rule_id=None,
    )
    monkeypatch.setattr(bot2, "fire_index", lambda *a, **k: result)

    scheduler.tick(FakeProc())
    run = repo.list_runs("u1")[0]
    assert run.status == "failed"
    assert "WITHOUT a stop" in run.reason_text


def test_a_margin_cap_miss_is_a_skip_not_a_failure(db, monkeypatch):
    """Declining to trade because one lot is unaffordable is correct behaviour."""
    enable_bot()
    patch_decision(monkeypatch, bot2.TickDecision("fire", None, None, ("NIFTY",)))
    result = bot2.FireResult(
        index_code="NIFTY", exchange_code=cfg.NFO, expiry_display="03-Sep-2026",
        right="put", reason_code=ReasonCode.MARGIN_CAP_TOO_SMALL,
        error="One lot needs more than the cap.",
    )
    monkeypatch.setattr(bot2, "fire_index", lambda *a, **k: result)

    scheduler.tick(FakeProc())
    run = repo.list_runs("u1")[0]
    assert run.status == "skipped"
    assert run.reason_code == ReasonCode.MARGIN_CAP_TOO_SMALL


def test_unreadable_margin_fails_rather_than_guessing(db, monkeypatch):
    enable_bot()
    patch_decision(monkeypatch, bot2.TickDecision("fire", None, None, ("NIFTY",)))
    fired = []
    monkeypatch.setattr(bot2, "fire_index", lambda *a, **k: fired.append(1))

    scheduler.tick(FakeProc(available=0.0))
    assert fired == []
    assert repo.list_runs("u1")[0].reason_code == ReasonCode.BROKER_ERROR


def test_an_exception_mid_fire_closes_the_run(db, monkeypatch):
    """A crash must not leave the run `running` — that is what the reaper exists for, but
    the happy path should not need it."""
    enable_bot()
    patch_decision(monkeypatch, bot2.TickDecision("fire", None, None, ("NIFTY",)))

    def boom(*a, **k):
        raise RuntimeError("broker exploded")

    monkeypatch.setattr(bot2, "fire_index", boom)
    scheduler.tick(FakeProc())
    run = repo.list_runs("u1")[0]
    assert run.status == "failed"
    assert run.reason_code == ReasonCode.INTERNAL_ERROR


def test_the_sweep_reaps_hung_runs(db, monkeypatch):
    import sqlite3

    run_id = repo.start_run("u1", BOT_EXPIRY_INDEX_WRITER, "schedule")
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE bot_runs SET started_at='2000-01-01 00:00:00' WHERE id=?", (run_id,))
        conn.commit()
    scheduler.tick(FakeProc())
    assert repo.list_runs("u1")[0].reason_code == ReasonCode.INTERRUPTED
