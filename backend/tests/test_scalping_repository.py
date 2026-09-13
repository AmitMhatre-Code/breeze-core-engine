"""Scalper persistence: session runs, cycles and day totals (app.repositories.bots).

The behaviours worth protecting here are the ones a naive implementation gets wrong and
never notices: that a session run is not reaped while it is healthy, that day totals are
recomputed from rows rather than accumulated in memory, and that an aborted entry neither
counts toward the consecutive-loss cooldown nor clears it.
"""
from __future__ import annotations

import datetime

import pytest

from icici_breeze_backend.app.db.bots_migrate import (
    BOT_CAS_BINGO,
    BOT_EXPIRY_INDEX_WRITER,
    BOT_HOLDINGS_WRITER,
    BOT_IRON_FLY_SCALPER,
    BOT_MOMENTUM_LONG_SCALPER,
    ensure_bots_tables,
)
from icici_breeze_backend.app.domain.bots import ReasonCode
from icici_breeze_backend.app.repositories import bots as repo

USER = "u1"


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = str(tmp_path / "users_test.sqlite3")
    monkeypatch.setattr(repo, "_db_path", lambda: path)
    ensure_bots_tables(path)
    ensure_bots_tables(path)  # migration must be idempotent
    return path


def _open(run_id, *, structure="long_ce", entry=1000.0, paper=True):
    return repo.open_cycle(
        USER,
        BOT_MOMENTUM_LONG_SCALPER,
        run_id,
        structure=structure,
        legs=[{"right": "call", "strike_price": 24000.0}],
        lots=1,
        entry_value=entry,
        paper=paper,
    )


# --- bot instances --------------------------------------------------------------------


def test_scalper_bots_are_created_lazily_with_policy_defaults(db_path):
    bot = repo.get_or_create_bot(USER, BOT_MOMENTUM_LONG_SCALPER)
    assert bot.enabled is False
    # Ships in paper mode: arming something that fires unattended orders is a deliberate act.
    assert bot.config["mode"] == "paper"
    assert bot.config["risk"]["cumulative_stop_inr"] == 10000.0
    assert bot.config["signal"]["ema_period"] == 9

    fly = repo.get_or_create_bot(USER, BOT_IRON_FLY_SCALPER)
    assert fly.config["structure"]["wing_width_points"] == 150.0
    assert fly.config["reentry"]["cooldown_minutes"] == 15


def test_all_four_bots_get_distinct_priorities(db_path):
    priorities = {
        t: repo.get_or_create_bot(USER, t).priority
        for t in (
            BOT_HOLDINGS_WRITER,
            BOT_EXPIRY_INDEX_WRITER,
            BOT_MOMENTUM_LONG_SCALPER,
            BOT_IRON_FLY_SCALPER,
        )
    }
    assert len(set(priorities.values())) == 4, priorities
    assert priorities[BOT_HOLDINGS_WRITER] == 1  # capped by stock held, so it sizes first


def test_all_four_bots_are_listed(db_path):
    """The scalpers joined /bots at step 8, created lazily and switched off."""
    listed = {b.bot_type for b in repo.list_bots(USER)}
    assert listed == {
        BOT_HOLDINGS_WRITER,
        BOT_EXPIRY_INDEX_WRITER,
        BOT_MOMENTUM_LONG_SCALPER,
        BOT_IRON_FLY_SCALPER,
        BOT_CAS_BINGO,
    }
    scalpers = [b for b in repo.list_bots(USER) if b.bot_type in
                (BOT_MOMENTUM_LONG_SCALPER, BOT_IRON_FLY_SCALPER)]
    assert all(not b.enabled and b.config["mode"] == "paper" for b in scalpers)


def test_stored_config_survives_a_field_being_added_later(db_path):
    """Validating on read is what makes an older blob inherit new policy defaults."""
    repo.get_or_create_bot(USER, BOT_MOMENTUM_LONG_SCALPER)
    repo.update_bot(USER, BOT_MOMENTUM_LONG_SCALPER, config={"premium_outlay_inr": 5000.0})
    cfg = repo.get_or_create_bot(USER, BOT_MOMENTUM_LONG_SCALPER).config
    assert cfg["premium_outlay_inr"] == 5000.0
    assert cfg["exits"]["target_pts"] == 10.0  # inherited, not missing


