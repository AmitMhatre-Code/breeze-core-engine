"""Momentum: close above EMA and session VWAP on a volume spike, on d-minute candles. Pure.

Bot 3's entry signal, lifted out of the bot and re-expressed on one-minute bars so it can be
replayed from ICICI history and read by any bot (docs/signals-streamline-plan.md section 2.2):

    candles  = d-minute candles built from one-minute bars, aligned to 09:15
    trend    = last completed candle's close vs EMA(9) of today's candle closes
    value    = the same close vs today's session VWAP
    volume   = the candle's traded quantity ranked against the trailing 20 candles
    call     = bullish when close > EMA and close > VWAP and volume ranks in the top fifth;
               bearish is the mirror; a call stands for one candle and extends while it re-fires

VWAP resets every session; the trend line is carried over, shifted by the gap
------------------------------------------------------------------------------
The rule this replaces was simpler: EMA and VWAP are price *levels*, so both were rebuilt from
today's bars only, and an overnight gap could never read as a breakout at the open (decision 15).
VWAP still works that way -- it is an average of today's trading and means nothing else.

The EMA could not. EMA(9) on d-minute candles needs nine completed candles, so rebuilt daily it
said nothing until 09:15 + 9d: 09:24 at one minute, 10:00 at five and **11:30 at fifteen**, every
single day. Measured over 117 sessions (2026-04-01 to 09-18) the fifteen-minute series produced
its first reading at 11:30 on every one of them, its first call at 13:00, and no call at all on
101 of the 117 -- and fifteen minutes is the reading the navbar shows. A third of the session was
not "warming up" in any recoverable sense; it was a rule the signal could not satisfy.

So the line now starts where yesterday's ended, **moved by the overnight gap**:

    seed = yesterday's final EMA + (today's first candle open - yesterday's final candle close)

which keeps the property decision 15 actually existed to protect. A flat open puts the first
candle exactly on the line, not above it, so the gap alone can never fire a call -- only trading
away from the gap-adjusted line can. The seed then decays as any EMA does: after nine candles it
carries 13% of the weight, after eighteen under 2%, so by mid-morning the line is today's.

Replay parity survives it because of that decay. A live engine warmed on two sessions and a
replay warmed on a week disagree only through a seed that both have already decayed away by an
order of magnitude before the range begins. With no previous session at all -- a cold cache --
there is no seed and the old nine-candle wait applies, which is the fail-closed answer.

The volume ranking compares *sizes*, not levels, so it keeps the trailing candles across the
night as it always did, which is what lets the first candle of the day be ranked at all.

Why VWAP is rebuilt from bars, not read from the tick
-----------------------------------------------------
The live tick carries the exchange's own `avgPrice`, which history does not. A signal that read
it could not be replayed, so live and replay both price each bar's volume at its OHLC average,
pre-open included (`bots/scalping/backtest_common.SessionVwap`). Measured against `avgPrice`
over 122 live candles it reads 0.98 pts off on average and never put a close on the other side.

Why a percentile and not "1.5x the mean"
----------------------------------------
Bar volume is right-skewed, so the mean sits above most bars and 1.5x it landed past the 95th
percentile: it fired on 4.9% of bars and its full condition never co-occurred across a logged
session (#33). Ranking says what it means -- the top fifth, whatever the day's absolute volume.
"""
from __future__ import annotations

import datetime
from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, Optional

from icici_breeze_backend.app.services.index_signal import bars as bars_mod
from icici_breeze_backend.app.services.index_signal.bars import Bar
from icici_breeze_backend.app.services.index_signal.states import (
    Evaluation,
    REASON_NO_CONFLUENCE,
    REASON_VOLUME_LOW,
    REASON_VOLUME_UNKNOWN,
    REASON_VWAP_UNKNOWN,
    REASON_WARMING_UP,
)


@dataclass(frozen=True)
class MomentumParams:
    candle_minutes: int = 1
    ema_period: int = 9
    #: Completed candles the current one's volume is ranked against. Carried across sessions.
    volume_lookback: int = 20
    volume_percentile: float = 0.80

    def __post_init__(self) -> None:
        if self.candle_minutes < 1:
            raise ValueError("candle_minutes must be at least 1")
        if not 0.0 < self.volume_percentile < 1.0:
            raise ValueError("volume_percentile must be strictly between 0 and 1")


@dataclass
class Candle:
    start: float
    open: float
    high: float
    low: float
    close: float
    volume: Optional[float]
    bars: int


def ema_of(values: list[float], period: int) -> Optional[float]:
    """SMA-seeded EMA, identical to the scalper's so the 1-minute reading is Bot 3's signal."""
    if period <= 0 or len(values) < period:
        return None
    ema = sum(values[:period]) / float(period)
    k = 2.0 / (period + 1)
    for value in values[period:]:
        ema = (value - ema) * k + ema
    return ema


def percentile_rank(values: list[float], reading: float) -> Optional[float]:
    """Share of `values` strictly below `reading`. A flat market ranks 0 and cannot fire."""
    if not values:
        return None
    return sum(1 for v in values if v < reading) / len(values)


