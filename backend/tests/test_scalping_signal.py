"""The momentum entry signal (services.bots.scalping.signal)."""
from __future__ import annotations

import pytest

from icici_breeze_backend.app.domain.bots import MomentumSignalConfig
from icici_breeze_backend.app.services.bots.scalping.candles import Candle
from icici_breeze_backend.app.services.bots.scalping.signal import evaluate_momentum

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
