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
    BOT_HOLDINGS_WRITER,
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
    # The sweep reads the real wall clock; pin it inside the session so these tests do not
    # depend on what time of day the suite happens to run.
    monkeypatch.setattr(scheduler, "_market_has_opened", lambda: True)
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
    # These tests cover the autonomous auto-fire path; the config default is now the
    # Telegram-approval path, so opt in explicitly. HITL routing has its own tests.
    cfg_kw.setdefault("approval_mode", "auto")
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


def test_nothing_is_logged_before_the_open(db, monkeypatch):
    """A deployment powered on at 08:00 logged the day's skip at 08:00."""
    enable_bot()
    monkeypatch.setattr(scheduler, "_market_has_opened", lambda: False)
    patch_decision(
        monkeypatch,
        bot2.TickDecision("skip", ReasonCode.NOT_AN_EXPIRY_DAY, "No expiry today."),
    )
    scheduler.tick(FakeProc())
    assert repo.list_runs("u1") == []

    # ...and is logged on the first sweep after the open.
    monkeypatch.setattr(scheduler, "_market_has_opened", lambda: True)
    scheduler.tick(FakeProc())
    assert [r.reason_code for r in repo.list_runs("u1")] == [ReasonCode.NOT_AN_EXPIRY_DAY]


def test_nothing_fires_before_the_open(db, monkeypatch):
    enable_bot(entry_time_ist="08:30")
    monkeypatch.setattr(scheduler, "_market_has_opened", lambda: False)
    fired = []
    monkeypatch.setattr(bot2, "fire_index", lambda *a, **k: fired.append(1))
    patch_decision(monkeypatch, bot2.TickDecision("fire", None, None, ("NIFTY",)))
    scheduler.tick(FakeProc())
    assert fired == []
    assert repo.list_runs("u1") == []


def test_the_login_nag_still_goes_out_before_the_open(db, monkeypatch):
    """The nag exists to get the user logged in before the entry time, so it is exempt."""
    enable_bot()
    monkeypatch.setattr(scheduler, "_market_has_opened", lambda: False)
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
    assert run.status == "partial"
    assert "WITHOUT a stop" in run.reason_text


def test_a_stop_skipped_for_an_existing_position_is_partial_not_failed(db, monkeypatch):
    """The screenshot case: the legs the user approved went on, and only the stop was
    declined (a Strategy Group rule there would have pooled P&L with another position).

    Reporting that as `failed` said the run did nothing, which is the one reading that is
    definitely wrong -- a live short was open and needed a stop set by hand.
    """
    enable_bot()
    patch_decision(monkeypatch, bot2.TickDecision("fire", None, None, ("NIFTY",)))
    result = bot2.FireResult(
        index_code="NIFTY", exchange_code=cfg.NFO, expiry_display="03-Sep-2026",
        right="put", strike_price=23500.0, lots=2, quantity=150, entry_price=42.0,
        order_ids=["OID1"], rule_id=None,
        reason_code=ReasonCode.EXIT_ARM_SKIPPED_EXISTING_POSITION,
        arm_error="1 other open leg(s) already exist",
        error="Position is OPEN but no stop was armed: 1 other open leg(s) already exist",
    )
    monkeypatch.setattr(bot2, "fire_index", lambda *a, **k: result)
    monkeypatch.setattr(bot2, "notify_arm_skipped", lambda *a, **k: None)

    scheduler.tick(FakeProc())
    run = repo.list_runs("u1")[0]

    assert run.status == "partial"
    assert run.reason_code == ReasonCode.EXIT_ARM_SKIPPED_EXISTING_POSITION
    assert "Sold 2 lot(s) NIFTY" in run.reason_text, "what went on comes first"
    assert "no stop was armed" in run.reason_text
    assert run.detail["legs"][0]["order_ids"] == ["OID1"]


