"""The paper-evidence gate (app.services.bots.scalping.evidence).

This is what stands between a scalper and unattended real orders, so the tests are written
against the ways a gate silently stops being one: evidence that survives a settings change,
a half-day counted as a day, a paper record credited to a live bot, and a config that
normalises differently depending on which fields the user happened to save.
"""
from __future__ import annotations

import pytest

from icici_breeze_backend.app.db.bots_migrate import (
    BOT_IRON_FLY_SCALPER,
    BOT_MOMENTUM_LONG_SCALPER,
    ensure_bots_tables,
)
from icici_breeze_backend.app.repositories import bots as repo
from icici_breeze_backend.app.services.bots.scalping import evidence as ev

USER = "u1"
BOT = BOT_MOMENTUM_LONG_SCALPER


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = str(tmp_path / "users_test.sqlite3")
    monkeypatch.setattr(repo, "_db_path", lambda: path)
    ensure_bots_tables(path)
    return path


def _finished_paper_day(config: dict, *, cycles=(100.0, -40.0), mode="paper"):
    """One session run, stamped and closed the way a real trading day closes."""
    run_id = repo.open_session_run(USER, BOT)
    repo.stamp_session_config(run_id, ev.material_config_hash(BOT, config), mode)
    for net in cycles:
        cycle = repo.open_cycle(
            USER, BOT, run_id,
            structure="long_ce",
            legs=[{"right": "call", "strike_price": 24000.0}],
            lots=1, entry_value=1000.0, paper=(mode == "paper"),
        )
        repo.close_cycle(
            cycle.id, exit_value=1000.0 + net, gross_pnl=net, friction=20.0,
            exit_reason_code="target", exit_reason_text="done",
        )
    repo.finish_run(
        run_id, status="completed", reason_code="session_complete",
        reason_text="The day's last trading window has closed.",
    )
    return run_id


# --------------------------------------------------------------------------------------
# The fingerprint
# --------------------------------------------------------------------------------------


def test_defaults_hash_the_same_whether_spelled_out_or_omitted():
    """A user who opens the Signal tab and saves without changing anything must not void
    their own evidence -- the most confusing behaviour a gate could have."""
    sparse = {"index": "NIFTY"}
    spelled = {
        "index": "NIFTY",
        "signal": {"mechanism": "expansion", "duration": 15, "direction": "fade"},
    }
    assert ev.material_config_hash(BOT, sparse) == ev.material_config_hash(BOT, spelled)


def test_mode_is_not_material():
    """Paper evidence has to match the hash of the live bot it is unlocking, so the field
    being gated cannot be part of the fingerprint."""
    assert ev.material_config_hash(BOT, {"mode": "paper"}) == ev.material_config_hash(
        BOT, {"mode": "live"}
    )


def test_api_budget_reserve_is_not_material():
    """Operational: it can stop the bot entering, but it changes neither signal, size nor
    exit, so it must not cost the user a re-proving week."""
    a = {"risk": {"api_budget_reserve_calls": 25}}
    b = {"risk": {"api_budget_reserve_calls": 40}}
    assert ev.material_config_hash(BOT, a) == ev.material_config_hash(BOT, b)


@pytest.mark.parametrize(
    "change",
    [
        {"signal": {"mechanism": "momentum", "duration": 5, "direction": "follow"}},
        {"exits": {"stop_loss_pts": 9.0}},
        {"premium_outlay_inr": 50000.0},
        {"execution": {"entry_retries": 0}},
        {"risk": {"cumulative_stop_inr": 25000.0}},
        {"sessions": [{"start": "10:00", "end": "11:00"}]},
        {"hard_square_off_ist": "15:05"},
        {"trade_on_expiry_day": True},
    ],
)
def test_every_pnl_bearing_field_is_material(change):
    """The allowlist is exclusions-only, so anything not named in NON_MATERIAL_PATHS must
    move the hash. A field that quietly did not would be evidence for the wrong settings."""
    assert ev.material_config_hash(BOT, {}) != ev.material_config_hash(BOT, change)


def test_unparseable_config_still_hashes_and_never_matches():
    """Fail closed: a config the model rejects must not collide with a valid one and hand
    it someone else's evidence."""
    bad = ev.material_config_hash(BOT, {"sessions": "not-a-list"})
    assert bad and bad != ev.material_config_hash(BOT, {})


# --------------------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------------------


def test_a_fresh_bot_is_locked(db_path):
    assert not ev.gather(USER, BOT, {}).unlocked


def test_one_completed_paper_day_unlocks(db_path):
    _finished_paper_day({})
    found = ev.gather(USER, BOT, {})
    assert found.unlocked and found.days == 1
    assert found.closed_cycles == 2 and found.wins == 1 and found.losses == 1
    # Net is the sum of the closed cycles' net, friction reported alongside rather than
    # netted into it (plan section 6.4).
    assert found.net_pnl == pytest.approx(100.0 - 40.0 - 40.0)
    assert found.friction == pytest.approx(40.0)


def test_a_quiet_day_with_no_cycles_still_unlocks(db_path):
    """Deliberate: the bar is a completed trading day on these settings, not a cycle count.
    A day that produced no signal is still evidence about what these settings do."""
    _finished_paper_day({}, cycles=())
    found = ev.gather(USER, BOT, {})
    assert found.unlocked and found.cycles == 0


