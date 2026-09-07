"""The scalper driver (services.bots.scalping.runtime).

Step 3's contract is narrow and worth pinning: the loop decides, logs and heartbeats, and
places nothing. It also must not fall over when the licence, the pacer or the feed are
unavailable -- it runs unattended for a whole session.
"""
from __future__ import annotations

import datetime

import pytest

from icici_breeze_backend.app.db.bots_migrate import (
    BOT_MOMENTUM_LONG_SCALPER,
    ensure_bots_tables,
)
from icici_breeze_backend.app.domain.bots import MomentumLongScalperConfig, ReasonCode
from icici_breeze_backend.app.repositories import bots as repo
from icici_breeze_backend.app.services.bots.scalping import runtime
from icici_breeze_backend.app.services.bots.scalping.decide import FeedHealth

USER = "u1"


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = str(tmp_path / "users_test.sqlite3")
    monkeypatch.setattr(repo, "_db_path", lambda: path)
    ensure_bots_tables(path)
    runtime.reset_state_for_tests()
    return path


@pytest.fixture
def stubbed(monkeypatch):
    """Isolate the driver from the broker, the licence and the clock."""
    monkeypatch.setattr(runtime, "_trading_allowed", lambda: True)
    monkeypatch.setattr(runtime, "_api_calls_remaining", lambda uid: 90)
    monkeypatch.setattr(runtime, "_is_expiry_day", lambda cfg: False)
    monkeypatch.setattr(
        runtime, "_feed_health", lambda cfg: FeedHealth(warm=True, stale=False, stale_seconds=0.0)
    )
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.market_calendar.is_trading_day", lambda now=None: True
    )
    monkeypatch.setattr(runtime, "now_ist", lambda: datetime.datetime(2026, 9, 8, 10, 0))


def test_a_pass_opens_a_session_run_and_heartbeats_it(db_path, stubbed):
    decision = runtime.tick_bot(USER, BOT_MOMENTUM_LONG_SCALPER, MomentumLongScalperConfig())
    assert decision.action == "enter"  # gates clear; the bot layer would take it from here

    runs = repo.list_runs(USER, bot_type=BOT_MOMENTUM_LONG_SCALPER)
    assert len(runs) == 1 and runs[0].status == "running" and runs[0].trigger == "session"

    import sqlite3

    with sqlite3.connect(db_path) as conn:
        beat = conn.execute("SELECT heartbeat_at FROM bot_runs WHERE id = ?", (runs[0].id,)).fetchone()[0]
    assert beat is not None


def test_repeated_passes_reuse_one_session_run(db_path, stubbed):
    cfg = MomentumLongScalperConfig()
    for _ in range(5):
        runtime.tick_bot(USER, BOT_MOMENTUM_LONG_SCALPER, cfg)
    assert len(repo.list_runs(USER, bot_type=BOT_MOMENTUM_LONG_SCALPER)) == 1


def test_step_3_places_nothing_and_opens_no_cycles(db_path, stubbed):
    """The whole point of this step: observable, and inert."""
    cfg = MomentumLongScalperConfig()
    for _ in range(3):
        assert runtime.tick_bot(USER, BOT_MOMENTUM_LONG_SCALPER, cfg).action == "enter"
    assert repo.list_cycles(USER, bot_type=BOT_MOMENTUM_LONG_SCALPER) == []
    assert repo.open_cycles(USER, BOT_MOMENTUM_LONG_SCALPER) == []


def test_a_session_run_is_written_even_on_a_quiet_day(db_path, stubbed, monkeypatch):
    """An unexplained no-trade day is exactly what the run log exists to prevent."""
    monkeypatch.setattr(runtime, "now_ist", lambda: datetime.datetime(2026, 9, 8, 12, 0))
    d = runtime.tick_bot(USER, BOT_MOMENTUM_LONG_SCALPER, MomentumLongScalperConfig())
    assert d.reason_code == ReasonCode.OUTSIDE_SESSION_WINDOW
    assert len(repo.list_runs(USER, bot_type=BOT_MOMENTUM_LONG_SCALPER)) == 1


def test_an_open_cycle_is_seen_as_a_held_position(db_path, stubbed):
    run_id = repo.open_session_run(USER, BOT_MOMENTUM_LONG_SCALPER)
    repo.open_cycle(
        USER, BOT_MOMENTUM_LONG_SCALPER, run_id,
        structure="long_ce", legs=[], lots=1, entry_value=1000.0,
    )
    d = runtime.tick_bot(USER, BOT_MOMENTUM_LONG_SCALPER, MomentumLongScalperConfig())
    assert d.action == "idle" and d.reason_code == "holding"


def test_an_unknown_licence_state_is_treated_as_read_only(monkeypatch):
    """Fail closed: an unreadable licence is not a licence."""
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.deployment_license_status.trading_mutations_allowed",
        lambda: (_ for _ in ()).throw(RuntimeError("portal unreachable")),
    )
    assert runtime._trading_allowed() is False


def test_an_unreadable_api_budget_blocks_entries_only(monkeypatch):
    """Unknown budget reads as none left, which is an entry gate and never an exit one."""
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.icici_api_pacing.GlobalIciciApiPacer.calls_in_window",
        staticmethod(lambda uid: (_ for _ in ()).throw(RuntimeError("boom"))),
    )
    assert runtime._api_calls_remaining(USER) == 0


def test_the_loop_cadence_follows_the_pb_sl_setting(monkeypatch):
    """One latency knob for everything watching an open position, not a second one."""
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.pnl_engine_settings.load_pnl_engine_settings",
        lambda: {"pnl_recompute_interval_seconds": 7.5},
    )
    assert runtime._interval_seconds() == pytest.approx(7.5)


def test_cadence_falls_back_when_the_setting_cannot_be_read(monkeypatch):
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.pnl_engine_settings.load_pnl_engine_settings",
        lambda: (_ for _ in ()).throw(RuntimeError("db locked")),
    )
    assert 1.0 <= runtime._interval_seconds() <= 30.0


def test_one_failing_bot_does_not_kill_the_pass(db_path, monkeypatch):
    """The loop runs unattended all session; a bad bot must not take the others with it."""
    monkeypatch.setattr(
        repo, "list_enabled_bots",
        lambda bot_type: (_ for _ in ()).throw(RuntimeError("db gone")),
    )
    runtime.tick()  # must not raise
