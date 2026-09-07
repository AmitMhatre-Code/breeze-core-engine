"""The scalpers' gate stack (services.bots.scalping.decide).

The rule this file exists to protect: **a gate that blocks entering must never block
leaving.** Read-only mode, an expiry day, a closed window, a cooldown and an exhausted API
budget are all reasons not to open a position; none is a reason to abandon one that is
already open with a stop running against it.
"""
from __future__ import annotations

import datetime

import pytest

from icici_breeze_backend.app.domain.bots import (
    IronFlyScalperConfig,
    MomentumLongScalperConfig,
    ReasonCode,
    ScalperDayTotals,
)
from icici_breeze_backend.app.services.bots.scalping.decide import (
    FeedHealth,
    Snapshot,
    decide,
    in_cooldown,
    in_window,
)

CFG = MomentumLongScalperConfig()          # windows 09:35-11:30, 13:30-15:10; square-off 15:15
FLY = IronFlyScalperConfig()               # window 11:30-13:30


def _at(hhmm: str) -> datetime.datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    return datetime.datetime(2026, 9, 8, h, m)


def _snap(**kw) -> Snapshot:
    base = dict(
        now_ist=_at("10:00"),
        trading_allowed=True,
        is_trading_day=True,
        is_expiry_day=False,
        feed=FeedHealth(warm=True, stale=False, stale_seconds=0.0),
        totals=ScalperDayTotals(),
        has_open_position=False,
        api_calls_remaining=90,
    )
    base.update(kw)
    return Snapshot(**base)


# --- windows and cooldown -------------------------------------------------------------


@pytest.mark.parametrize(
    "hhmm, inside",
    [("09:34", False), ("09:35", True), ("11:29", True), ("11:30", False), ("14:00", True)],
)
def test_window_boundaries_are_start_inclusive_end_exclusive(hhmm, inside):
    assert (in_window(_at(hhmm), CFG.sessions) is not None) is inside


def test_cooldown_holds_until_the_configured_time_has_passed():
    now = _at("10:00")
    totals = ScalperDayTotals(consecutive_losses=3, last_closed_at="2026-09-08 09:45:00")
    assert in_cooldown(totals, now, consecutive_loss_limit=3, cooldown_minutes=30) is True
    assert in_cooldown(totals, now, consecutive_loss_limit=3, cooldown_minutes=10) is False


def test_cooldown_needs_the_full_streak():
    now = _at("10:00")
    totals = ScalperDayTotals(consecutive_losses=2, last_closed_at="2026-09-08 09:59:00")
    assert in_cooldown(totals, now, consecutive_loss_limit=3, cooldown_minutes=30) is False


def test_an_unreadable_last_close_holds_the_cooldown():
    """The streak is real either way; holding is the conservative reading."""
    totals = ScalperDayTotals(consecutive_losses=3, last_closed_at="not-a-timestamp")
    assert in_cooldown(totals, _at("10:00"), consecutive_loss_limit=3, cooldown_minutes=30)


# --- entry gates ----------------------------------------------------------------------


def test_all_gates_clear_gives_the_bot_specific_layer_its_turn():
    d = decide(_snap(), CFG)
    assert d.action == "enter" and d.reason_code == "gates_clear"


@pytest.mark.parametrize(
    "override, code",
    [
        ({"trading_allowed": False}, ReasonCode.TRADING_READ_ONLY),
        ({"is_trading_day": False}, ReasonCode.MARKET_CLOSED),
        ({"is_expiry_day": True}, ReasonCode.NOT_A_FIRING_DAY),
        ({"now_ist": _at("12:00")}, ReasonCode.OUTSIDE_SESSION_WINDOW),
        ({"sg_rule_conflict": True}, ReasonCode.SG_RULE_CONFLICT),
        ({"api_calls_remaining": 5}, ReasonCode.API_BUDGET_LOW),
        ({"feed": FeedHealth(warm=True, stale=True, stale_seconds=12.0)}, ReasonCode.STALE_FEED),
        ({"feed": FeedHealth(warm=False, stale=False)}, ReasonCode.NOT_WARM),
    ],
)
def test_each_entry_gate_blocks_with_its_own_reason(override, code):
    d = decide(_snap(**override), CFG)
    assert d.reason_code == code
    assert d.action in ("idle", "stand_down")


def test_expiry_day_can_be_opted_into():
    cfg = MomentumLongScalperConfig(trade_on_expiry_day=True)
    assert decide(_snap(is_expiry_day=True), cfg).action == "enter"


