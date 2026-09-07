"""Scalper config models (app.domain.bots).

Defaults here ARE the agreed policy, so they are asserted rather than assumed. The ladder
validator is the load-bearing one: the exit loop's invariant is that a stop only ratchets
up, and the cheapest place to guarantee that is refusing a config that cannot satisfy it.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from icici_breeze_backend.app.domain.bots import (
    IronFlyScalperConfig,
    MomentumLongScalperConfig,
    SessionWindow,
    TrailingLadderConfig,
)


# --- Bot 3 ----------------------------------------------------------------------------


def test_bot3_defaults_match_agreed_policy():
    c = MomentumLongScalperConfig()
    assert c.mode == "paper"               # arming live is a deliberate act
    assert c.trade_on_expiry_day is False
    # 25,000, not the source conversation's 10,000: at 10,000 a single ATM lot is
    # unaffordable beyond ~3 days to expiry on a 75 lot (plan section 8.5).
    assert c.premium_outlay_inr == 25000.0
    assert c.signal.volume_multiplier == 1.5
    assert c.risk.consecutive_loss_limit == 3
    assert c.risk.api_budget_reserve_calls == 25


def test_bot3_morning_window_starts_after_warmup():
    """09:35, not 09:20: the volume MA needs 20 bars from market open, so an earlier
    window could only ever log `not_warm`."""
    assert MomentumLongScalperConfig().sessions[0].start == "09:35"


def test_target_is_a_trailing_trigger_above_level_2():
    """Reaching `target_pts` starts the runner rather than closing the trade."""
    c = MomentumLongScalperConfig()
    assert c.exits.target_pts > c.exits.level_2_trigger_pts > c.exits.level_1_trigger_pts


@pytest.mark.parametrize(
    "override, expected",
    [
        ({"level_2_trigger_pts": 4.0}, "Level 2 must trigger above level 1"),
        ({"target_pts": 7.0}, "runner target must sit above level 2"),
        ({"level_2_lock_pts": 1.0}, "Level 2 must lock in more than level 1"),
        # Must clear the level-2 checks first, or this trips an earlier rule instead.
        (
            {"level_1_trigger_pts": 2.0, "level_1_lock_pts": 3.0},
            "Level 1 cannot lock in more than it has gained",
        ),
        ({"level_2_lock_pts": 9.0}, "Level 2 cannot lock in more than it has gained"),
    ],
)
def test_ladder_rejects_configs_that_would_move_a_stop_backwards(override, expected):
    with pytest.raises(ValidationError) as exc:
        TrailingLadderConfig(**override)
    assert expected in str(exc.value)


def test_ladder_accepts_a_valid_non_default_shape():
    c = TrailingLadderConfig(
        target_pts=20.0,
        level_1_trigger_pts=8.0, level_1_lock_pts=2.0,
        level_2_trigger_pts=14.0, level_2_lock_pts=9.0,
    )
    assert c.target_pts == 20.0


# --- windows --------------------------------------------------------------------------


def test_session_window_must_end_after_it_starts():
    with pytest.raises(ValidationError):
        SessionWindow(start="11:30", end="09:30")
    with pytest.raises(ValidationError):
        SessionWindow(start="09:30", end="09:30")
    assert SessionWindow(start="09:30", end="11:30").end == "11:30"


def test_session_window_rejects_a_malformed_time():
    with pytest.raises(ValidationError):
        SessionWindow(start="9:30", end="11:30")


# --- Bot 4 ----------------------------------------------------------------------------


def test_bot4_defaults_match_agreed_policy():
    c = IronFlyScalperConfig()
    assert c.mode == "paper"
    assert c.structure.wing_width_points == 150.0
    assert c.structure.widen_above_vix is None      # the VIX rule ships off
    assert c.exits.max_spot_drift_pct == 0.35
    assert c.reentry.cooldown_minutes == 15 and c.reentry.max_range_pct == 0.15
    assert [(s.start, s.end) for s in c.sessions] == [("11:30", "13:30")]


def test_bot4_rejects_a_widened_wing_that_is_not_wider():
    with pytest.raises(ValidationError):
        IronFlyScalperConfig(
            structure={"wing_width_points": 200.0, "widen_above_vix": 18.0,
                       "widened_wing_width_points": 150.0}
        )


def test_bot4_accepts_the_vix_rule_when_it_widens():
    c = IronFlyScalperConfig(
        structure={"wing_width_points": 150.0, "widen_above_vix": 18.0,
                   "widened_wing_width_points": 200.0}
    )
    assert c.structure.widen_above_vix == 18.0


def test_the_two_bots_carry_independent_stops():
    """Separate stops sum: the combined daily downside is the total, not either one."""
    b3, b4 = MomentumLongScalperConfig(), IronFlyScalperConfig()
    combined = b3.risk.cumulative_stop_inr + b4.risk.cumulative_stop_inr
    assert combined == 20000.0


# --- session windows ------------------------------------------------------------------
#
# Configurable start/stop times, up to four windows per bot. The rules exist because every
# invalid shape here is silent at runtime: a bot with a window it can never trade in looks
# exactly like a bot having a quiet day.


def _momentum(**kw):
    return MomentumLongScalperConfig(**kw)


def test_both_bots_ship_with_valid_windows():
    """The defaults have to satisfy the rules, or `normalize_config`'s fallback is a trap."""
    assert [(w.start, w.end) for w in MomentumLongScalperConfig().sessions] == [
        ("09:35", "11:30"),
        ("13:30", "15:10"),
    ]
    assert [(w.start, w.end) for w in IronFlyScalperConfig().sessions] == [("11:30", "13:30")]