# --- session runs ---------------------------------------------------------------------


def test_open_session_run_is_idempotent_within_a_day(db_path):
    """The caller is a loop asking on every wake, not a scheduler firing once.

    A second row would split the day's cycles across two runs and silently reset the
    consecutive-loss counter that reads them.
    """
    first = repo.open_session_run(USER, BOT_MOMENTUM_LONG_SCALPER)
    assert repo.open_session_run(USER, BOT_MOMENTUM_LONG_SCALPER) == first


def test_healthy_session_is_not_reaped_but_a_stalled_one_is(db_path):
    run_id = repo.open_session_run(USER, BOT_MOMENTUM_LONG_SCALPER)
    repo.touch_run_heartbeat(run_id)
    assert repo.reap_stale_runs(older_than_minutes=30) == 0

    # Backdate only the heartbeat: the run is old, but that is not what liveness means.
    stale = (datetime.datetime.now() - datetime.timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
    import sqlite3

    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE bot_runs SET heartbeat_at = ? WHERE id = ?", (stale, run_id))
        conn.commit()
    assert repo.reap_stale_runs(older_than_minutes=30) == 1
    run = repo.list_runs(USER, bot_type=BOT_MOMENTUM_LONG_SCALPER)[0]
    assert run.status == "failed"
    assert run.reason_code == ReasonCode.INTERRUPTED


def test_reaping_is_unchanged_for_bots_that_never_heartbeat(db_path):
    """COALESCE(heartbeat_at, started_at) keeps Bots 1 and 2 exactly as they were."""
    run_id = repo.start_run(USER, BOT_EXPIRY_INDEX_WRITER, "schedule")
    assert repo.reap_stale_runs(older_than_minutes=30) == 0  # young, stays
    import sqlite3

    old = (datetime.datetime.now() - datetime.timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE bot_runs SET started_at = ? WHERE id = ?", (old, run_id))
        conn.commit()
    assert repo.reap_stale_runs(older_than_minutes=30) == 1


def test_startup_reap_takes_everything_including_sessions(db_path):
    """One process owns this file, so any running row at startup is definitionally stale."""
    repo.open_session_run(USER, BOT_MOMENTUM_LONG_SCALPER)
    repo.start_run(USER, BOT_HOLDINGS_WRITER, "schedule")
    assert repo.reap_stale_runs() == 2


# --- cycles ---------------------------------------------------------------------------


def test_cycle_numbers_are_sequential_within_a_session(db_path):
    run_id = repo.open_session_run(USER, BOT_MOMENTUM_LONG_SCALPER)
    assert [_open(run_id).cycle_no for _ in range(3)] == [1, 2, 3]


def test_net_pnl_is_derived_and_always_subtracts_friction(db_path):
    """Net is what the stop and the loss counter read; callers must not supply it."""
    run_id = repo.open_session_run(USER, BOT_MOMENTUM_LONG_SCALPER)
    cycle = _open(run_id)
    repo.close_cycle(
        cycle.id,
        exit_reason_code=ReasonCode.TARGET_HIT,
        exit_reason_text="Target reached.",
        gross_pnl=650.0,
        friction=102.5,
    )
    closed = repo.list_cycles(USER, run_id=run_id)[0]
    assert closed.net_pnl == pytest.approx(547.5)
    assert closed.is_open is False


def test_a_gross_win_smaller_than_friction_is_a_loss(db_path):
    """The whole friction argument in plan section 6.4, in one row."""
    run_id = repo.open_session_run(USER, BOT_MOMENTUM_LONG_SCALPER)
    cycle = _open(run_id)
    repo.close_cycle(
        cycle.id,
        exit_reason_code=ReasonCode.TRAILING_STOP,
        exit_reason_text="Trailed out.",
        gross_pnl=60.0,
        friction=102.5,
    )
    assert repo.list_cycles(USER, run_id=run_id)[0].is_loss is True


def test_open_cycles_survive_a_restart(db_path):
    """An open cycle is a position the broker holds and this process has forgotten."""
    run_id = repo.open_session_run(USER, BOT_MOMENTUM_LONG_SCALPER)
    cycle = _open(run_id)
    assert [c.id for c in repo.open_cycles(USER, BOT_MOMENTUM_LONG_SCALPER)] == [cycle.id]
    repo.close_cycle(
        cycle.id, exit_reason_code=ReasonCode.SQUARE_OFF, exit_reason_text="EOD.",
        gross_pnl=0.0, friction=0.0,
    )
    assert repo.open_cycles(USER, BOT_MOMENTUM_LONG_SCALPER) == []


def test_cycles_of_one_bot_do_not_leak_into_another(db_path):
    run3 = repo.open_session_run(USER, BOT_MOMENTUM_LONG_SCALPER)
    run4 = repo.open_session_run(USER, BOT_IRON_FLY_SCALPER)
    assert run3 != run4
    _open(run3)
    repo.open_cycle(USER, BOT_IRON_FLY_SCALPER, run4, structure="iron_fly", legs=[], lots=2)
    assert len(repo.list_cycles(USER, bot_type=BOT_MOMENTUM_LONG_SCALPER)) == 1
    assert len(repo.list_cycles(USER, bot_type=BOT_IRON_FLY_SCALPER)) == 1


# --- day totals -----------------------------------------------------------------------


def _close(cycle, gross, friction=100.0, code=ReasonCode.TARGET_HIT):
    repo.close_cycle(
        cycle.id, exit_reason_code=code, exit_reason_text="x",
        gross_pnl=gross, friction=friction,
    )


def test_day_totals_are_recomputed_from_rows(db_path):
    """Not accumulated in memory: a restart must not reset a cumulative stop mid-session."""
    run_id = repo.open_session_run(USER, BOT_MOMENTUM_LONG_SCALPER)
    _close(_open(run_id), 650.0)
    _close(_open(run_id), -390.0)
    open_one = _open(run_id)

    totals = repo.scalper_day_totals(USER, BOT_MOMENTUM_LONG_SCALPER)
    assert totals.cycles == 3
    assert totals.open_cycles == 1
    assert totals.realized_net_pnl == pytest.approx(650 - 100 - 390 - 100)
    assert totals.friction == pytest.approx(200.0)  # the open cycle has none yet
    assert open_one.is_open


def test_consecutive_losses_count_back_to_the_first_win(db_path):
    run_id = repo.open_session_run(USER, BOT_MOMENTUM_LONG_SCALPER)
    _close(_open(run_id), -390.0)
    _close(_open(run_id), 650.0)   # resets the streak
    _close(_open(run_id), -390.0)
    _close(_open(run_id), -390.0)
    assert repo.scalper_day_totals(USER, BOT_MOMENTUM_LONG_SCALPER).consecutive_losses == 2


def test_an_aborted_entry_neither_counts_as_a_loss_nor_clears_the_streak(db_path):
    """A cycle that never opened a position was not a trade -- plan section 3.6."""
    run_id = repo.open_session_run(USER, BOT_MOMENTUM_LONG_SCALPER)
    _close(_open(run_id), -390.0)
    _close(_open(run_id), -390.0)
    aborted = _open(run_id)
    repo.close_cycle(
        aborted.id,
        exit_reason_code=ReasonCode.ENTRY_UNFILLED,
        exit_reason_text="Limit never filled; nothing traded.",
        gross_pnl=0.0,
        friction=0.0,
    )
    totals = repo.scalper_day_totals(USER, BOT_MOMENTUM_LONG_SCALPER)
    assert totals.consecutive_losses == 0  # a zero-P&L row is not a loss
    assert totals.realized_net_pnl == pytest.approx(-980.0)


def test_day_totals_ignore_other_days(db_path):
    import sqlite3

    run_id = repo.open_session_run(USER, BOT_MOMENTUM_LONG_SCALPER)
    old = _open(run_id)
    _close(old, -5000.0)
    yesterday = (datetime.datetime.now() - datetime.timedelta(days=1)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE bot_cycles SET opened_at = ? WHERE id = ?", (yesterday, old.id))
        conn.commit()
    assert repo.scalper_day_totals(USER, BOT_MOMENTUM_LONG_SCALPER).cycles == 0
