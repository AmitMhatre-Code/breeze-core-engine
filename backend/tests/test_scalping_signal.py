"""Bot 3's entry, read from the signal grid (docs/signals-streamline-plan.md section 7).

The momentum computation itself lives in `index_signal.momentum` (see test_signal_series.py);
this is the bot's side: a published reading as an entry, one trade per call, and the exit when
the call that opened the trade ends.
"""
from __future__ import annotations

from icici_breeze_backend.app.services.bots.scalping.signal import (
    call_ended,
    call_unbroken,
    evaluate_reading,
)
from icici_breeze_backend.app.services.index_signal.series import apply_direction

SERIES = "nifty:momentum:1m"


def call(state: str, started: float = 1000.0, until: float = 1060.0, **kw) -> dict:
    return {"state": state, "call_started_at": started, "held_until": until, "signal": 0.9,
            "mechanism": "momentum", "duration_minutes": 1, **kw}


def test_a_bullish_call_buys_the_call_and_carries_when_it_began():
    result = evaluate_reading(call("bullish"), SERIES)
    assert result.fired and result.right == "call"
    assert result.values["candle_start"] == 1000 and result.values["series"] == SERIES


def test_a_bearish_call_buys_a_put_never_sells():
    assert evaluate_reading(call("bearish"), SERIES).right == "put"


def test_quiet_and_unavailable_are_no_trade_and_say_why():
    quiet = evaluate_reading({"state": "neutral"}, SERIES)
    assert not quiet.fired and quiet.reason == "no_call"
    dark = evaluate_reading({"state": "unavailable", "reason": "warming_up"}, SERIES)
    assert not dark.fired and dark.reason == "signal_unavailable:warming_up"


def test_fade_trades_the_other_side_but_never_trades_unavailable():
    assert evaluate_reading(apply_direction(call("bullish"), "fade"), SERIES).right == "put"
    faded_dark = apply_direction({"state": "unavailable", "reason": "stale"}, "fade")
    assert not evaluate_reading(faded_dark, SERIES).fired


def test_one_trade_per_call():
    assert call_unbroken(call("bullish"), entry_candle_start=1000, side="bullish")
    assert not call_unbroken(call("bullish", started=1300.0), entry_candle_start=1000, side="bullish")
    assert not call_unbroken(call("bearish"), entry_candle_start=1000, side="bullish")
    assert not call_unbroken({"state": "neutral"}, entry_candle_start=1000, side="bullish")


def test_a_standing_call_extends_the_trade():
    ended, until = call_ended(call("bullish", until=1180.0), started_at=1000.0, side="bullish",
                              known_until=1060.0, now=1100.0)
    assert not ended and until == 1180.0


def test_a_quiet_reading_or_the_other_side_ends_the_trade():
    for reading in ({"state": "neutral"}, call("bearish"), call("bullish", started=1300.0)):
        ended, _ = call_ended(reading, started_at=1000.0, side="bullish", known_until=1060.0, now=1070.0)
        assert ended


def test_an_unreadable_signal_holds_until_the_call_would_have_lapsed():
    dark = {"state": "unavailable", "reason": "stale"}
    assert call_ended(dark, started_at=1000.0, side="bullish", known_until=1060.0, now=1030.0) == (False, 1060.0)
    assert call_ended(dark, started_at=1000.0, side="bullish", known_until=1060.0, now=1060.0)[0]


# -- what closes a signal trade -----------------------------------------------------------


def test_a_call_going_quiet_no_longer_closes_a_trade():
    """The call's own length used to be the maximum hold, which left the trailing stop no room
    to move: 89% of one-minute calls simply lapsed, so nearly every trade was closed by that
    clock rather than by anything about the trade."""
    from icici_breeze_backend.app.services.bots.scalping.signal import call_reversed

    quiet = {"state": "neutral", "reason": "no_expansion"}
    assert call_reversed(quiet, side="bullish") is False


def test_an_unreadable_signal_is_not_a_reversal():
    """A feed blip must hold a position, never flatten one (#30)."""
    from icici_breeze_backend.app.services.bots.scalping.signal import call_reversed

    for reason in ("warming_up", "stale", "not_published", "anchor_not_traded"):
        assert call_reversed({"state": "unavailable", "reason": reason}, side="bullish") is False


def test_a_call_the_other_way_closes_the_trade():
    from icici_breeze_backend.app.services.bots.scalping.signal import call_reversed

    assert call_reversed({"state": "bearish"}, side="bullish") is True
    assert call_reversed({"state": "bullish"}, side="bearish") is True


def test_a_fresh_call_the_same_way_holds():
    """A re-fire is the signal repeating itself, not contradicting itself."""
    from icici_breeze_backend.app.services.bots.scalping.signal import call_reversed

    assert call_reversed({"state": "bullish", "call_started_at": 9_999.0}, side="bullish") is False
