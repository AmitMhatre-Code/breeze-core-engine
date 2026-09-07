"""The safety guards that gate live enablement (services.bots.scalping.guards).

Build order step 7 exists to be finished before step 9 turns live dispatch on. These are the
failures that are invisible until they cost money: a Strategy Group rule quietly squaring off
the bot's legs, a daily stop that a restart resets, and an ordinary trading day logged as a
crash.
"""
from __future__ import annotations

import datetime

import pytest

from icici_breeze_backend.app.db.bots_migrate import (
    BOT_MOMENTUM_LONG_SCALPER,
    ensure_bots_tables,
)
from icici_breeze_backend.app.domain.bots import (
    IronFlyScalperConfig,
    MomentumLongScalperConfig,
    ReasonCode,
    ScalperDayTotals,
)
from icici_breeze_backend.app.repositories import bots as repo
from icici_breeze_backend.app.services import portfolio_pnl_engine as engine
from icici_breeze_backend.app.services.bots.scalping import guards, runtime
from icici_breeze_backend.app.services.bots.scalping.decide import (
    FeedHealth,
    Snapshot,
    decide,
    stop_breached,
)

USER = "u1"
EXPIRY = "10-Sep-2026"


class FakeProc:
    def fetch_stock_codes(self, exchange_code):
        return [{"stock_code": "NIFTY", "expiry_dates": ["2026-09-10T06:00:00.000Z"]}]


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = str(tmp_path / "users_test.sqlite3")
    monkeypatch.setattr(repo, "_db_path", lambda: path)
    ensure_bots_tables(path)
    runtime.reset_state_for_tests()
    engine.clear_group_rule(USER, "NIFTY", EXPIRY)
    yield path
    engine.clear_group_rule(USER, "NIFTY", EXPIRY)


@pytest.fixture
def sent(monkeypatch):
    """Capture Telegram alerts instead of sending them."""
    messages: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.telegram_alerts._notify",
        lambda user_id, text, *, kind: messages.append((kind, text)),
    )
    return messages


# --- the Strategy Group collision -----------------------------------------------------


def test_no_conflict_when_no_rule_is_armed(db):
    assert guards.find_sg_conflict(FakeProc(), USER) is None


def test_a_rule_on_the_traded_expiry_is_a_conflict(db):
    engine.set_group_rule(USER, "r1", stock_code="NIFTY", expiry_display=EXPIRY, stop_loss_pnl=-500.0)
    conflict = guards.find_sg_conflict(FakeProc(), USER)
    assert conflict is not None
    assert conflict.expiry_display == EXPIRY and conflict.rule_id == "r1"


def test_a_rule_on_a_different_expiry_is_not_a_conflict(db):
    """The group key is (stock_code, expiry); another expiry is a different group."""
    engine.set_group_rule(USER, "r1", stock_code="NIFTY", expiry_display="17-Sep-2026", stop_loss_pnl=-500.0)
    assert guards.find_sg_conflict(FakeProc(), USER) is None


def test_disarming_clears_the_rule_and_alerts_the_user(db, sent):
    engine.set_group_rule(USER, "r1", stock_code="NIFTY", expiry_display=EXPIRY, stop_loss_pnl=-500.0)
    conflict = guards.find_sg_conflict(FakeProc(), USER)

    guards.disarm_conflicting_rule(USER, conflict)

    assert engine.group_rule_for(USER, "NIFTY", EXPIRY) is None
    assert len(sent) == 1
    kind, text = sent[0]
    assert kind == "scalping_sg_conflict"
    assert EXPIRY in text
    assert "stop the scalping bot first" in text.lower()


def test_the_alert_names_other_positions_left_unprotected(db, sent, monkeypatch):
    """The accepted hazard, made explicit: the rule covers the whole group, not just the bot.

    Disarming it removes the stop from any other legs the user holds on that expiry, so the
    message has to say so rather than leave it to be discovered.
    """
    engine.set_group_rule(USER, "r1", stock_code="NIFTY", expiry_display=EXPIRY, stop_loss_pnl=-500.0)
    monkeypatch.setattr(guards, "nearest_expiry", lambda proc: EXPIRY)
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.portfolio_pnl_engine.group_legs_for_user",
        lambda u, s, e: [object()] * 5,
    )
    conflict = guards.find_sg_conflict(FakeProc(), USER, bot_leg_count=1)
    assert conflict.other_legs == 4

    guards.disarm_conflicting_rule(USER, conflict)
    assert "unprotected" in sent[0][1].lower()
    assert "4 other leg" in sent[0][1]


