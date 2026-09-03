"""Persistence for the Bots section (app.repositories.bots)."""
from __future__ import annotations

import pytest

from icici_breeze_backend.app.db.bots_migrate import (
    BOT_EXPIRY_INDEX_WRITER,
    BOT_HOLDINGS_WRITER,
    ensure_bots_tables,
)
from icici_breeze_backend.app.domain.bots import ProposalLeg, ReasonCode, ScripPref
from icici_breeze_backend.app.repositories import bots as repo


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = str(tmp_path / "users_test.sqlite3")
    monkeypatch.setattr(repo, "_db_path", lambda: path)
    ensure_bots_tables(path)
    ensure_bots_tables(path)  # migration must be idempotent
    return path


def _leg(stock_code="ITC", right="call", lots=1, **kw):
    base = dict(
        stock_code=stock_code,
        right=right,
        expiry_display="24-Sep-2026",
        strike_price=280.0,
        lots=lots,
        lot_size=1725,
        quantity=1725 * lots,
        premium_per_share=4.25,
        premium_total=4.25 * 1725 * lots,
    )
    base.update(kw)
    return ProposalLeg(**base)


# --- bot instances -------------------------------------------------------------------


def test_bots_are_created_lazily_disabled_with_policy_defaults(db_path):
    bots = repo.list_bots("u1")
    assert {b.bot_type for b in bots} == {BOT_HOLDINGS_WRITER, BOT_EXPIRY_INDEX_WRITER}
    assert all(b.enabled is False for b in bots), "a new bot must never start armed"

    holdings = next(b for b in bots if b.bot_type == BOT_HOLDINGS_WRITER)
    # CE default-on / PE opt-in is policy, so it must hold without any config being written.
    assert holdings.config["default_safety_pct_ce"] == 5.0
    assert holdings.config["expiry_preference"] == "current"

    index = next(b for b in bots if b.bot_type == BOT_EXPIRY_INDEX_WRITER)
    assert index.config["cutoff_ist"] == "12:00"
    # Profit booking is a share of the premium now, not an absolute paise price.
    assert index.config["profit_book_premium_pct"] == 50.0
    assert "profit_target_option_price" not in index.config
    assert set(index.config["indices"]) == {"NIFTY", "BSESEN"}


def test_get_or_create_is_stable(db_path):
    first = repo.get_or_create_bot("u1", BOT_HOLDINGS_WRITER)
    second = repo.get_or_create_bot("u1", BOT_HOLDINGS_WRITER)
    assert first.id == second.id


def test_update_merges_config_instead_of_replacing(db_path):
    repo.update_bot("u1", BOT_HOLDINGS_WRITER, config={"delivery_cash_budget": 500000.0})
    # A second partial edit must not reset the first -- the UI edits one panel at a time.
    updated = repo.update_bot("u1", BOT_HOLDINGS_WRITER, config={"default_safety_pct_ce": 7.5})
    assert updated.config["delivery_cash_budget"] == 500000.0
    assert updated.config["default_safety_pct_ce"] == 7.5


def test_enabling_does_not_disturb_config(db_path):
    repo.update_bot("u1", BOT_HOLDINGS_WRITER, config={"delivery_cash_budget": 250000.0})
    enabled = repo.update_bot("u1", BOT_HOLDINGS_WRITER, enabled=True)
    assert enabled.enabled is True
    assert enabled.config["delivery_cash_budget"] == 250000.0


def test_config_persisted_by_an_older_build_inherits_new_defaults(db_path):
    """A stored blob missing a field must inherit the policy default, not KeyError deep
    inside a bot that is mid-run."""
    import json
    import sqlite3

    repo.get_or_create_bot("u1", BOT_EXPIRY_INDEX_WRITER)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE bots SET config = ? WHERE user_id = 'u1' AND bot_type = ?",
            (json.dumps({"entry_time_ist": "09:45"}), BOT_EXPIRY_INDEX_WRITER),
        )
        conn.commit()
    bot = repo.get_or_create_bot("u1", BOT_EXPIRY_INDEX_WRITER)
    assert bot.config["entry_time_ist"] == "09:45"
    assert bot.config["cutoff_ist"] == "12:00"


def test_corrupt_config_falls_back_to_defaults_rather_than_breaking_the_list(db_path):
    import sqlite3

    repo.get_or_create_bot("u1", BOT_HOLDINGS_WRITER)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE bots SET config = 'not json' WHERE user_id = 'u1' AND bot_type = ?",
            (BOT_HOLDINGS_WRITER,),
        )
        conn.commit()
    assert repo.get_or_create_bot("u1", BOT_HOLDINGS_WRITER).config["default_safety_pct_ce"] == 5.0