def test_a_stop_waiting_on_its_fills_is_pending_not_unprotected(db, monkeypatch):
    """Orders out, stop waiting for them to finish filling. `exit_arming` arms it on the
    last fill, so this is neither a failure nor a position left WITHOUT a stop -- and the
    run's note is the exact text `exit_arming` rewrites once the stop arms."""
    enable_bot()
    patch_decision(monkeypatch, bot2.TickDecision("fire", None, None, ("NIFTY",)))
    result = bot2.FireResult(
        index_code="NIFTY", exchange_code=cfg.NFO, expiry_display="03-Sep-2026",
        right="put", strike_price=23500.0, lots=2, quantity=150, entry_price=42.0,
        order_ids=["OID1"], rule_id=None, arm_pending=True,
        reason_code=ReasonCode.EXIT_ARM_PENDING,
    )
    seen_run_ids = []

    def fake_fire(*a, run_id=None, **k):
        seen_run_ids.append(run_id)
        return result

    monkeypatch.setattr(bot2, "fire_index", fake_fire)

    scheduler.tick(FakeProc())
    run = repo.list_runs("u1")[0]

    assert run.status == "completed"
    assert run.reason_code == ReasonCode.EXIT_ARM_PENDING
    assert "WITHOUT a stop" not in run.reason_text
    assert "NIFTY stop arms once every order fills" in run.reason_text
    assert seen_run_ids == [run.id], "the fire must know its run, so the stop can revise it"


def test_a_filled_position_whose_stop_failed_is_not_logged_as_a_rejection(db, monkeypatch):
    """The run log has to tell these two apart at a glance.

    A rejection means nothing happened and nothing is owed. This means the legs are live,
    money is at risk, and a stop has to be set by hand -- and it was reading as
    `order_rejected`, the code for the harmless one, while a naked short sat open.
    """
    enable_bot()
    patch_decision(monkeypatch, bot2.TickDecision("fire", None, None, ("NIFTY",)))
    result = bot2.FireResult(
        index_code="NIFTY", exchange_code=cfg.NFO, expiry_display="03-Sep-2026",
        right="put", strike_price=23500.0, lots=2, quantity=150, entry_price=42.0,
        order_ids=["OID1"], rule_id=None,
        reason_code=ReasonCode.EXIT_ARM_FAILED,
        error="Position is OPEN but its stop could not be armed: engine down",
    )
    monkeypatch.setattr(bot2, "fire_index", lambda *a, **k: result)

    scheduler.tick(FakeProc())
    run = repo.list_runs("u1")[0]
    # `partial`, not `failed`: the leg is filled and holding margin, so the headline has to
    # say the trade happened -- only the stop is missing, which the reason code carries.
    assert run.status == "partial"
    assert run.reason_code == ReasonCode.EXIT_ARM_FAILED
    assert run.reason_code != ReasonCode.ORDER_REJECTED
    assert "could not be armed" in run.reason_text
    # The filled legs stay on the record: it is the only place the user can see what is
    # actually open.
    assert run.detail["legs"][0]["order_ids"] == ["OID1"]


@pytest.mark.parametrize(
    "reason_code, error",
    [
        (ReasonCode.EXIT_ARM_FAILED, "Position is OPEN but its stop could not be armed: x"),
        (ReasonCode.ORDER_REJECTED, "Partially placed (1 order(s)): Rejected"),
    ],
    ids=["stop_failed", "partial_fill"],
)
def test_margin_held_by_a_failed_fire_is_still_passed_to_the_next_bot(
    db, monkeypatch, reason_code, error
):
    """Anything that reached the exchange holds margin, whether or not the run was clean.

    Neither shape here is a clean run, and both used to hand the next bot in the sweep a
    commitment of zero -- so with the Expiry Writer ordered first, the Holdings Writer sized
    against capital a live short was already using.
    """
    enable_bot()
    repo.update_bot("u1", BOT_EXPIRY_INDEX_WRITER, priority=1)
    repo.update_bot("u1", BOT_HOLDINGS_WRITER, enabled=True, priority=2)
    patch_decision(monkeypatch, bot2.TickDecision("fire", None, None, ("NIFTY",)))
    result = bot2.FireResult(
        index_code="NIFTY", exchange_code=cfg.NFO, expiry_display="03-Sep-2026",
        right="put", strike_price=23500.0, lots=2, quantity=150, entry_price=42.0,
        order_ids=["OID1"], rule_id=None, margin_total=250_000.0,
        reason_code=reason_code, error=error,
    )
    monkeypatch.setattr(bot2, "fire_index", lambda *a, **k: result)

    handed_on = []

    def holdings(*a, margin_committed, **k):
        handed_on.append(margin_committed)
        return 0.0

    monkeypatch.setattr(scheduler, "_tick_holdings_writer", holdings)

    scheduler.tick(FakeProc())

    assert repo.list_runs("u1")[0].status == "partial"
    assert handed_on == [250_000.0]