class MomentumEvaluator:
    """Feeds on one-minute bars; returns an `Evaluation` each time a candle completes."""

    def __init__(self, params: MomentumParams) -> None:
        self.params = params
        self._day: Optional[datetime.date] = None
        self._closes: list[float] = []  # today's completed candle closes
        self._pv = 0.0
        self._v = 0.0
        self._cur: Optional[Candle] = None
        self._cur_index: Optional[int] = None
        self._volumes: Deque[float] = deque(maxlen=params.volume_lookback)
        #: Today's running EMA once it has a seed from the previous session. None means there is
        #: no seed and the nine-candle SMA-seeded EMA over today's closes applies instead.
        self._ema: Optional[float] = None
        #: (the previous session's final EMA, its final candle close), for the gap shift.
        self._carry: Optional[tuple[float, float]] = None

    # -- session state ---------------------------------------------------------------------

    def _roll_day(self, day: datetime.date) -> None:
        if day == self._day:
            return
        # Keep where the trend line ended, to be carried into the new session shifted by the
        # overnight gap -- see the module docstring. VWAP is not carried: it is an average of
        # one session's trading and means nothing across two.
        if self._closes:
            last = self._ema if self._ema is not None else ema_of(self._closes, self.params.ema_period)
            if last is not None:
                self._carry = (last, self._closes[-1])
        self._day = day
        self._closes = []
        self._pv = 0.0
        self._v = 0.0
        self._cur = None
        self._cur_index = None
        self._ema = None

    @property
    def session_vwap(self) -> Optional[float]:
        return self._pv / self._v if self._v > 0 else None

    def _candle_index(self, bar: Bar) -> int:
        start = bars_mod.session_start_ts(bars_mod.trading_date(bar.ts))
        return int((bar.ts - start) // (60 * self.params.candle_minutes))

    # -- feeding ---------------------------------------------------------------------------

    def on_bar(self, bar: Bar) -> Optional[Evaluation]:
        self._roll_day(bars_mod.trading_date(bar.ts))
        if bar.volume is not None and bar.volume > 0 and (bars_mod.is_pre_open(bar) or bars_mod.in_session(bar)):
            self._pv += bar.ohlc4 * bar.volume
            self._v += bar.volume
        if not bars_mod.in_session(bar):
            return None

        d = self.params.candle_minutes
        idx = self._candle_index(bar)
        result: Optional[Evaluation] = None
        if self._cur is not None and idx != self._cur_index:
            # A bar from a later candle arrived before this one's last minute: the candle is
            # closed short. Its volume is incomplete, so it cannot be ranked.
            result = self._complete(self._cur, short=True)
            self._cur = None
        if self._cur is None:
            self._cur_index = idx
            self._cur = Candle(bar.ts, bar.open or bar.close, bar.high or bar.close,
                               bar.low or bar.close, bar.close, bar.volume, 1)
        else:
            c = self._cur
            c.high = max(c.high, bar.high if bar.high is not None else bar.close)
            c.low = min(c.low, bar.low if bar.low is not None else bar.close)
            c.close = bar.close
            c.volume = None if c.volume is None or bar.volume is None else c.volume + bar.volume
            c.bars += 1
        start = bars_mod.session_start_ts(bars_mod.trading_date(bar.ts))
        candle_end = start + (idx + 1) * 60 * d
        if bar.close_ts >= candle_end:
            result = self._complete(self._cur, short=False)
            self._cur = None
        return result

    def _advance_ema(self, candle: Candle) -> Optional[float]:
        """Today's trend line after this candle.

        On the session's first candle the previous session's line is carried in, moved by the
        overnight gap, so a gap can never by itself put a close on one side of the line. With no
        previous session there is no seed and the nine-candle SMA-seeded EMA applies, which means
        no reading until nine candles exist -- the old behaviour, kept for a cold start."""
        p = self.params
        if len(self._closes) == 1 and self._carry is not None:
            carried_ema, prev_close = self._carry
            self._ema = carried_ema + (candle.open - prev_close)
        if self._ema is None:
            return ema_of(self._closes, p.ema_period)
        k = 2.0 / (p.ema_period + 1)
        self._ema = (candle.close - self._ema) * k + self._ema
        return self._ema

    def _complete(self, candle: Candle, *, short: bool) -> Evaluation:
        p = self.params
        volume = None if short or candle.bars < p.candle_minutes else candle.volume
        prior = list(self._volumes)
        self._closes.append(candle.close)
        if volume is not None:
            self._volumes.append(volume)

        ema = self._advance_ema(candle)
        vwap = self.session_vwap
        rank = percentile_rank(prior, volume) if volume is not None and len(prior) >= p.volume_lookback else None
        components: dict[str, Any] = {
            "candle_start": candle.start,
            "candle_minutes": p.candle_minutes,
            "open": candle.open,
            "high": candle.high,
            "low": candle.low,
            "close": candle.close,
            "volume": volume,
            "ema": None if ema is None else round(ema, 4),
            "vwap": None if vwap is None else round(vwap, 4),
            "volume_rank": None if rank is None else round(rank, 4),
            "candles_today": len(self._closes),
            "volume_baseline": len(prior),
        }
        if ema is None or len(prior) < p.volume_lookback:
            return Evaluation(None, None, components, REASON_WARMING_UP)
        if volume is None:
            return Evaluation(None, None, components, REASON_VOLUME_UNKNOWN)
        if vwap is None:
            return Evaluation(None, None, components, REASON_VWAP_UNKNOWN)

        trend = 1.0 if candle.close > ema else -1.0 if candle.close < ema else 0.0
        strength = trend * float(rank)
        components["strength"] = round(strength, 4)
        if rank < p.volume_percentile:
            return Evaluation(None, strength, components, REASON_VOLUME_LOW)
        if candle.close > ema and candle.close > vwap:
            return Evaluation("bullish", strength, components, None)
        if candle.close < ema and candle.close < vwap:
            return Evaluation("bearish", strength, components, None)
        return Evaluation(None, strength, components, REASON_NO_CONFLUENCE)