def test_bots_are_scoped_per_user(db_path):
    repo.update_bot("u1", BOT_HOLDINGS_WRITER, enabled=True)
    assert repo.get_or_create_bot("u2", BOT_HOLDINGS_WRITER).enabled is False


# --- per-scrip prefs -----------------------------------------------------------------


def test_scrip_prefs_upsert_and_override(db_path):
    repo.upsert_scrip_prefs("u1", [ScripPref(stock_code="itc", ce_enabled=False, pe_enabled=True)])
    prefs = repo.list_scrip_prefs("u1")
    assert len(prefs) == 1
    assert prefs[0].stock_code == "ITC", "codes normalize to upper -- ICICI ShortName namespace"
    assert prefs[0].ce_enabled is False and prefs[0].pe_enabled is True

    repo.upsert_scrip_prefs("u1", [ScripPref(stock_code="ITC", ce_enabled=True, safety_pct_ce=8.0)])
    prefs = repo.list_scrip_prefs("u1")
    assert len(prefs) == 1, "upsert must not duplicate the scrip"
    assert prefs[0].ce_enabled is True and prefs[0].safety_pct_ce == 8.0


def test_absent_scrip_pref_means_policy_default(db_path):
    assert repo.list_scrip_prefs("u1") == []


# --- run log -------------------------------------------------------------------------


def test_run_records_a_machine_readable_reason(db_path):
    run_id = repo.start_run("u1", BOT_EXPIRY_INDEX_WRITER, "schedule")
    repo.finish_run(
        run_id,
        status="skipped",
        reason_code=ReasonCode.NO_BROKER_SESSION,
        reason_text="No ICICI session by 12:00.",
        detail={"nags_sent": 16},
    )
    run = repo.list_runs("u1")[0]
    assert run.status == "skipped"
    assert run.reason_code == ReasonCode.NO_BROKER_SESSION
    assert run.detail == {"nags_sent": 16}
    assert run.finished_at is not None


def test_distinct_no_trade_days_stay_distinguishable(db_path):
    """The whole point of the run log: two different no-trade days must not collapse."""
    for code, text in (
        (ReasonCode.NO_BROKER_SESSION, "No ICICI session by 12:00."),
        (ReasonCode.MARGIN_CAP_TOO_SMALL, "One lot exceeded the margin cap."),
    ):
        rid = repo.start_run("u1", BOT_EXPIRY_INDEX_WRITER, "schedule")
        repo.finish_run(rid, status="skipped", reason_code=code, reason_text=text)
    codes = {r.reason_code for r in repo.list_runs("u1")}
    assert codes == {ReasonCode.NO_BROKER_SESSION, ReasonCode.MARGIN_CAP_TOO_SMALL}


def test_runs_filter_by_bot_and_are_newest_first(db_path):
    a = repo.start_run("u1", BOT_HOLDINGS_WRITER, "manual")
    repo.finish_run(a, status="proposed", reason_code=ReasonCode.PROPOSAL_READY, reason_text="ok")
    b = repo.start_run("u1", BOT_EXPIRY_INDEX_WRITER, "schedule")
    repo.finish_run(b, status="completed", reason_code=ReasonCode.ORDERS_PLACED, reason_text="ok")

    assert [r.id for r in repo.list_runs("u1")] == [b, a]
    assert [r.id for r in repo.list_runs("u1", bot_type=BOT_HOLDINGS_WRITER)] == [a]


# --- proposals -----------------------------------------------------------------------


def test_proposal_round_trips_legs(db_path):
    run_id = repo.start_run("u1", BOT_HOLDINGS_WRITER, "manual")
    created = repo.create_proposal(
        run_id=run_id,
        user_id="u1",
        bot_type=BOT_HOLDINGS_WRITER,
        legs=[_leg(), _leg(stock_code="GAIL", right="put", delivery_exposure=1_171_500.0)],
        totals={"premium_total": 12345.0},
    )
    assert len(created.legs) == 2
    pending = repo.get_pending_proposal("u1", BOT_HOLDINGS_WRITER)
    assert pending is not None and pending.id == created.id
    assert pending.legs[1].delivery_exposure == 1_171_500.0
    assert pending.totals == {"premium_total": 12345.0}