def test_multiple_windows_and_a_custom_square_off_are_accepted():
    """The feature itself: four windows, user-chosen bounds, user-chosen square-off."""
    c = _momentum(
        sessions=[
            {"start": "09:35", "end": "10:30"},
            {"start": "10:45", "end": "11:30"},
            {"start": "13:30", "end": "14:15"},
            {"start": "14:20", "end": "14:45"},
        ],
        hard_square_off_ist="14:50",
    )
    assert len(c.sessions) == 4
    assert c.hard_square_off_ist == "14:50"


def test_a_window_may_start_where_the_previous_one_ends():
    """Adjacent is not overlapping -- `in_window` uses start <= now < end, so they abut."""
    c = _momentum(
        sessions=[{"start": "09:35", "end": "11:30"}, {"start": "11:30", "end": "13:00"}]
    )
    assert len(c.sessions) == 2


def test_overlapping_windows_are_refused():
    """`in_window` returns the first match, so the second could never be reached."""
    with pytest.raises(ValidationError, match="overlap"):
        _momentum(
            sessions=[{"start": "09:35", "end": "11:30"}, {"start": "11:00", "end": "13:00"}]
        )


def test_windows_are_refused_out_of_order_too():
    """The overlap test sorts first: the user's typing order is not the schedule."""
    with pytest.raises(ValidationError, match="overlap"):
        _momentum(
            sessions=[{"start": "13:00", "end": "14:00"}, {"start": "09:35", "end": "13:30"}]
        )


def test_a_window_before_the_warmup_floor_is_refused():
    """Indicators are built from live ticks with no backfill -- 09:20 can only be not_warm."""
    with pytest.raises(ValidationError, match="09:35"):
        _momentum(sessions=[{"start": "09:20", "end": "11:30"}])


def test_a_window_ending_after_the_square_off_is_refused():
    """Otherwise the bot enters at 15:20 and is flattened on the next pass, paying friction
    for a position it never had a chance to hold."""
    with pytest.raises(ValidationError, match="square-off"):
        _momentum(sessions=[{"start": "13:30", "end": "15:25"}], hard_square_off_ist="15:15")


def test_moving_the_square_off_later_is_what_makes_a_later_window_legal():
    """The two settings are edited together, so the rule has to be satisfiable."""
    c = _momentum(sessions=[{"start": "13:30", "end": "15:25"}], hard_square_off_ist="15:28")
    assert c.sessions[0].end == "15:25"


def test_a_window_past_the_market_close_is_refused():
    with pytest.raises(ValidationError, match="market close"):
        _momentum(sessions=[{"start": "14:00", "end": "15:45"}], hard_square_off_ist="15:50")


def test_at_least_one_and_at_most_four_windows():
    with pytest.raises(ValidationError):
        _momentum(sessions=[])
    with pytest.raises(ValidationError):
        _momentum(sessions=[{"start": "09:35", "end": "10:00"}] * 5)


def test_an_impossible_clock_time_is_refused():
    """The old pattern accepted 25:00, which passes the model and then compares as a plain
    string against the clock -- a window that is simply never open."""
    with pytest.raises(ValidationError):
        SessionWindow(start="25:00", end="26:00")
    with pytest.raises(ValidationError):
        _momentum(hard_square_off_ist="29:59")


def test_the_iron_fly_enforces_the_same_rules():
    """Shared validator, not a copy: both bots take windows and both must refuse the same
    shapes."""
    with pytest.raises(ValidationError, match="square-off"):
        IronFlyScalperConfig(
            sessions=[{"start": "11:30", "end": "15:20"}], hard_square_off_ist="15:15"
        )
    c = IronFlyScalperConfig(
        sessions=[{"start": "09:35", "end": "11:00"}, {"start": "13:30", "end": "14:30"}]
    )
    assert len(c.sessions) == 2


def test_a_stored_config_that_fails_the_new_rules_falls_back_rather_than_bricking():
    """Validating on read is what lets policy tighten; the fallback is what keeps the bot
    list loading when it does."""
    from icici_breeze_backend.app.repositories.bots import normalize_config
    from icici_breeze_backend.app.db.bots_migrate import BOT_MOMENTUM_LONG_SCALPER

    stored = MomentumLongScalperConfig().model_dump()
    stored["sessions"] = [{"start": "09:20", "end": "11:30"}]  # legal before, refused now
    out = normalize_config(BOT_MOMENTUM_LONG_SCALPER, stored)
    assert out["sessions"][0]["start"] == "09:35"
