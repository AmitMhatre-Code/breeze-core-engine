"""Volume-confirmed price expansion with OI quadrants (#34): the pure evaluation.

A call's lifetime and availability belong to the series engine now; see test_signal_series.py."""
from __future__ import annotations

import pytest

from icici_breeze_backend.app.services.index_signal import expansion as ex
from icici_breeze_backend.app.services.index_signal.states import REASON_WARMING_UP

B = 1_800_000_000.0  # bar 0's start; every bar below is a minute apart


def flat(n: int, *, close: float = 100.0, volume: float = 100.0, oi: float = 1_000_000.0) -> list[ex.Bar]:
    return [ex.Bar(ts=B + 60 * i, close=close, volume=volume, oi=oi) for i in range(n)]


def params(**kw) -> ex.ExpansionParams:
    base = dict(window_minutes=15, baseline_bars=120, min_baseline_bars=60)
    base.update(kw)
    return ex.ExpansionParams(**base)


# -- the pieces --------------------------------------------------------------------------


def test_percentile_rank_counts_strictly_below():
    # A dead market where every bar is identical must rank at 0 and therefore never fire:
    # the top tail has to be a real tail, not a tie.
    assert ex.percentile_rank([5.0] * 10, 5.0) == 0.0
    assert ex.percentile_rank([1.0, 2.0, 3.0, 4.0], 3.5) == 0.75
    assert ex.percentile_rank([], 1.0) is None


def test_quadrants_map_to_sides_and_unwinds_are_not_reversals():
    assert ex.quadrant(10.0, 500.0) == "new_longs"
    assert ex.quadrant(10.0, -500.0) == "short_covering"
    assert ex.quadrant(-10.0, 500.0) == "new_shorts"
    assert ex.quadrant(-10.0, -500.0) == "long_liquidation"
    assert ex.QUADRANT_SIDE["new_longs"] == "bullish"
    assert ex.QUADRANT_SIDE["new_shorts"] == "bearish"
    # An unwind refuses to call; it never calls the other way.
    assert ex.QUADRANT_SIDE["short_covering"] is None
    assert ex.QUADRANT_SIDE["long_liquidation"] is None


def test_a_window_under_fifteen_minutes_is_refused():
    # Median 1-minute OI change is under 0.01% of outstanding -- there is nothing to read.
    with pytest.raises(ValueError, match="at least 15"):
        params(window_minutes=5)


def test_thresholds_must_be_percentiles_and_the_baseline_must_outlast_the_window():
    with pytest.raises(ValueError, match="percentile"):
        params(price_percentile=1.5)
    with pytest.raises(ValueError, match="percentile"):
        params(volume_percentile=0.0)
    with pytest.raises(ValueError, match="baseline must be longer"):
        params(window_minutes=60, min_baseline_bars=60, baseline_bars=120)


def test_a_window_with_one_unknown_volume_reports_no_volume_at_all():
    bars = flat(20)
    bars[10] = ex.Bar(ts=bars[10].ts, close=100.0, volume=None, oi=1_000_000.0)
    reading = ex._window_reading(bars, 19, 15)
    assert reading is not None
    # Summing the rest would understate the window and suppress the filter exactly when the
    # feed has just come up.
    assert reading[1] is None


# -- evaluate ----------------------------------------------------------------------------


def test_too_few_bars_is_warming_up_not_a_reading():
    side, strength, _c, reason = ex.evaluate(flat(40), params())
    assert side is None and strength is None
    assert reason == REASON_WARMING_UP


def test_a_quiet_market_is_a_reading_of_no_expansion():
    side, strength, components, reason = ex.evaluate(flat(100), params())
    assert side is None
    assert reason == ex.REASON_NO_EXPANSION
    # Still reports a number: the shadow log scores a continuous value, not three states.
    assert strength == 0.0
    assert components["price_rank"] == 0.0


def _burst(*, up: bool, oi_rises: bool, n: int = 100) -> list[ex.Bar]:
    """A flat baseline then one bar that jumps on heavy volume, with OI moving either way."""
    bars = flat(n)
    last = bars[-1]
    bars[-1] = ex.Bar(
        ts=last.ts,
        close=100.0 + (2.0 if up else -2.0),
        volume=10_000.0,
        oi=1_000_000.0 + (50_000.0 if oi_rises else -50_000.0),
    )
    return bars