def test_an_unreachable_user_does_not_stop_the_disarm(db, monkeypatch):
    engine.set_group_rule(USER, "r1", stock_code="NIFTY", expiry_display=EXPIRY, stop_loss_pnl=-500.0)
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.telegram_alerts._notify",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("telegram down")),
    )
    guards.disarm_conflicting_rule(USER, guards.find_sg_conflict(FakeProc(), USER))
    assert engine.group_rule_for(USER, "NIFTY", EXPIRY) is None  # still disarmed


def test_a_conflict_blocks_entry_but_never_strands_a_position():
    """It gates opening. A held position is dealt with by disarming the rule instead."""
    base = dict(
        now_ist=datetime.datetime(2026, 9, 8, 10, 0), trading_allowed=True, is_trading_day=True,
        is_expiry_day=False, feed=FeedHealth(warm=True, stale=False), totals=ScalperDayTotals(),
        api_calls_remaining=90, sg_rule_conflict=True,
    )
    cfg = MomentumLongScalperConfig()
    assert decide(Snapshot(has_open_position=False, **base), cfg).reason_code == ReasonCode.SG_RULE_CONFLICT
    assert decide(Snapshot(has_open_position=True, **base), cfg).action == "idle"


# --- the cumulative stop --------------------------------------------------------------


def test_unrealized_counts_towards_the_daily_stop():
    """Realized alone would let a bot sit deep underwater and keep opening more."""
    totals = ScalperDayTotals(realized_net_pnl=-6_000.0)
    assert stop_breached(totals, 10_000.0) is False
    assert stop_breached(totals, 10_000.0, unrealized=-4_500.0) is True


def test_an_unpriceable_position_falls_back_to_the_realized_test():
    """Zero, not an error: the realized half is durable and must keep working."""
    assert guards.unrealized_pnl(None, None) == 0.0
    assert guards.unrealized_pnl(object(), None) == 0.0


def test_unrealized_is_signed_correctly_for_each_structure():
    class Cycle:
        def __init__(self, entry, structure):
            self.entry_value, self.structure = entry, structure

    # Long option: mark above entry is a profit.
    assert guards.unrealized_pnl(Cycle(1_000.0, "long_ce"), 1_400.0) == pytest.approx(400.0)
    # Credit structure: entry_value is credit RECEIVED, mark is the cost to buy it back.
    assert guards.unrealized_pnl(Cycle(9_000.0, "iron_fly"), 7_500.0) == pytest.approx(1_500.0)
    assert guards.unrealized_pnl(Cycle(9_000.0, "iron_fly"), 11_000.0) == pytest.approx(-2_000.0)


def test_breaching_the_stop_disarms_the_bot(db, sent):
    repo.get_or_create_bot(USER, BOT_MOMENTUM_LONG_SCALPER)
    repo.update_bot(USER, BOT_MOMENTUM_LONG_SCALPER, enabled=True)

    guards.disarm_bot(USER, BOT_MOMENTUM_LONG_SCALPER, "Daily loss limit reached.")

    assert repo.get_or_create_bot(USER, BOT_MOMENTUM_LONG_SCALPER).enabled is False
    assert sent and sent[0][0] == "scalping_daily_stop"
    assert "re-enable" in sent[0][1]


def test_the_stop_is_recomputed_from_rows_so_a_restart_cannot_reset_it(db):
    """No in-memory counter: a container upgrade must not let a stopped bot resume."""
    run_id = repo.open_session_run(USER, BOT_MOMENTUM_LONG_SCALPER)
    cycle = repo.open_cycle(
        USER, BOT_MOMENTUM_LONG_SCALPER, run_id, structure="long_ce", legs=[], lots=1
    )
    repo.close_cycle(
        cycle.id, exit_reason_code=ReasonCode.STOP_LOSS, exit_reason_text="x",
        gross_pnl=-10_500.0, friction=100.0,
    )
    totals = repo.scalper_day_totals(USER, BOT_MOMENTUM_LONG_SCALPER)
    assert stop_breached(totals, 10_000.0) is True


# --- session finalisation -------------------------------------------------------------


