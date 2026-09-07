"""The momentum entry signal (docs/bots-scalping-plan.md section 3.2).

Pure. Given completed candles, a session VWAP and the config, it returns a side or None
along with every number it used -- the numbers matter as much as the verdict, because an
unexplained no-trade day is the failure the run log exists to prevent, and "no signal" is
the most common thing a scalper does all session.

Evaluated on the **last completed candle**, never the bar in progress. A signal read off a
partial bar changes as the bar fills and would fire, unfire and re-fire within the same
minute against an EMA that has not moved.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional, Sequence

from icici_breeze_backend.app.domain.bots import MomentumSignalConfig
from icici_breeze_backend.app.services.bots.scalping.candles import Candle, ema_of

SignalSide = Literal["bullish", "bearish"]

# A bullish signal buys the ATM call, a bearish one the ATM put -- the bot is long-only, so
# "bearish" is a view expressed by buying a put, never by selling anything.
SIDE_TO_RIGHT: dict[str, str] = {"bullish": "call", "bearish": "put"}


@dataclass(frozen=True)
class SignalResult:
    side: Optional[SignalSide]
    reason: str
    values: dict[str, Any] = field(default_factory=dict)

    @property
    def fired(self) -> bool:
        return self.side is not None

    @property
    def right(self) -> Optional[str]:
        return SIDE_TO_RIGHT.get(self.side or "")


def evaluate_momentum(
    candles: Sequence[Candle],
    session_vwap: Optional[float],
    config: MomentumSignalConfig,
) -> SignalResult:
    """Bullish when the last close is above both the EMA and VWAP on above-average volume.

    All three conditions are required in the same direction. The volume filter is what keeps
    this from firing on a drifting close in a dead market, and it is also the reason the
    signal runs on futures rather than the index -- an index has no volume to compare.
    """
    completed = list(candles)
    required = max(config.ema_period, config.volume_ma_period)
    if len(completed) < required:
        return SignalResult(
            None,
            "not_enough_candles",
            {"candles": len(completed), "candles_required": required},
        )

    last = completed[-1]
    ema = ema_of([c.close for c in completed], config.ema_period)
    if ema is None:
        return SignalResult(None, "ema_unavailable", {"candles": len(completed)})

    window = [c.volume for c in completed[-config.volume_ma_period :]]
    if any(v is None for v in window):
        # An unknown bar is not a zero-volume bar -- see `candles.Candle.volume`. Refusing
        # here is what stops a feed gap from manufacturing a volume surge out of a low mean.
        return SignalResult(None, "volume_unavailable", {"candles": len(completed)})
    volume_ma = sum(int(v) for v in window if v is not None) / float(config.volume_ma_period)

    if config.require_vwap and session_vwap is None:
        return SignalResult(None, "vwap_unavailable", {"candles": len(completed)})

    threshold = volume_ma * config.volume_multiplier
    last_volume = last.volume
    values: dict[str, Any] = {
        "close": last.close,
        "ema": round(ema, 4),
        "session_vwap": round(session_vwap, 4) if session_vwap is not None else None,
        "volume": last_volume,
        "volume_ma": round(volume_ma, 2),
        "volume_threshold": round(threshold, 2),
        "candle_start": last.start,
    }

    if last_volume is None or last_volume <= threshold:
        return SignalResult(None, "volume_below_threshold", values)

    above_vwap = session_vwap is None or last.close > session_vwap
    below_vwap = session_vwap is None or last.close < session_vwap
    if last.close > ema and above_vwap:
        return SignalResult("bullish", "confluence", values)
    if last.close < ema and below_vwap:
        return SignalResult("bearish", "confluence", values)
    return SignalResult(None, "no_confluence", values)