def test_price_up_on_volume_with_rising_oi_is_bullish():
    side, strength, components, reason = ex.evaluate(_burst(up=True, oi_rises=True), params())
    assert side == "bullish"
    assert reason is None
    assert components["quadrant"] == "new_longs"
    assert strength == pytest.approx(1.0)


def test_price_down_on_volume_with_rising_oi_is_bearish():
    side, _s, components, reason = ex.evaluate(_burst(up=False, oi_rises=True), params())
    assert side == "bearish"
    assert reason is None
    assert components["quadrant"] == "new_shorts"


def test_the_same_rally_on_falling_oi_is_an_unwind_and_calls_nothing():
    side, strength, components, reason = ex.evaluate(_burst(up=True, oi_rises=False), params())
    assert side is None
    assert reason == ex.REASON_UNWIND
    assert components["quadrant"] == "short_covering"
    # The price and volume were extreme; it is the confirmation that is missing, not the move.
    assert strength == pytest.approx(1.0)


def test_without_oi_required_the_same_bars_call_on_price_and_volume_alone():
    """SENSEX: ICICI serves no OI for BSE, so the quadrant half cannot run there."""
    bars = _burst(up=True, oi_rises=False)
    side, _s, components, reason = ex.evaluate(bars, params(require_oi=False))
    assert side == "bullish"  # the unwind that neutralised NIFTY cannot be seen here
    assert reason is None
    assert components["quadrant"] is None


def test_missing_oi_when_it_is_required_is_no_reading_not_a_neutral_one():
    bars = _burst(up=True, oi_rises=True)
    bars = [ex.Bar(ts=b.ts, close=b.close, volume=b.volume, oi=None) for b in bars]
    side, _s, _c, reason = ex.evaluate(bars, params())
    assert side is None
    assert reason == ex.REASON_NO_OI


def test_a_zero_oi_bar_is_treated_as_absent_not_as_a_reading_of_zero():
    """ICICI serves OI 0 on pre-open bars and on *every* BSE bar, so a zero reaching the
    engine is likely, not hypothetical. Anchoring a window on one would turn the next real
    value into the largest OI rise ever recorded, and call it `new_longs`."""
    bars = _burst(up=True, oi_rises=True)
    leaked = list(bars)
    anchor = leaked[-16]  # the window's opening bar
    leaked[-16] = ex.Bar(ts=anchor.ts, close=anchor.close, volume=anchor.volume, oi=0.0)
    side, strength, components, reason = ex.evaluate(leaked, params())
    assert components["oi_delta"] is None
    assert side is None
    assert reason == ex.REASON_NO_OI
    # The price and volume halves still read normally -- only the confirmation is missing.
    assert strength == pytest.approx(1.0)


# -- windows and gaps ---------------------------------------------------------------------


def test_a_window_spanning_the_overnight_break_is_not_a_reading():
    """Without this, the first bars of every session measure the gap move as a 15-minute
    expansion on 15 minutes' volume, and the signal opens screaming."""
    yesterday = flat(80)
    overnight = 17 * 3600
    today = [
        ex.Bar(ts=yesterday[-1].ts + overnight + 60 * i, close=103.0, volume=100.0, oi=1_000_000.0)
        for i in range(5)
    ]
    reading = ex._window_reading(yesterday + today, 84, 15)
    assert reading is None


def test_a_small_feed_hole_is_tolerated_and_a_large_one_invalidates_the_window():
    """The tolerance is deliberate. Invalidating a window for one missing bar would make the
    signal fragile on a feed that drops ticks by design; the cost is that the volume sum is
    one bar short, which biases *against* firing and so fails safe."""
    bars = flat(100)
    one_missing = [b for i, b in enumerate(bars) if i != 90]
    assert ex._window_reading(one_missing, len(one_missing) - 1, 15) is not None

    # Six consecutive bars gone is a seven-minute hole, past max_gap_seconds.
    many_missing = [b for i, b in enumerate(bars) if not 88 <= i <= 93]
    assert ex._window_reading(many_missing, len(many_missing) - 1, 15) is None