def test_the_day_is_over_only_after_the_last_window_and_the_square_off():
    cfg = MomentumLongScalperConfig()  # windows to 15:10, square-off 15:15
    assert guards.session_is_over(cfg, datetime.datetime(2026, 9, 8, 14, 0)) is False
    assert guards.session_is_over(cfg, datetime.datetime(2026, 9, 8, 15, 11)) is False
    assert guards.session_is_over(cfg, datetime.datetime(2026, 9, 8, 15, 15)) is True


def test_bot4s_earlier_window_still_waits_for_the_square_off():
    """Its window closes at 13:30, but nothing may be open past 15:15 either way."""
    cfg = IronFlyScalperConfig()
    assert guards.session_is_over(cfg, datetime.datetime(2026, 9, 8, 14, 0)) is False
    assert guards.session_is_over(cfg, datetime.datetime(2026, 9, 8, 15, 20)) is True


def test_finalising_writes_a_completed_run_with_a_summary(db):
    """Otherwise the row stays `running`, the heartbeat stops, and the 30-minute reaper
    marks an ordinary trading day as "Interrupted before it finished"."""
    run_id = repo.open_session_run(USER, BOT_MOMENTUM_LONG_SCALPER)
    cycle = repo.open_cycle(
        USER, BOT_MOMENTUM_LONG_SCALPER, run_id, structure="long_ce", legs=[], lots=1
    )
    repo.close_cycle(
        cycle.id, exit_reason_code=ReasonCode.TARGET_HIT, exit_reason_text="x",
        gross_pnl=650.0, friction=100.0,
    )

    guards.finalise_session(
        USER, BOT_MOMENTUM_LONG_SCALPER, run_id,
        reason_code="session_complete", reason_text="The day's last trading window has closed.",
    )

    run = repo.list_runs(USER, bot_type=BOT_MOMENTUM_LONG_SCALPER)[0]
    assert run.status == "completed"
    assert run.detail["cycles"] == 1 and run.detail["net_pnl"] == pytest.approx(550.0)


def test_a_finalised_session_is_not_reaped_as_interrupted(db):
    run_id = repo.open_session_run(USER, BOT_MOMENTUM_LONG_SCALPER)
    guards.finalise_session(
        USER, BOT_MOMENTUM_LONG_SCALPER, run_id,
        reason_code="session_complete", reason_text="done",
    )
    assert repo.reap_stale_runs() == 0
    assert repo.list_runs(USER, bot_type=BOT_MOMENTUM_LONG_SCALPER)[0].status == "completed"


# --- through the driver ---------------------------------------------------------------


def _drive(monkeypatch, now=datetime.datetime(2026, 9, 8, 10, 0)):
    proc = FakeProc()
    monkeypatch.setattr(runtime, "_trading_allowed", lambda: True)
    monkeypatch.setattr(runtime, "_api_calls_remaining", lambda uid: 90)
    monkeypatch.setattr(runtime, "_is_expiry_day", lambda cfg: False)
    monkeypatch.setattr(
        runtime, "_feed_health", lambda cfg: FeedHealth(warm=True, stale=False, stale_seconds=0.0)
    )
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.market_calendar.is_trading_day", lambda now=None: True
    )
    monkeypatch.setattr(runtime, "now_ist", lambda: now)
    monkeypatch.setattr("icici_breeze_backend.app.services.processor.processor", lambda: proc)
    monkeypatch.setattr(runtime.momentum_bot, "execute", lambda *a, **k: None)
    monkeypatch.setattr(runtime.momentum_bot, "inspect_position", lambda *a, **k: None)

    class Builder:
        candles, session_vwap = [], 24_000.0

    class Feed:
        builder = Builder()

    monkeypatch.setattr(runtime.futures_feed, "get_feed", lambda: Feed())
    return proc


def test_the_driver_blocks_entry_while_a_pbsl_rule_is_armed(db, sent, monkeypatch):
    _drive(monkeypatch)
    engine.set_group_rule(USER, "r1", stock_code="NIFTY", expiry_display=EXPIRY, stop_loss_pnl=-500.0)

    decision = runtime.tick_bot(USER, BOT_MOMENTUM_LONG_SCALPER, MomentumLongScalperConfig())

    assert decision.reason_code == ReasonCode.SG_RULE_CONFLICT
    # Nothing is held, so the rule is left alone -- it is the user's and it is doing no harm.
    assert engine.group_rule_for(USER, "NIFTY", EXPIRY) is not None
    assert sent == []