def test_a_fire_that_placed_nothing_commits_nothing(db, monkeypatch):
    """The other side of the same rule: a clean rejection holds no margin, so the next bot
    must get the full amount rather than a phantom deduction."""
    enable_bot()
    repo.update_bot("u1", BOT_EXPIRY_INDEX_WRITER, priority=1)
    repo.update_bot("u1", BOT_HOLDINGS_WRITER, enabled=True, priority=2)
    patch_decision(monkeypatch, bot2.TickDecision("fire", None, None, ("NIFTY",)))
    result = bot2.FireResult(
        index_code="NIFTY", exchange_code=cfg.NFO, expiry_display="03-Sep-2026",
        right="put", margin_total=250_000.0,
        reason_code=ReasonCode.ORDER_REJECTED, error="Rejected",
    )
    monkeypatch.setattr(bot2, "fire_index", lambda *a, **k: result)

    handed_on = []

    def holdings(*a, margin_committed, **k):
        handed_on.append(margin_committed)
        return 0.0

    monkeypatch.setattr(scheduler, "_tick_holdings_writer", holdings)

    scheduler.tick(FakeProc())

    assert handed_on == [0.0]


def test_one_clean_index_cannot_mask_another_left_without_a_stop(db, monkeypatch):
    """Two indices expiring together: NIFTY fires and arms cleanly, SENSEX fills but its
    stop fails. The run used to be judged on the clean results alone, so it read as
    `completed` / `orders_placed` while a SENSEX short sat open with nothing behind it --
    the one state the run log exists to make impossible to miss."""
    both = {
        "NIFTY": IndexWriterLeg(enabled=True, priority=1).model_dump(),
        "BSESEN": IndexWriterLeg(enabled=True, priority=2).model_dump(),
    }
    enable_bot(indices=both)
    patch_decision(
        monkeypatch,
        bot2.TickDecision("fire", None, None, ("NIFTY", "BSESEN")),
        expiring={"NIFTY": "03-Sep-2026", "BSESEN": "03-Sep-2026"},
    )
    by_index = {
        "NIFTY": bot2.FireResult(
            index_code="NIFTY", exchange_code=cfg.NFO, expiry_display="03-Sep-2026",
            right="put", strike_price=23500.0, lots=2, quantity=150, entry_price=42.0,
            order_ids=["OID1"], rule_id="rule-1",
        ),
        "BSESEN": bot2.FireResult(
            index_code="BSESEN", exchange_code=cfg.BFO, expiry_display="03-Sep-2026",
            right="put", strike_price=80000.0, lots=1, quantity=20, entry_price=90.0,
            order_ids=["OID2"], rule_id=None,
            reason_code=ReasonCode.EXIT_ARM_FAILED,
            error="Position is OPEN but its stop could not be armed: engine down",
        ),
    }
    monkeypatch.setattr(bot2, "fire_index", lambda proc, user_id, code, **k: by_index[code])

    scheduler.tick(FakeProc())
    run = repo.list_runs("u1")[0]

    assert run.status == "partial"
    assert run.reason_code == ReasonCode.EXIT_ARM_FAILED
    # Both positions are named in the headline: what is protected and what is not.
    assert "NIFTY 23500 PE" in run.reason_text
    sensex = bot2.INDEX_LABEL.get("BSESEN", "BSESEN")
    assert f"{sensex} 80000 PE — Position is OPEN but its stop could not be armed" in run.reason_text


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
