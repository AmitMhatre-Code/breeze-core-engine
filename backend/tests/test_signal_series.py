"""The signal grid's pure layer: bars, momentum, the series engine, replay parity.

docs/signals-streamline-plan.md sections 2-3. Every reading is a pure function of one-minute
bars, and the live publisher and the replay share one engine -- these tests pin both halves.
"""
from __future__ import annotations

import datetime

import pytest

from icici_breeze_backend.app.core.timezone import IST
from icici_breeze_backend.app.services.index_signal import bars as bars_mod
from icici_breeze_backend.app.services.index_signal.bars import Bar, LiveBarBuilder
from icici_breeze_backend.app.services.index_signal.mechanisms import (
    SeriesKey,
    all_keys,
    expansion_params,
)
from icici_breeze_backend.app.services.index_signal.momentum import MomentumEvaluator, MomentumParams
from icici_breeze_backend.app.services.index_signal.series import (
    SeriesEngine,
    apply_direction,
    replay_series,
)

DAY = datetime.date(2026, 9, 14)


def at(day: datetime.date, hh: int, mm: int, ss: int = 0) -> float:
    return datetime.datetime.combine(day, datetime.time(hh, mm, ss), IST).timestamp()


def bar(day: datetime.date, hh: int, mm: int, close: float, volume: float = 100.0, oi=None) -> Bar:
    return Bar(ts=at(day, hh, mm), close=close, volume=volume, oi=oi,
               open=close, high=close, low=close)


def session_bars(day: datetime.date, closes: list[float], volumes: list[float] | None = None) -> list[Bar]:
    start = at(day, 9, 15)
    vols = volumes or [100.0] * len(closes)
    return [
        Bar(ts=start + 60 * k, close=c, volume=v, open=c, high=c, low=c)
        for k, (c, v) in enumerate(zip(closes, vols))
    ]


# --------------------------------------------------------------------------------------
# Grid
# --------------------------------------------------------------------------------------


def test_grid_has_twelve_series_and_ids_round_trip():
    keys = all_keys()
    assert len(keys) == 12
    for key in keys:
        assert SeriesKey.parse(key.id) == key


def test_nifty_short_windows_read_oi_over_fifteen_minutes():
    p = expansion_params(SeriesKey("expansion", 1, "nifty"))
    assert p.window_minutes == 1 and p.oi_window == 15 and p.require_oi
    assert not expansion_params(SeriesKey("expansion", 5, "sensex")).require_oi


def test_unknown_duration_is_refused():
    with pytest.raises(ValueError):
        SeriesKey("momentum", 30, "nifty")


# --------------------------------------------------------------------------------------
# Live bars
# --------------------------------------------------------------------------------------


def test_live_builder_differences_the_counter_and_keeps_ohlc():
    b = LiveBarBuilder()
    t0 = at(DAY, 9, 15)
    assert b.ingest(t0 + 1, {"last": 100, "ttq": 1000, "OI": 50}) == []
    b.ingest(t0 + 20, {"last": 104, "ttq": 1100})
    b.ingest(t0 + 40, {"last": 99, "ttq": 1150})
    first = b.ingest(t0 + 61, {"last": 101, "ttq": 1200})
    assert len(first) == 1
    # The first bar has no baseline to difference against: unknown, never zero.
    assert first[0].volume is None
    assert (first[0].open, first[0].high, first[0].low, first[0].close) == (100, 104, 99, 99)
    second = b.flush(t0 + 125)
    assert second[0].volume == 50 and second[0].close == 101


def test_live_builder_writes_flat_bars_for_quiet_minutes_only_across_a_short_hole():
    b = LiveBarBuilder()
    t0 = at(DAY, 9, 15)
    b.ingest(t0 + 1, {"last": 100, "ttq": 10})
    b.ingest(t0 + 61, {"last": 100, "ttq": 12})
    out = b.ingest(t0 + 60 * 3 + 5, {"last": 101, "ttq": 15})
    assert [round(x.ts - t0) for x in out] == [60, 120]
    assert out[1].volume == 0.0 and out[1].close == 100
    # Quiet minutes are filled only while the last print is under five minutes old; a longer
    # silence is a feed problem, and the gap after it stays a gap the engines refuse to read.
    later = b.ingest(t0 + 60 * 20, {"last": 102, "ttq": 20})
    assert [round(x.ts - t0) for x in later] == [180, 240, 300, 360, 420]


