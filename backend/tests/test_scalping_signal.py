"""The momentum entry signal (services.bots.scalping.signal)."""
from __future__ import annotations

import pytest

from icici_breeze_backend.app.domain.bots import MomentumSignalConfig
from icici_breeze_backend.app.services.bots.scalping.candles import Candle
from icici_breeze_backend.app.services.bots.scalping.signal import (
    evaluate_momentum,
    signal_run_unbroken,
)

CFG = MomentumSignalConfig(ema_period=3, volume_ma_period=3, volume_multiplier=1.5)


def _c(close, volume=1000, start=0):
    return Candle(
        start=start, open=close, high=close, low=close, close=close,
        volume=volume, turnover=None, ticks=1,
    )


def _rising(last_close, last_volume):
    """Three flat bars then one that decides it, so EMA/volume baselines are predictable."""
    return [_c(100.0, 1000, i * 60) for i in range(3)] + [_c(last_close, last_volume, 180)]


def test_no_signal_until_enough_candles():
    r = evaluate_momentum([_c(100.0)], None, CFG)
    assert r.side is None and r.reason == "not_enough_candles"
    assert r.values["candles_required"] == 3


def test_bullish_needs_close_above_ema_and_vwap_on_volume():
    r = evaluate_momentum(_rising(110.0, 5000), session_vwap=99.0, config=CFG)
    assert r.side == "bullish" and r.reason == "confluence"
    assert r.right == "call"
    assert r.values["volume_threshold"] == pytest.approx(
        (1000 + 1000 + 5000) / 3 * 1.5
    )


def test_bearish_buys_a_put_never_sells():
    r = evaluate_momentum(_rising(90.0, 5000), session_vwap=101.0, config=CFG)
    assert r.side == "bearish"
    assert r.right == "put"  # long-only: a bearish view is expressed by buying a put


def test_volume_alone_is_not_a_signal():
    """Close above the EMA but below VWAP is not confluence."""
    r = evaluate_momentum(_rising(110.0, 5000), session_vwap=120.0, config=CFG)
    assert r.side is None and r.reason == "no_confluence"


def test_confluence_without_volume_is_not_a_signal():
    r = evaluate_momentum(_rising(110.0, 1000), session_vwap=99.0, config=CFG)
    assert r.side is None and r.reason == "volume_below_threshold"


def test_volume_exactly_at_the_threshold_does_not_fire():
    """`>` not `>=`: a surge has to actually exceed the average."""
    candles = [_c(100.0, 1000, i * 60) for i in range(3)] + [_c(110.0, 1000, 180)]
    r = evaluate_momentum(candles, session_vwap=99.0, config=CFG)
    assert r.side is None and r.reason == "volume_below_threshold"


def test_an_unknown_volume_bar_blocks_the_signal():
    """A feed gap must not manufacture a surge by dragging the mean down.

    `Candle.volume is None` means unknown, not zero -- see the candles module.
    """
    candles = [_c(100.0, 1000, 0), _c(100.0, None, 60), _c(100.0, 1000, 120), _c(110.0, 9999, 180)]
    r = evaluate_momentum(candles, session_vwap=99.0, config=CFG)
    assert r.side is None and r.reason == "volume_unavailable"


def test_missing_vwap_blocks_the_signal_when_required():
    r = evaluate_momentum(_rising(110.0, 5000), session_vwap=None, config=CFG)
    assert r.side is None and r.reason == "vwap_unavailable"


def test_vwap_can_be_switched_off():
    cfg = MomentumSignalConfig(ema_period=3, volume_ma_period=3, require_vwap=False)
    r = evaluate_momentum(_rising(110.0, 5000), session_vwap=None, config=cfg)
    assert r.side == "bullish"


def test_values_are_reported_even_when_nothing_fires():
    """An unexplained no-trade day is the failure the run log exists to prevent."""
    r = evaluate_momentum(_rising(110.0, 1000), session_vwap=99.0, config=CFG)
    assert not r.fired
    assert set(r.values) >= {"close", "ema", "session_vwap", "volume", "volume_ma", "volume_threshold"}


# --- the fresh-signal rule: one trade per signal run ----------------------------------


def _cv(close, volume, start, vwap):
    return Candle(
        start=start, open=close, high=close, low=close, close=close,
        volume=volume, turnover=None, ticks=1, vwap=vwap,
    )


# Three flat bars, then a bullish bar at 180 -- the candle the last trade was opened on.
_ENTRY = [_cv(100.0, 1000, i * 60, 99.0) for i in range(3)] + [_cv(110.0, 5000, 180, 99.0)]


def _still_running(candles, side="bullish"):
    return signal_run_unbroken(candles, CFG, entry_candle_start=180, side=side)


def test_the_run_continues_while_every_later_candle_fires_the_same_side():
    """The 10-11 Sep leak: a stopped-out long re-bought two seconds later on the same run."""
    assert _still_running(_ENTRY)  # nothing after the entry candle yet
    assert _still_running(_ENTRY + [_cv(115.0, 9000, 240, 100.0)])


def test_a_low_volume_candle_ends_the_run_and_the_next_firing_is_fresh():
    candles = _ENTRY + [_cv(112.0, 1000, 240, 100.0)]
    assert not _still_running(candles)
    # Fires again after lapsing: a new run, even though it lapsed while the trade was held.
    assert not _still_running(candles + [_cv(125.0, 20000, 300, 100.0)])


def test_the_opposite_side_ends_the_run():
    assert not _still_running(_ENTRY + [_cv(90.0, 9000, 240, 101.0)])


def test_an_unknown_reading_does_not_end_the_run():
    """Low volume, but VWAP unknown: the verdict is a data gap, not evidence the signal lapsed."""
    assert _still_running(_ENTRY + [_cv(112.0, 1000, 240, None)])


def test_each_candle_is_replayed_with_its_own_vwap():
    """Against its own VWAP the bar fired; against a later, higher one it would read as off."""
    assert _still_running(_ENTRY + [_cv(115.0, 9000, 240, 100.0)])
    assert not _still_running(_ENTRY + [_cv(115.0, 9000, 240, 120.0)])


def test_after_a_restart_the_run_holds_until_an_off_candle_is_seen():
    """The entry candle is gone with the old process; a rebuilt history that only fires holds."""
    rebuilt = [_cv(100.0, 1000, 600, 99.0), _cv(100.0, 1000, 660, 99.0), _cv(110.0, 5000, 720, 99.0)]
    assert _still_running(rebuilt)
    assert not _still_running(rebuilt + [_cv(108.0, 1000, 780, 99.0)])
