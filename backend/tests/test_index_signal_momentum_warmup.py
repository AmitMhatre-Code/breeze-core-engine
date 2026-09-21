"""The momentum trend line across the overnight break.

Rebuilt from scratch each morning, EMA(9) on d-minute candles said nothing until 09:15 + 9d --
11:30 at fifteen minutes, on every one of 117 replayed sessions, for the reading the navbar
shows. It now starts from where yesterday's line ended, moved by the overnight gap, which keeps
the property the reset rule existed to protect: a gap on its own can never fire a call.
"""
from __future__ import annotations

import datetime

from icici_breeze_backend.app.core.timezone import IST
from icici_breeze_backend.app.services.index_signal import momentum as mo
from icici_breeze_backend.app.services.index_signal.bars import Bar

DAY_ONE = datetime.date(2026, 3, 9)
DAY_TWO = datetime.date(2026, 3, 10)


def _bars(day: datetime.date, closes: list[float], volume: float = 1_000.0) -> list[Bar]:
    start = datetime.datetime.combine(day, datetime.time(9, 15), IST).timestamp()
    return [Bar(ts=start + 60 * i, close=c, volume=volume, oi=None, open=c, high=c, low=c)
            for i, c in enumerate(closes)]


def _engine(candle_minutes: int = 15) -> mo.MomentumEvaluator:
    return mo.MomentumEvaluator(mo.MomentumParams(candle_minutes=candle_minutes))


def _feed(engine, bars) -> list:
    return [e for e in (engine.on_bar(b) for b in bars) if e is not None]


def _session(base: float, minutes: int = 360, drift: float = 0.0) -> list[float]:
    return [base + drift * i for i in range(minutes)]


def test_without_a_previous_session_the_old_nine_candle_wait_still_applies():
    """A cold cache has no line to carry, and inventing one is worse than saying nothing."""
    engine = _engine(15)
    results = _feed(engine, _bars(DAY_ONE, _session(24_000.0, 200, drift=0.1)))
    early = [r for r in results if r.components.get("ema") is not None]
    # 200 minutes is 13 completed 15-minute candles; only those from the 9th carry a line.
    assert len(early) == len(results) - 8


def test_the_second_session_has_a_trend_line_from_its_very_first_candle():
    engine = _engine(15)
    _feed(engine, _bars(DAY_ONE, _session(24_000.0, 360, drift=0.1)))
    day_two = _feed(engine, _bars(DAY_TWO, _session(24_050.0, 60, drift=0.1)))
    assert day_two, "the second session produced no completed candles"
    assert day_two[0].components["ema"] is not None
    # And it is the FIRST candle of the day, not the ninth.
    assert day_two[0].components["candles_today"] == 1


def test_a_flat_gapped_open_puts_the_first_close_on_the_line_not_above_it():
    """The whole point of the reset rule, kept: however far the market gaps, a session that then
    does nothing sits exactly on its trend line, so the gap alone cannot fire a call."""
    for gap in (-900.0, -100.0, 0.0, 100.0, 900.0):
        engine = _engine(15)
        _feed(engine, _bars(DAY_ONE, _session(24_000.0, 360)))          # dead flat all day
        opened_at = 24_000.0 + gap
        day_two = _feed(engine, _bars(DAY_TWO, _session(opened_at, 30)))  # flat again after the gap
        assert day_two
        first = day_two[0].components
        assert first["ema"] is not None
        assert abs(first["close"] - first["ema"]) < 1e-6, f"gap {gap} moved the close off the line"


def test_a_gap_that_keeps_running_does_fire_the_line():
    """It is trading away from the gap-adjusted line that counts, not the gap."""
    engine = _engine(15)
    _feed(engine, _bars(DAY_ONE, _session(24_000.0, 360)))
    rising = _feed(engine, _bars(DAY_TWO, _session(24_100.0, 30, drift=1.0)))
    assert rising
    assert rising[0].components["close"] > rising[0].components["ema"]


def test_the_seed_decays_so_a_replay_and_a_live_engine_converge():
    """Live warms on two sessions and a replay on more. They start from different seeds, and the
    line has to forget that difference quickly or the two would not agree on the same day."""
    short_warm, long_warm = _engine(15), _engine(15)
    _feed(short_warm, _bars(DAY_ONE, _session(24_000.0, 360, drift=0.05)))
    for k, base in enumerate((23_000.0, 23_500.0, 24_000.0)):
        day = DAY_ONE - datetime.timedelta(days=6 - k * 2)
        _feed(long_warm, _bars(day, _session(base, 360, drift=0.05)))
    _feed(long_warm, _bars(DAY_ONE, _session(24_000.0, 360, drift=0.05)))

    today = _bars(DAY_TWO, _session(24_020.0, 360, drift=0.05))
    a, b = _feed(short_warm, today), _feed(long_warm, today)
    assert len(a) == len(b) and a
    # Different histories, yet by the ninth candle they agree to well under a tenth of a point.
    assert abs(a[8].components["ema"] - b[8].components["ema"]) < 0.1


def test_vwap_is_still_rebuilt_every_session():
    """Only the trend line is carried. VWAP is an average of one session's trading."""
    engine = _engine(15)
    _feed(engine, _bars(DAY_ONE, _session(20_000.0, 360)))
    day_two = _feed(engine, _bars(DAY_TWO, _session(24_000.0, 30)))
    assert day_two
    assert abs(day_two[0].components["vwap"] - 24_000.0) < 1e-6