def test_from_hist_treats_negative_volume_and_zero_oi_as_unknown():
    class H:
        ts = datetime.datetime(2026, 9, 15, 9, 3)
        open = high = low = close = 25000.0
        volume = -650
        oi = 0

    got = bars_mod.from_hist(H())
    assert got.volume is None and got.oi is None


# --------------------------------------------------------------------------------------
# Momentum
# --------------------------------------------------------------------------------------


def _warm(ev: MomentumEvaluator, day: datetime.date, n: int = 20) -> None:
    for x in session_bars(day, [100.0] * n, [100.0] * n):
        ev.on_bar(x)


def test_momentum_fires_bullish_on_trend_vwap_and_top_volume():
    ev = MomentumEvaluator(MomentumParams(candle_minutes=1))
    prev = DAY - datetime.timedelta(days=1)
    _warm(ev, prev)  # volume ranking carries across the night
    closes = [100.0] * 9 + [101.0]
    volumes = [100.0] * 9 + [500.0]
    results = [ev.on_bar(x) for x in session_bars(DAY, closes, volumes)]
    last = results[-1]
    assert last is not None and last.side == "bullish"
    assert last.components["volume_rank"] == 1.0


def test_momentum_levels_reset_at_the_open_so_a_gap_is_not_a_breakout():
    ev = MomentumEvaluator(MomentumParams(candle_minutes=1))
    _warm(ev, DAY - datetime.timedelta(days=1))
    # Gap up 2% at the open on heavy volume: the EMA is rebuilt from today's candles, so there is
    # no trend reading until nine candles exist today.
    first = ev.on_bar(bar(DAY, 9, 15, 102.0, volume=900.0))
    assert first is not None and first.side is None and first.reason == "warming_up"


def test_momentum_evaluates_five_minute_candles_only_when_they_close():
    ev = MomentumEvaluator(MomentumParams(candle_minutes=5))
    got = [ev.on_bar(x) for x in session_bars(DAY, [100.0] * 10)]
    assert [g is not None for g in got] == [False, False, False, False, True] * 2


def test_momentum_short_candle_has_unknown_volume():
    ev = MomentumEvaluator(MomentumParams(candle_minutes=5))
    ev.on_bar(bar(DAY, 9, 15, 100.0))
    # 09:16-09:19 missing: the next bar belongs to the 09:20 candle.
    closed = ev.on_bar(bar(DAY, 9, 20, 100.0))
    assert closed is not None and closed.components["volume"] is None


# --------------------------------------------------------------------------------------
# Series engine: a call's lifetime and availability
# --------------------------------------------------------------------------------------


class _FixedEvaluator:
    """Stands in for a mechanism: fires what it is told, per bar."""

    def __init__(self, script: dict[float, str | None]):
        self.script = script

    def on_bar(self, b: Bar):
        from icici_breeze_backend.app.services.index_signal.states import Evaluation

        if not bars_mod.in_session(b):
            return None
        side = self.script.get(b.ts)
        return Evaluation(side, 0.9 if side else 0.1, {}, None if side else "no_expansion")


def _engine(duration: int, script: dict[float, str | None]) -> SeriesEngine:
    eng = SeriesEngine(SeriesKey("expansion", duration, "sensex"))
    eng._evaluator = _FixedEvaluator(script)  # noqa: SLF001 -- test seam
    return eng


def test_a_one_minute_call_is_visible_at_the_bar_close_it_fired_on():
    t = at(DAY, 10, 0)
    eng = _engine(1, {t: "bullish"})
    eng.on_bar(bar(DAY, 10, 0, 100.0))
    snap = eng.snapshot(t + 60 + 2)
    assert snap["state"] == "bullish" and snap["call_started_at"] == t + 60
    assert eng.snapshot(t + 120 + 1)["state"] == "neutral"