def test_cooldown_blocks_entry_and_reports_the_streak():
    totals = ScalperDayTotals(consecutive_losses=3, last_closed_at="2026-09-08 09:55:00")
    d = decide(_snap(totals=totals), CFG)
    assert d.reason_code == ReasonCode.COOLDOWN_ACTIVE
    assert d.detail["consecutive_losses"] == 3


def test_breaching_the_daily_stop_stands_the_bot_down():
    totals = ScalperDayTotals(realized_net_pnl=-10000.0)
    d = decide(_snap(totals=totals), CFG)
    assert d.action == "stand_down" and d.reason_code == ReasonCode.TERMINATED_FOR_DAY


def test_the_daily_stop_outranks_every_other_entry_gate():
    """Terminal-for-the-day is checked first so nothing after it can assume otherwise."""
    d = decide(
        _snap(
            totals=ScalperDayTotals(realized_net_pnl=-10000.0),
            trading_allowed=False,
            is_trading_day=False,
        ),
        CFG,
    )
    assert d.reason_code == ReasonCode.TERMINATED_FOR_DAY


# --- exits: the ordering rule ---------------------------------------------------------


def test_an_open_position_is_never_stranded_by_an_entry_gate():
    """The central rule. None of these is a reason to stop managing a live position."""
    for override in (
        {"trading_allowed": False},
        {"is_expiry_day": True},
        {"now_ist": _at("12:00")},                       # outside Bot 3's windows
        {"api_calls_remaining": 0},
        {"sg_rule_conflict": True},
        {"totals": ScalperDayTotals(consecutive_losses=9, last_closed_at="2026-09-08 09:59:00")},
        {"feed": FeedHealth(warm=False, stale=False)},
    ):
        d = decide(_snap(has_open_position=True, **override), CFG)
        assert d.action == "idle", f"{override} stranded the position: {d}"
        assert d.reason_code == "holding"


def test_hard_square_off_closes_whatever_is_open():
    d = decide(_snap(has_open_position=True, now_ist=_at("15:15")), CFG)
    assert d.action == "exit" and d.reason_code == ReasonCode.SQUARE_OFF


def test_breaching_the_daily_stop_closes_an_open_position():
    d = decide(
        _snap(has_open_position=True, totals=ScalperDayTotals(realized_net_pnl=-10500.0)), CFG
    )
    assert d.action == "exit" and d.reason_code == ReasonCode.TERMINATED_FOR_DAY


def test_a_brief_feed_blip_does_not_flatten_a_position():
    """Flattening on a hiccup costs a round trip of friction -- the binding constraint."""
    d = decide(
        _snap(has_open_position=True, feed=FeedHealth(warm=True, stale=True, stale_seconds=12.0)),
        CFG,
        stale_exit_seconds=60.0,
    )
    assert d.action == "idle"


def test_a_sustained_blackout_does_flatten_it():
    d = decide(
        _snap(has_open_position=True, feed=FeedHealth(warm=True, stale=True, stale_seconds=61.0)),
        CFG,
        stale_exit_seconds=60.0,
    )
    assert d.action == "exit" and d.reason_code == ReasonCode.STALE_FEED


def test_the_position_layers_own_verdict_is_carried_through():
    d = decide(
        _snap(has_open_position=True, position_exit=(ReasonCode.TRAILING_STOP, "Trailed out.")),
        CFG,
    )
    assert d.action == "exit" and d.reason_code == ReasonCode.TRAILING_STOP


def test_obligations_outrank_the_position_layers_opinion():
    """Square-off must win over a ladder that has not fired yet."""
    d = decide(
        _snap(
            has_open_position=True,
            now_ist=_at("15:20"),
            position_exit=(ReasonCode.TRAILING_STOP, "Trailed out."),
        ),
        CFG,
    )
    assert d.reason_code == ReasonCode.SQUARE_OFF


# --- the two bots differ on one thing -------------------------------------------------


def test_bot4_flattens_when_its_window_closes_but_bot3_does_not():
    """Bot 4's fly is a window trade; cutting Bot 3's runner on the clock defeats its ladder."""
    fly = decide(
        _snap(has_open_position=True, now_ist=_at("13:45"), exit_at_window_end=True), FLY
    )
    assert fly.action == "exit" and fly.reason_code == ReasonCode.SQUARE_OFF

    long_ = decide(
        _snap(has_open_position=True, now_ist=_at("12:00"), exit_at_window_end=False), CFG
    )
    assert long_.action == "idle"
