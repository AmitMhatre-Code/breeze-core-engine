"""Momentum v1-v3 side by side (guide/technical/design-decisions.md #72).

v3 answers an outside review of v2: volume judged for the duration's job (a burst for the 1m
scalper reading, the same time of day for 5m/15m, so the U-shaped intraday volume curve is out
of the comparison), and a minimum move of half an ATR past the trend line. v1 and v2 keep
running unchanged until v3 has been judged.
"""
from __future__ import annotations

import datetime
from typing import Optional

from icici_breeze_backend.app.core.timezone import IST
from icici_breeze_backend.app.services.index_signal import momentum as mo
from icici_breeze_backend.app.services.index_signal.bars import Bar
from icici_breeze_backend.app.services.index_signal.mechanisms import SeriesKey, make_evaluator

BASE = 24_000.0
FIRST_DAY = datetime.date(2026, 3, 2)


def _day(n: int) -> datetime.date:
    return FIRST_DAY + datetime.timedelta(days=n)


def _bar(day: datetime.date, minute: int, close: float, volume: float, *, open_: Optional[float] = None,
         hour: int = 9, start_minute: int = 15) -> Bar:
    ts = datetime.datetime.combine(day, datetime.time(hour, start_minute), IST).timestamp() + 60 * minute
    o = close if open_ is None else open_
    return Bar(ts=ts, close=close, volume=volume, oi=None, open=o, high=max(o, close) + 1, low=min(o, close) - 1)


def _flat_day(day: datetime.date, per_minute: float = 100.0, *, heavy_until: int = 0,
              heavy: float = 1_000.0) -> list[Bar]:
    """A flat session; the first `heavy_until` minutes trade `heavy` a minute (a busy open)."""
    return [_bar(day, m, BASE, heavy if m < heavy_until else per_minute) for m in range(360)]


def _feed(engine, bars) -> list:
    return [e for e in (engine.on_bar(b) for b in bars) if e is not None]


def _v(version: int, duration: int):
    return make_evaluator(SeriesKey("momentum", duration, "nifty", version))


# --------------------------------------------------------------------------------------
# The three definitions
# --------------------------------------------------------------------------------------


def test_each_version_is_its_own_parameter_set():
    p1 = mo.MomentumParams(candle_minutes=5, carry_ema=False)
    assert _v(1, 5).params == p1
    assert _v(2, 5).params == mo.MomentumParams(candle_minutes=5)
    v3_1m, v3_15m = _v(3, 1).params, _v(3, 15).params
    assert (v3_1m.volume_test, v3_1m.burst_lookback) == ("burst", 3)
    assert (v3_15m.volume_test, v3_15m.slot_sessions, v3_15m.slot_min_sessions) == ("slot", 10, 8)
    assert v3_1m.atr_period == v3_15m.atr_period == 14 and v3_1m.atr_fraction == 0.5


def test_v1_rebuilds_its_trend_line_every_morning():
    v1, v2 = _v(1, 5), _v(2, 5)
    for engine in (v1, v2):
        _feed(engine, _flat_day(_day(0)))
    day_two = _flat_day(_day(1))[:60]
    first_v1 = _feed(v1, day_two)
    first_v2 = _feed(v2, day_two)
    assert first_v2[0].components["ema"] is not None
    assert [r.components["ema"] is None for r in first_v1[:9]] == [True] * 8 + [False]


# --------------------------------------------------------------------------------------
# v3 at one minute: the burst test
# --------------------------------------------------------------------------------------


def _burst_day_two(last_close: float, last_volume: float) -> list[Bar]:
    day = _day(1)
    return [_bar(day, m, BASE, 100.0) for m in range(3)] + [
        _bar(day, 3, last_close, last_volume, open_=BASE)
    ]


def test_the_burst_baseline_restarts_each_session():
    """The open is never ranked against yesterday's last minutes -- the review's false positive."""
    engine = _v(3, 1)
    _feed(engine, _flat_day(_day(0)))
    first_three = _feed(engine, _burst_day_two(BASE + 10, 500.0))[:3]
    assert [r.reason for r in first_three] == ["warming_up"] * 3
    assert [r.components["volume_baseline"] for r in first_three] == [0, 1, 2]


def test_a_candle_that_out_trades_each_of_the_last_three_and_clears_the_line_fires():
    engine = _v(3, 1)
    _feed(engine, _flat_day(_day(0)))
    last = _feed(engine, _burst_day_two(BASE + 10, 500.0))[-1]
    assert last.side == "bullish", (last.reason, last.components)
    assert last.components["volume_test"] == "burst" and last.components["volume_rank"] == 1.0