def test_new_scan_supersedes_the_previous_pending_proposal(db_path):
    run_id = repo.start_run("u1", BOT_HOLDINGS_WRITER, "manual")
    first = repo.create_proposal(
        run_id=run_id, user_id="u1", bot_type=BOT_HOLDINGS_WRITER, legs=[_leg()]
    )
    second = repo.create_proposal(
        run_id=run_id, user_id="u1", bot_type=BOT_HOLDINGS_WRITER, legs=[_leg(lots=2)]
    )
    assert repo.get_proposal("u1", first.id).status == "superseded"
    assert repo.get_pending_proposal("u1", BOT_HOLDINGS_WRITER).id == second.id


def test_expired_proposal_is_never_returned_as_pending(db_path):
    """A proposal is a priced snapshot; stale prices must not be actionable."""
    run_id = repo.start_run("u1", BOT_HOLDINGS_WRITER, "manual")
    created = repo.create_proposal(
        run_id=run_id, user_id="u1", bot_type=BOT_HOLDINGS_WRITER, legs=[_leg()], ttl_minutes=1
    )
    import sqlite3

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE bot_proposals SET expires_at = '2000-01-01 00:00:00' WHERE id = ?",
            (created.id,),
        )
        conn.commit()
    assert repo.get_pending_proposal("u1", BOT_HOLDINGS_WRITER) is None
    assert repo.get_proposal("u1", created.id).status == "expired"


def test_resolve_proposal_records_outcome(db_path):
    run_id = repo.start_run("u1", BOT_HOLDINGS_WRITER, "manual")
    created = repo.create_proposal(
        run_id=run_id, user_id="u1", bot_type=BOT_HOLDINGS_WRITER, legs=[_leg()]
    )
    resolved = repo.resolve_proposal("u1", created.id, status="placed", note="2 of 3 legs placed")
    assert resolved.status == "placed"
    assert resolved.resolution_note == "2 of 3 legs placed"
    assert repo.get_pending_proposal("u1", BOT_HOLDINGS_WRITER) is None


def test_proposals_are_scoped_per_user(db_path):
    run_id = repo.start_run("u1", BOT_HOLDINGS_WRITER, "manual")
    created = repo.create_proposal(
        run_id=run_id, user_id="u1", bot_type=BOT_HOLDINGS_WRITER, legs=[_leg()]
    )
    assert repo.get_proposal("u2", created.id) is None


# --- stale-run reaper ----------------------------------------------------------------


def test_reaper_closes_a_run_left_running(db_path):
    """A process that dies mid-run leaves `running` for ever; unattended Bot 2's log is
    exactly where that reads as "still working" long after the process is gone."""
    run_id = repo.start_run("u1", BOT_EXPIRY_INDEX_WRITER, "schedule")
    assert repo.list_runs("u1")[0].status == "running"

    assert repo.reap_stale_runs() == 1
    reaped = repo.list_runs("u1")[0]
    assert reaped.status == "failed"
    assert reaped.reason_code == ReasonCode.INTERRUPTED
    assert reaped.finished_at is not None
    # It must not claim nothing happened -- an interrupted run may already have traded.
    assert "Order Book" in reaped.reason_text


def test_reaper_leaves_finished_runs_alone(db_path):
    run_id = repo.start_run("u1", BOT_HOLDINGS_WRITER, "manual")
    repo.finish_run(run_id, status="completed", reason_code=ReasonCode.ORDERS_PLACED,
                    reason_text="done")
    assert repo.reap_stale_runs() == 0
    assert repo.list_runs("u1")[0].reason_code == ReasonCode.ORDERS_PLACED


def test_age_bounded_reap_spares_a_run_still_in_flight(db_path):
    """The scheduler's periodic sweep must not kill a scan that is simply slow."""
    repo.start_run("u1", BOT_HOLDINGS_WRITER, "manual")
    assert repo.reap_stale_runs(older_than_minutes=30) == 0
    assert repo.list_runs("u1")[0].status == "running"


def test_age_bounded_reap_catches_a_hung_run(db_path):
    import sqlite3

    run_id = repo.start_run("u1", BOT_HOLDINGS_WRITER, "manual")
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE bot_runs SET started_at = '2000-01-01 00:00:00' WHERE id = ?",
                     (run_id,))
        conn.commit()
    assert repo.reap_stale_runs(older_than_minutes=30) == 1
    assert repo.list_runs("u1")[0].reason_code == ReasonCode.INTERRUPTED


def test_reaper_is_idempotent(db_path):
    repo.start_run("u1", BOT_EXPIRY_INDEX_WRITER, "schedule")
    assert repo.reap_stale_runs() == 1
    assert repo.reap_stale_runs() == 0
