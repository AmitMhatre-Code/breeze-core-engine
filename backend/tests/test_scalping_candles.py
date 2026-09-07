"""Tick-to-candle maths for the scalping bots (services.bots.scalping.candles).

The load-bearing test here is `test_real_capture_avgprice_equals_ttv_over_ttq`: it pins the
finding the whole VWAP design rests on -- that ICICI's `ttv` is a crore-suffixed string and
`ttv x 1e7 / ttq` is exactly the tick's own `avgPrice`. If a future SDK or broker change
breaks that identity, this fails rather than the bot silently trading off a wrong VWAP.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from icici_breeze_backend.app.services.bots.scalping.candles import (
    BUCKET_SECONDS,
    VWAP_CROSS_CHECK_TOLERANCE,
    CandleBuilder,
    bucket_start,
    coerce_float,
    ema_of,
    parse_ttv,
)

_TICK_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "icici_ticks"

# Captures with irregular, real-looking values. The other two fixtures in that directory
# carry round numbers (ttq exactly 1,000,000) and do not satisfy the identity, so they are
# treated as hand-authored and deliberately excluded.
_REAL_CAPTURES = ("nifty_call_24000_raw.json", "nifty_call_25000_raw.json")


def _tick(name: str) -> dict:
    return json.loads((_TICK_FIXTURES / name).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- parsing


def test_coerce_float_treats_empty_string_as_absent():
    # Real captures carry "CHNGOI": "" -- absent, not zero.
    assert coerce_float("") is None
    assert coerce_float(None) is None
    assert coerce_float("61.2") == pytest.approx(61.2)
    assert coerce_float(61) == 61.0


def test_coerce_float_rejects_bool():
    # bool is an int subclass; a True must not silently become 1.0 in a price field.
    assert coerce_float(True) is None


def test_parse_ttv_handles_crore_suffix():
    assert parse_ttv("4473.68C") == pytest.approx(4473.68 * 1e7)
    assert parse_ttv("10.08C") == pytest.approx(10.08 * 1e7)
    assert parse_ttv("100") == pytest.approx(100.0)
    assert parse_ttv(250.0) == pytest.approx(250.0)


def test_parse_ttv_returns_none_for_unknown_suffix():
    # An unrecognised unit must disable the cross-check, never guess a multiplier.
    assert parse_ttv("123X") is None
    assert parse_ttv("") is None
    assert parse_ttv(None) is None


@pytest.mark.parametrize("name", _REAL_CAPTURES)
def test_real_capture_avgprice_equals_ttv_over_ttq(name):
    """The identity the VWAP source is chosen on. See this module's docstring."""
    tick = _tick(name)
    derived = parse_ttv(tick["ttv"]) / float(tick["ttq"])
    assert derived == pytest.approx(float(tick["avgPrice"]), rel=1e-3)


def test_real_capture_ltt_is_not_numeric():
    """Why candles bucket on arrival time: `ltt` is a locale-formatted string, not epoch."""
    tick = _tick("nifty_call_24000_raw.json")
    assert coerce_float(tick["ltt"]) is None


# --------------------------------------------------------------------------- bucketing


def test_bucket_start_aligns_to_the_minute():
    assert bucket_start(1_000_061.5) == 1_000_020
    assert bucket_start(1_000_020) == 1_000_020
    assert BUCKET_SECONDS == 60


def test_candle_closes_on_minute_rollover_with_ohlc():
    b = CandleBuilder()
    t = 1_000_000  # already minute-aligned
    for price in (100.0, 105.0, 95.0, 101.0):
        b.ingest(t + 1, price, ttq=1000, ttv="0.0001C", avg_price=100.0)
    assert b.candles == []  # nothing completes until the clock leaves the bucket

    b.ingest(t + BUCKET_SECONDS + 1, 102.0, ttq=1500, ttv="0.00015C", avg_price=100.0)
    candles = b.candles
    assert len(candles) == 1
    c = candles[0]
    assert (c.open, c.high, c.low, c.close) == (100.0, 105.0, 95.0, 101.0)
    assert c.ticks == 4


def test_first_candle_has_unknown_volume_not_zero():
    """No earlier cumulative reading exists, so the bar's volume is unknown."""
    b = CandleBuilder()
    t = 1_000_000
    b.ingest(t, 100.0, ttq=5000, ttv="0.0005C", avg_price=100.0)
    b.ingest(t + 61, 101.0, ttq=6000, ttv="0.0006C", avg_price=100.0)
    assert b.candles[0].volume is None
    assert b.volume_ma(1) is None  # an unknown bar must not count as zero