def test_a_burst_must_beat_every_one_of_the_three():
    engine = _v(3, 1)
    _feed(engine, _flat_day(_day(0)))
    day = _day(1)
    bars = [_bar(day, 0, BASE, 100.0), _bar(day, 1, BASE, 100.0), _bar(day, 2, BASE, 600.0),
            _bar(day, 3, BASE + 10, 500.0, open_=BASE)]
    last = _feed(engine, bars)[-1]
    assert last.side is None and last.reason == "volume_below_threshold"


def test_a_close_a_whisker_past_the_line_is_not_a_call():
    """On the right side of both lines, on a burst -- but not half an ATR past the trend line."""
    engine = _v(3, 1)
    _feed(engine, _flat_day(_day(0)))
    last = _feed(engine, _burst_day_two(BASE + 1, 500.0))[-1]
    assert last.side is None and last.reason == "move_too_small"
    assert 0 < last.components["ema_distance_atr"] < 0.5


def test_v2_calls_the_same_whisker():
    """The contrast that justifies the ATR test: v2 has no minimum move."""
    engine = _v(2, 1)
    day0 = [_bar(_day(0), m, BASE, 100.0) for m in range(360)]
    _feed(engine, day0)
    last = _feed(engine, _burst_day_two(BASE + 1, 5_000.0))[-1]
    assert last.side == "bullish"


def test_the_first_candle_true_range_ignores_the_overnight_gap():
    engine = _v(3, 1)
    _feed(engine, _flat_day(_day(0)))
    before = engine._atr  # noqa: SLF001
    gapped = BASE + 300
    _feed(engine, [_bar(_day(1), 0, gapped, 100.0)])
    # The candle's own range is 2 points (high - low); the 300-point gap does not enter the ATR.
    assert abs(engine._atr - before) < 0.1  # noqa: SLF001


# --------------------------------------------------------------------------------------
# v3 at five and fifteen minutes: the same time of day
# --------------------------------------------------------------------------------------


def _slot_history(engine, sessions: int) -> None:
    for n in range(sessions):
        _feed(engine, _flat_day(_day(n), heavy_until=60))


def _rising_candle(day: datetime.date, candle: int, per_minute: float) -> list[Bar]:
    """The five one-minute bars of 5-minute candle `candle`, climbing 2 points a minute."""
    out = []
    for k in range(5):
        minute = candle * 5 + k
        out.append(_bar(day, minute, BASE + 2 * (k + 1), per_minute, open_=BASE + 2 * k))
    return out


def _slot_day(day: datetime.date, candle: int, per_minute: float) -> list[Bar]:
    flat = _flat_day(day, heavy_until=60)
    return flat[: candle * 5] + _rising_candle(day, candle, per_minute)


def test_the_slot_test_waits_for_eight_sessions_of_its_time_of_day():
    engine = _v(3, 5)
    _slot_history(engine, 7)
    last = _feed(engine, _slot_day(_day(7), 15, 150.0))[-1]
    assert last.reason == "warming_up" and last.components["volume_baseline"] == 7


def test_a_quiet_hour_is_ranked_against_itself_not_against_the_busy_open():
    """10:30 trading half again its usual is unusual for 10:30, whatever 09:15 did. v2 ranks the
    same candle against the busy first hour just before it and calls it light."""
    v2, v3 = _v(2, 5), _v(3, 5)
    for engine in (v2, v3):
        _slot_history(engine, 8)
    on_v3 = _feed(v3, _slot_day(_day(8), 15, 150.0))[-1]
    on_v2 = _feed(v2, _slot_day(_day(8), 15, 150.0))[-1]
    assert on_v3.side == "bullish", (on_v3.reason, on_v3.components)
    assert on_v3.components["volume_test"] == "slot" and on_v3.components["volume_baseline"] == 8
    assert on_v2.side is None and on_v2.reason == "volume_below_threshold"


def test_the_slot_baseline_holds_ten_sessions():
    engine = _v(3, 15)
    _slot_history(engine, 12)
    last = _feed(engine, _flat_day(_day(12))[:15])[-1]
    assert last.components["volume_baseline"] == 10


# --------------------------------------------------------------------------------------
# The pre-open, recorded beside VWAP but never decided on
# --------------------------------------------------------------------------------------


def test_vwap_without_the_pre_open_is_recorded_beside_the_vwap_used():
    engine = _v(3, 1)
    _feed(engine, _flat_day(_day(0)))
    day = _day(1)
    pre_open = [_bar(day, m, BASE - 50, 10_000.0, hour=9, start_minute=0) for m in range(15)]
    _feed(engine, pre_open)
    first = _feed(engine, [_bar(day, 0, BASE, 100.0)])[0]
    assert first.components["vwap"] < first.components["vwap_ex_preopen"]
    assert first.components["vwap_ex_preopen"] == BASE
