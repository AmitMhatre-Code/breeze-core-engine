"""The trailing ladder (services.bots.scalping.ladder).

The invariant under test throughout is that a stop only ever ratchets UP. Every other
property here is secondary: a ladder that walks a stop backwards turns a banked winner into
a loser, silently, and only shows up as a bad day's P&L.
"""
from __future__ import annotations

import pytest

from icici_breeze_backend.app.domain.bots import ReasonCode, TrailingLadderConfig
from icici_breeze_backend.app.services.bots.scalping.ladder import (
    LEVEL_BREAK_EVEN,
    LEVEL_INITIAL,
    LEVEL_LOCK_PROFIT,
    LEVEL_RUNNER,
    LadderState,
    advance,
    exit_decision,
    open_ladder,
)

CFG = TrailingLadderConfig()  # target 10, sl 6, L1 5/+1.2, L2 8/+5, runner step 3
ENTRY = 100.0
T0 = 1_000_000.0


def _open():
    return open_ladder(ENTRY, T0, CFG)


def _walk(prices):
    state = _open()
    moves = []
    for p in prices:
        state, moved = advance(state, p, CFG)
        moves.append(moved)
    return state, moves


# --- ratcheting -----------------------------------------------------------------------


def test_initial_stop_sits_below_entry():
    s = _open()
    assert s.stop_price == pytest.approx(94.0)
    assert s.level == LEVEL_INITIAL
    assert s.peak_price == ENTRY


def test_levels_promote_as_gain_is_earned():
    s, _ = _walk([103.0])
    assert s.level == LEVEL_INITIAL and s.stop_price == pytest.approx(94.0)

    s, _ = _walk([105.0])
    assert s.level == LEVEL_BREAK_EVEN and s.stop_price == pytest.approx(101.2)

    s, _ = _walk([108.0])
    assert s.level == LEVEL_LOCK_PROFIT and s.stop_price == pytest.approx(105.0)

    s, _ = _walk([110.0])
    assert s.level == LEVEL_RUNNER and s.stop_price == pytest.approx(107.0)  # peak - 3


def test_a_single_jump_promotes_through_every_rung_it_earned():
    """A gap up must not need three ticks to reach the level it has already paid for."""
    s, _ = _walk([115.0])
    assert s.level == LEVEL_RUNNER
    assert s.stop_price == pytest.approx(112.0)


def test_stop_never_moves_backwards_on_a_pullback():
    s, _ = _walk([112.0, 104.0, 101.0])
    assert s.stop_price == pytest.approx(109.0)  # locked at the 112 peak, not re-derived
    assert s.peak_price == pytest.approx(112.0)
    assert s.level == LEVEL_RUNNER


def test_runner_trails_the_peak_not_the_current_price():
    s, _ = _walk([111.0, 120.0, 118.0])
    assert s.peak_price == pytest.approx(120.0)
    assert s.stop_price == pytest.approx(117.0)  # 120 - 3, unaffected by the pullback to 118


def test_stop_moved_flag_fires_only_on_a_real_transition():
    """This is what gates persistence: peaks move constantly, stops rarely."""
    _, moves = _walk([101.0, 102.0, 103.0])
    assert moves == [False, False, False]

    _, moves = _walk([105.0, 105.5, 106.0])
    assert moves[0] is True and moves[1:] == [False, False]

    # In the runner, each new high moves the stop; a pullback does not.
    _, moves = _walk([111.0, 113.0, 112.0])
    assert moves == [True, True, False]


# --- exits ----------------------------------------------------------------------------


def test_initial_stop_reports_as_a_stop_loss():
    s = _open()
    assert exit_decision(s, 95.0, T0 + 10, CFG) is None
    code, text = exit_decision(s, 94.0, T0 + 10, CFG)
    assert code == ReasonCode.STOP_LOSS
    assert "94.00" in text


def test_a_stop_hit_after_ratcheting_reports_as_a_trailing_stop():
    s, _ = _walk([112.0])
    code, _ = exit_decision(s, 109.0, T0 + 10, CFG)
    assert code == ReasonCode.TRAILING_STOP


def test_time_invalidation_uses_the_peak_gain_not_the_current_one():
    """'Did not achieve +N points' means it never got there.

    A trade that ran to +4 and came back has moved; it belongs to the stop, not the clock.
    """
    flat, _ = _walk([100.5, 101.0])
    code, _ = exit_decision(flat, 101.0, T0 + 90, CFG)
    assert code == ReasonCode.TIME_INVALIDATION

    moved, _ = _walk([104.0, 101.0])  # peak gain 4 >= 3, so the clock does not apply
    assert exit_decision(moved, 101.0, T0 + 90, CFG) is None


def test_time_invalidation_does_not_fire_early():
    s, _ = _walk([100.5])
    assert exit_decision(s, 100.5, T0 + 89, CFG) is None
    assert exit_decision(s, 100.5, T0 + 90, CFG) is not None


def test_stop_is_reported_in_preference_to_the_time_stop():
    """A trade that blew its stop AND went nowhere is a stop-out, not a timeout."""
    s = _open()
    code, _ = exit_decision(s, 90.0, T0 + 300, CFG)
    assert code == ReasonCode.STOP_LOSS


# --- persistence ----------------------------------------------------------------------


def test_state_round_trips_through_its_stored_form():
    s, _ = _walk([113.0, 111.0])
    restored = LadderState.from_detail(s.to_detail())
    assert restored == s


def test_a_restart_resumes_the_locked_stop_rather_than_resetting_it():
    """The whole reason state is persisted: an upgrade mid-trade must not un-ratchet."""
    s, _ = _walk([112.0])
    assert s.stop_price == pytest.approx(109.0)
    resumed = LadderState.from_detail(s.to_detail())
    assert resumed.stop_price == pytest.approx(109.0)
    # and a fresh ladder at the same entry would have been far looser
    assert open_ladder(ENTRY, T0, CFG).stop_price == pytest.approx(94.0)


@pytest.mark.parametrize("bad", [None, {}, {"entry_price": "x"}, [1, 2], {"entry_price": 1.0}])
def test_unusable_stored_state_returns_none_rather_than_guessing(bad):
    """A half-parsed ladder is more dangerous than an absent one."""
    assert LadderState.from_detail(bad) is None