def test_volume_is_cumulative_difference_not_ltq_sum():
    b = CandleBuilder()
    t = 1_000_000
    b.ingest(t, 100.0, ttq=1_000, ttv="0.0001C", avg_price=100.0)
    b.ingest(t + 61, 100.0, ttq=3_000, ttv="0.0003C", avg_price=100.0)   # closes bar 1
    b.ingest(t + 121, 100.0, ttq=7_500, ttv="0.00075C", avg_price=100.0)  # closes bar 2
    b.ingest(t + 181, 100.0, ttq=8_000, ttv="0.0008C", avg_price=100.0)   # closes bar 3
    volumes = [c.volume for c in b.candles]
    assert volumes == [None, 2_000, 4_500]


def test_dropped_mid_bucket_tick_changes_nothing():
    """Only the LAST cumulative reading in a bucket is used, so mid-bar drops are free.

    This is the first half of why volume is differenced rather than summed from `ltq`: the
    tick pipeline drops on a full queue by design, and a summing builder would lose that
    quantity permanently from the bar and from every total built on it.
    """
    t = 1_000_020  # minute-aligned
    samples = [(t, 1_000), (t + 20, 2_000), (t + 40, 3_000), (t + 60, 4_000), (t + 120, 5_000)]

    def build(skip_ts=None):
        b = CandleBuilder()
        for ts, ttq in samples:
            if ts == skip_ts:
                continue
            b.ingest(ts, 100.0, ttq=ttq, ttv=f"{ttq * 100 / 1e7}C", avg_price=100.0)
        return [c.volume for c in b.candles]

    assert build() == [None, 1_000]
    assert build(skip_ts=t + 20) == [None, 1_000]  # identical despite the gap


def test_dropped_last_tick_of_a_bucket_misattributes_then_recovers():
    """Losing a bucket's final reading moves volume to the next bar -- it is never lost.

    The quantity shifts across the boundary and the very next bar agrees again, because the
    following tick carries the whole day's cumulative total. Summing `ltq` has no such
    recovery.
    """
    t = 1_000_020
    samples = [
        (t, 1_000), (t + 20, 2_000), (t + 40, 3_000),
        (t + 60, 4_000), (t + 120, 5_000), (t + 180, 6_000),
    ]

    def build(skip_ts=None):
        b = CandleBuilder()
        for ts, ttq in samples:
            if ts == skip_ts:
                continue
            b.ingest(ts, 100.0, ttq=ttq, ttv=f"{ttq * 100 / 1e7}C", avg_price=100.0)
        return [c.volume for c in b.candles]

    complete = build()
    lossy = build(skip_ts=t + 40)  # the last reading in bucket 0
    assert complete == [None, 1_000, 1_000]
    assert lossy == [None, 2_000, 1_000]  # 1,000 moved across the boundary, not out
    assert complete[-1] == lossy[-1]      # recovered by the following bar


def test_flush_closes_a_bar_with_no_further_ticks():
    """A thin contract can print nothing for a minute; the bar must still close."""
    b = CandleBuilder()
    t = 1_000_000
    b.ingest(t, 100.0, ttq=1_000, ttv="0.0001C", avg_price=100.0)
    assert b.candles == []
    assert b.flush(t + 61)
    assert len(b.candles) == 1
    assert b.flush(t + 61) == []  # idempotent within the same bucket


def test_out_of_order_tick_does_not_reopen_a_closed_bar():
    b = CandleBuilder()
    t = 1_000_000
    b.ingest(t, 100.0, ttq=1_000, ttv="0.0001C", avg_price=100.0)
    b.ingest(t + 61, 101.0, ttq=2_000, ttv="0.0002C", avg_price=100.0)
    assert len(b.candles) == 1
    b.ingest(t + 5, 999.0, ttq=2_100, ttv="0.00021C", avg_price=100.0)  # late arrival
    assert len(b.candles) == 1
    assert b.candles[0].high == 100.0  # the closed bar is untouched


def test_tick_without_price_is_discarded():
    b = CandleBuilder()
    b.ingest(1_000_000, "", ttq=1_000, ttv="0.0001C", avg_price=100.0)
    b.ingest(1_000_061, None, ttq=2_000, ttv="0.0002C", avg_price=100.0)
    assert b.candles == []


# --------------------------------------------------------------------------- resets