def test_a_refired_call_extends_and_keeps_its_start():
    t = at(DAY, 10, 0)
    eng = _engine(5, {t: "bearish", t + 180: "bearish"})
    for k in range(5):
        eng.on_bar(bar(DAY, 10, k, 100.0))
    snap = eng.snapshot(t + 5 * 60)
    assert snap["state"] == "bearish"
    assert snap["call_started_at"] == t + 60
    assert snap["held_until"] == t + 240 + 300


def test_consecutive_firing_candles_are_one_call():
    t = at(DAY, 10, 0)
    eng = _engine(1, {t: "bullish", t + 60: "bullish", t + 120: None})
    for k in range(3):
        eng.on_bar(bar(DAY, 10, k, 100.0))
        snap = eng.snapshot(t + 60 * (k + 1))
        if k < 2:
            assert snap["state"] == "bullish" and snap["call_started_at"] == t + 60
    assert snap["state"] == "neutral"


def test_unavailable_outside_the_session_and_when_stale():
    t = at(DAY, 10, 0)
    eng = _engine(5, {t: "bullish"})
    eng.on_bar(bar(DAY, 10, 0, 100.0))
    assert eng.snapshot(at(DAY, 15, 20))["reason"] == "outside_session"
    assert eng.snapshot(t + 60 + 200)["state"] == "unavailable"
    assert eng.snapshot(t + 60 + 200)["reason"] == "stale"


def test_fade_swaps_the_side_but_never_turns_unavailable_into_a_trade():
    assert apply_direction({"state": "bullish", "signal": 0.9}, "fade")["state"] == "bearish"
    assert apply_direction({"state": "unavailable"}, "fade")["state"] == "unavailable"
    assert apply_direction({"state": "bullish"}, "follow")["state"] == "bullish"


def test_fifteen_minute_momentum_reads_warming_up_before_todays_first_candle():
    key = SeriesKey("momentum", 15, "nifty")
    eng = SeriesEngine(key)
    eng.seed(session_bars(DAY - datetime.timedelta(days=1), [100.0] * 360))
    eng.seed(session_bars(DAY, [100.0] * 5))
    snap = eng.snapshot(at(DAY, 9, 20, 5))
    assert snap["state"] == "unavailable" and snap["reason"] == "warming_up"


# --------------------------------------------------------------------------------------
# Live and replay are the same function of the bars
# --------------------------------------------------------------------------------------


def _wiggly_day(day: datetime.date) -> list[Bar]:
    import math

    closes = [25000 + 40 * math.sin(k / 7.0) + (k % 13) * 3 for k in range(360)]
    volumes = [1000 + (k * 37) % 900 for k in range(360)]
    oi = [1.8e7 + 500 * k for k in range(360)]
    start = at(day, 9, 15)
    return [
        Bar(ts=start + 60 * k, close=c, volume=float(v), oi=o, open=c - 1, high=c + 2, low=c - 3)
        for k, (c, v, o) in enumerate(zip(closes, volumes, oi))
    ]


@pytest.mark.parametrize("key", all_keys(), ids=lambda k: k.id)
def test_live_feed_and_replay_produce_identical_readings(key):
    history = _wiggly_day(DAY - datetime.timedelta(days=1)) + _wiggly_day(DAY)
    replayed = [snap for _b, snap in replay_series(history, key)]
    live = SeriesEngine(key)
    live_snaps = []
    for b in history:
        live.on_bar(b)
        if bars_mod.in_session(b):
            live_snaps.append(live.snapshot(b.close_ts))
    strip = lambda s: {k: v for k, v in s.items() if k != "computed_at"}  # noqa: E731
    assert [strip(s) for s in live_snaps] == [strip(s) for s in replayed]
    assert any(s["state"] != "unavailable" for s in replayed[360:])