def test_the_driver_disarms_a_rule_armed_while_a_position_is_held(db, sent, monkeypatch):
    _drive(monkeypatch)
    run_id = repo.open_session_run(USER, BOT_MOMENTUM_LONG_SCALPER)
    repo.open_cycle(
        USER, BOT_MOMENTUM_LONG_SCALPER, run_id, structure="long_ce",
        legs=[{"right": "call", "strike_price": 24_000.0, "quantity": 75,
               "expiry_display": EXPIRY}],
        lots=1, entry_value=7_500.0,
    )
    engine.set_group_rule(USER, "r1", stock_code="NIFTY", expiry_display=EXPIRY, stop_loss_pnl=-500.0)

    runtime.tick_bot(USER, BOT_MOMENTUM_LONG_SCALPER, MomentumLongScalperConfig())

    assert engine.group_rule_for(USER, "NIFTY", EXPIRY) is None
    assert sent and sent[0][0] == "scalping_sg_conflict"


def test_the_driver_disarms_the_bot_once_its_daily_stop_is_hit_and_it_is_flat(db, sent, monkeypatch):
    _drive(monkeypatch)
    repo.update_bot(USER, BOT_MOMENTUM_LONG_SCALPER, enabled=True)
    run_id = repo.open_session_run(USER, BOT_MOMENTUM_LONG_SCALPER)
    cycle = repo.open_cycle(
        USER, BOT_MOMENTUM_LONG_SCALPER, run_id, structure="long_ce", legs=[], lots=1
    )
    repo.close_cycle(
        cycle.id, exit_reason_code=ReasonCode.STOP_LOSS, exit_reason_text="x",
        gross_pnl=-10_400.0, friction=100.0,
    )

    decision = runtime.tick_bot(USER, BOT_MOMENTUM_LONG_SCALPER, MomentumLongScalperConfig())

    assert decision.reason_code == ReasonCode.TERMINATED_FOR_DAY
    assert repo.get_or_create_bot(USER, BOT_MOMENTUM_LONG_SCALPER).enabled is False
    assert any(kind == "scalping_daily_stop" for kind, _ in sent)


def test_the_bot_is_not_disarmed_while_it_still_holds_a_position(db, sent, monkeypatch):
    """Switching a bot off with an open position would leave it unmanaged."""
    _drive(monkeypatch)
    repo.update_bot(USER, BOT_MOMENTUM_LONG_SCALPER, enabled=True)
    run_id = repo.open_session_run(USER, BOT_MOMENTUM_LONG_SCALPER)
    losing = repo.open_cycle(
        USER, BOT_MOMENTUM_LONG_SCALPER, run_id, structure="long_ce", legs=[], lots=1
    )
    repo.close_cycle(
        losing.id, exit_reason_code=ReasonCode.STOP_LOSS, exit_reason_text="x",
        gross_pnl=-10_400.0, friction=100.0,
    )
    repo.open_cycle(
        USER, BOT_MOMENTUM_LONG_SCALPER, run_id, structure="long_ce",
        legs=[{"right": "call", "strike_price": 24_000.0, "quantity": 75,
               "expiry_display": EXPIRY}],
        lots=1, entry_value=7_500.0,
    )

    runtime.tick_bot(USER, BOT_MOMENTUM_LONG_SCALPER, MomentumLongScalperConfig())

    assert repo.get_or_create_bot(USER, BOT_MOMENTUM_LONG_SCALPER).enabled is True


def test_the_driver_finalises_the_session_once_the_day_is_over(db, monkeypatch):
    _drive(monkeypatch, now=datetime.datetime(2026, 9, 8, 15, 30))
    runtime.tick_bot(USER, BOT_MOMENTUM_LONG_SCALPER, MomentumLongScalperConfig())

    runs = repo.list_runs(USER, bot_type=BOT_MOMENTUM_LONG_SCALPER)
    assert runs[0].status == "completed"
    assert runs[0].reason_code == "session_complete"


def test_the_session_is_finalised_once_not_on_every_later_pass(db, monkeypatch):
    _drive(monkeypatch, now=datetime.datetime(2026, 9, 8, 15, 30))
    cfg = MomentumLongScalperConfig()
    for _ in range(4):
        runtime.tick_bot(USER, BOT_MOMENTUM_LONG_SCALPER, cfg)
    assert len(repo.list_runs(USER, bot_type=BOT_MOMENTUM_LONG_SCALPER)) == 1