def test_counter_reset_drops_history_rather_than_emitting_a_giant_bar():
    b = CandleBuilder()
    t = 1_000_000
    for i in range(4):
        b.ingest(t + i * 61, 100.0, ttq=1_000 * (i + 1), ttv=f"{1_000 * (i + 1) * 100 / 1e7}C", avg_price=100.0)
    assert len(b.candles) == 3
    b.ingest(t + 400, 100.0, ttq=50, ttv="0.0000005C", avg_price=100.0)  # new session
    assert b.candles == []
    assert b.counter_resets == 1


# --------------------------------------------------------------------------- VWAP


def test_session_vwap_comes_from_avg_price_without_warmup():
    """Both counters run from the exchange's session open, so VWAP needs no history."""
    b = CandleBuilder()
    b.ingest(1_000_000, 61.2, ttq=505_558_040, ttv="4473.68C", avg_price=88.49)
    assert b.session_vwap == pytest.approx(88.49)
    assert b.vwap_unavailable_reason is None
    assert b.candles == []  # no completed bar yet, and none needed


def test_vwap_unavailable_without_avg_price():
    b = CandleBuilder()
    assert b.vwap_unavailable_reason == "no_ticks_yet"
    b.ingest(1_000_000, 100.0, ttq=1_000, ttv="0.0001C", avg_price="")
    assert b.session_vwap is None
    assert b.vwap_unavailable_reason == "avg_price_missing"


def test_vwap_cross_check_agrees_on_a_real_capture():
    tick = _tick("nifty_call_24000_raw.json")
    b = CandleBuilder()
    b.ingest(1_000_000, tick["last"], tick["ttq"], tick["ttv"], tick["avgPrice"])
    assert b.vwap_cross_check_diff == pytest.approx(0.0, abs=VWAP_CROSS_CHECK_TOLERANCE)


def test_vwap_cross_check_flags_a_unit_change():
    """If ttv stopped being crores, the run log must show it rather than the bot trusting it."""
    b = CandleBuilder()
    b.ingest(1_000_000, 61.2, ttq=505_558_040, ttv="4473.68L", avg_price=88.49)
    assert b.vwap_cross_check_diff is not None
    assert b.vwap_cross_check_diff > VWAP_CROSS_CHECK_TOLERANCE
    assert b.session_vwap == pytest.approx(88.49)  # avgPrice still wins


def test_unknown_ttv_suffix_disables_cross_check_but_not_vwap():
    b = CandleBuilder()
    b.ingest(1_000_000, 61.2, ttq=505_558_040, ttv="4473.68X", avg_price=88.49)
    assert b.vwap_cross_check_diff is None
    assert b.session_vwap == pytest.approx(88.49)


# --------------------------------------------------------------------------- indicators


def test_ema_seeds_from_sma_and_needs_a_full_period():
    assert ema_of([1, 2, 3], 4) is None
    assert ema_of([10, 10, 10], 3) == pytest.approx(10.0)
    # Seeded with SMA(3)=2, then one step toward 10 at multiplier 2/(3+1)=0.5.
    assert ema_of([1, 2, 3, 10], 3) == pytest.approx(6.0)


def test_volume_ma_requires_known_volumes_in_the_window():
    b = CandleBuilder()
    t = 1_000_000
    for i in range(4):
        b.ingest(t + i * 61, 100.0, ttq=1_000 * (i + 1), ttv=f"{1_000 * (i + 1) * 100 / 1e7}C", avg_price=100.0)
    # Bars: [None, 1000, 1000]
    assert b.volume_ma(3) is None       # window includes the unknown first bar
    assert b.volume_ma(2) == pytest.approx(1_000.0)


def test_is_warm_requires_ema_volume_ma_and_vwap():
    b = CandleBuilder()
    t = 1_000_000
    for i in range(6):
        b.ingest(t + i * 61, 100.0 + i, ttq=1_000 * (i + 1), ttv=f"{1_000 * (i + 1) * 100 / 1e7}C", avg_price=100.0)
    assert b.is_warm(ema_period=3, volume_ma_period=3)
    assert not b.is_warm(ema_period=99, volume_ma_period=3)

    cold = CandleBuilder()
    for i in range(6):
        cold.ingest(t + i * 61, 100.0 + i, ttq=1_000 * (i + 1), ttv="", avg_price="")
    assert not cold.is_warm(ema_period=3, volume_ma_period=3)
    status = cold.warmup_status(ema_period=3, volume_ma_period=3)
    assert status["warm"] is False
    assert status["vwap_unavailable_reason"] == "avg_price_missing"