def test_changing_a_material_setting_relocks(db_path):
    """The whole point of the fingerprint: evidence is evidence for the settings that
    produced it, and nothing has to remember to reset a counter."""
    _finished_paper_day({})
    assert ev.gather(USER, BOT, {}).unlocked
    assert not ev.gather(USER, BOT, {"signal": {"mechanism": "momentum", "duration": 5, "direction": "follow"}}).unlocked


def test_an_interrupted_day_is_not_evidence(db_path):
    """`reap_stale_runs` marks a session the process died inside as `failed`. Half a day
    proves nothing about how these settings behave into the close."""
    run_id = repo.open_session_run(USER, BOT)
    repo.stamp_session_config(run_id, ev.material_config_hash(BOT, {}), "paper")
    repo.finish_run(
        run_id, status="failed", reason_code="interrupted", reason_text="stalled"
    )
    assert not ev.gather(USER, BOT, {}).unlocked


def test_a_live_day_is_not_paper_evidence(db_path):
    """Evidence has to come from paper. A live day unlocking live would be circular."""
    _finished_paper_day({}, mode="live")
    assert not ev.gather(USER, BOT, {}).unlocked


def test_settings_changed_mid_session_void_that_day(db_path):
    """A session stamped at 09:15 and edited at 11:00 ran half a day on each. Crediting it
    to the settings it started with would let a user paper-prove one configuration and arm
    a different one on its record."""
    run_id = repo.open_session_run(USER, BOT)
    repo.stamp_session_config(run_id, ev.material_config_hash(BOT, {}), "paper")
    repo.stamp_session_config(
        run_id, ev.material_config_hash(BOT, {"signal": {"mechanism": "momentum", "duration": 5, "direction": "follow"}}), "paper"
    )
    repo.finish_run(
        run_id, status="completed", reason_code="session_complete", reason_text="done"
    )
    assert not ev.gather(USER, BOT, {}).unlocked
    assert not ev.gather(USER, BOT, {"signal": {"mechanism": "momentum", "duration": 5, "direction": "follow"}}).unlocked


def test_a_voided_day_stays_voided_even_if_the_settings_come_back(db_path):
    run_id = repo.open_session_run(USER, BOT)
    base = ev.material_config_hash(BOT, {})
    repo.stamp_session_config(run_id, base, "paper")
    repo.stamp_session_config(run_id, ev.material_config_hash(BOT, {"premium_outlay_inr": 5e4}), "paper")
    repo.stamp_session_config(run_id, base, "paper")  # user puts it back
    repo.finish_run(run_id, status="completed", reason_code="session_complete", reason_text="d")
    assert not ev.gather(USER, BOT, {}).unlocked


def test_restamping_the_same_settings_is_a_no_op(db_path):
    """The loop stamps on every pass; an unchanged stamp must not look like a change."""
    run_id = repo.open_session_run(USER, BOT)
    h = ev.material_config_hash(BOT, {})
    for _ in range(5):
        repo.stamp_session_config(run_id, h, "paper")
    repo.finish_run(run_id, status="completed", reason_code="session_complete", reason_text="d")
    assert ev.gather(USER, BOT, {}).unlocked


def test_evidence_is_per_bot(db_path):
    """Bot 3's paper week says nothing about Bot 4's fly."""
    _finished_paper_day({})
    assert ev.gather(USER, BOT, {}).unlocked
    assert not ev.gather(USER, BOT_IRON_FLY_SCALPER, {}).unlocked


def test_cycles_inherit_the_run_hash_including_its_voiding(db_path):
    """`open_cycle` copies the hash from the run inside the INSERT, so a voided day's
    cycles are voided too rather than pointing at settings they only half ran on."""
    run_id = repo.open_session_run(USER, BOT)
    repo.stamp_session_config(run_id, ev.material_config_hash(BOT, {}), "paper")
    first = repo.open_cycle(
        USER, BOT, run_id, structure="long_ce", legs=[], lots=1, entry_value=1.0
    )
    repo.stamp_session_config(run_id, "some-other-hash", "paper")
    second = repo.open_cycle(
        USER, BOT, run_id, structure="long_ce", legs=[], lots=1, entry_value=1.0
    )
    with repo._connect() as conn:
        rows = {
            r["id"]: r["config_hash"]
            for r in conn.execute("SELECT id, config_hash FROM bot_cycles").fetchall()
        }
    assert rows[first.id] == ev.material_config_hash(BOT, {})
    assert rows[second.id] is None


def test_open_live_cycles_are_findable_for_exit_only_management(db_path):
    """What lets the loop keep managing a real position after the user sets the bot Off."""
    run_id = repo.open_session_run(USER, BOT)
    paper_cycle = repo.open_cycle(
        USER, BOT, run_id, structure="long_ce", legs=[], lots=1, entry_value=1.0, paper=True
    )
    repo.open_cycle(
        USER, BOT, run_id, structure="long_ce", legs=[], lots=1, entry_value=1.0, paper=False
    )
    assert repo.bots_with_open_live_cycles() == [(USER, BOT)]
    del paper_cycle  # an abandoned simulation has no exchange side to manage
