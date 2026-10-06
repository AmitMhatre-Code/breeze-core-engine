"""Momentum: close above EMA and session VWAP on a volume spike, on d-minute candles. Pure.

Bot 3's entry signal, lifted out of the bot and re-expressed on one-minute bars so it can be
replayed from ICICI history and read by any bot (docs/signals-streamline-plan.md section 2.2):

    candles  = d-minute candles built from one-minute bars, aligned to 09:15
    trend    = last completed candle's close vs EMA(9) of today's candle closes
    value    = the same close vs today's session VWAP
    volume   = the candle's traded quantity ranked against the trailing 20 candles
    call     = bullish when close > EMA and close > VWAP and volume ranks in the top fifth;
               bearish is the mirror; a call stands for one candle and extends while it re-fires

Three versions run side by side (guide/technical/design-decisions.md #72), all through this one
evaluator and told apart only by their `MomentumParams`, so a version can never drift by editing
shared code without its parameters saying so:

    v1  the trend line rebuilt from today's closes every session (`carry_ema=False`)
    v2  the trend line carried overnight, shifted by the gap (below)
    v3  v2, plus two tests from an outside review (2026-10-06):
        * volume judged for the duration it serves -- 1m: the candle must out-trade each of the
          session's last three candles (a burst); 5m/15m: it must rank in the top fifth of the
          same clock slot over the last ten sessions, which takes the U-shaped intraday volume
          curve out of the comparison (a midday candle is no longer ranked against the open)
        * a minimum move: the close must clear the trend line by at least half an ATR(14) of
          the series' own candles, so a close a few paise past the line is not a call

The 1m burst baseline restarts every session, so the open is never ranked against yesterday's
last minutes -- the false-positive-at-the-open the review was about. The slot baseline needs
eight of its ten sessions; with fewer it is still warming up. ATR is a size, like volume, so it
carries across sessions; the session's first candle's true range is its own high - low, so the
overnight gap never inflates it.

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
    REASON_MOVE_TOO_SMALL,
    REASON_NO_CONFLUENCE,
    REASON_VOLUME_LOW,
    REASON_VOLUME_UNKNOWN,
    REASON_VWAP_UNKNOWN,
    REASON_WARMING_UP,
)

#: How a candle's volume is judged. `rolling`: top fifth of the trailing `volume_lookback`
#: candles, carried across sessions (v1, v2). `burst`: more than each of the session's last
#: `burst_lookback` candles (v3 1m). `slot`: top fifth of the same clock slot over the last
#: `slot_sessions` sessions (v3 5m/15m).
VOLUME_TESTS = ("rolling", "burst", "slot")


@dataclass(frozen=True)
class MomentumParams:
    candle_minutes: int = 1
    ema_period: int = 9
    #: Completed candles the current one's volume is ranked against. Carried across sessions.
    volume_lookback: int = 20
    volume_percentile: float = 0.80
    #: v2 onward: the trend line starts each session where yesterday's ended, moved by the gap.
    #: v1 rebuilt it from today's closes and so read nothing for the first nine candles.
    carry_ema: bool = True
    volume_test: str = "rolling"
    burst_lookback: int = 3
    slot_sessions: int = 10
    #: Below this many past sessions for a slot, the slot test has no baseline yet.
    slot_min_sessions: int = 8
    #: None: no minimum move (v1, v2). Otherwise the close must clear the trend line by
    #: `atr_fraction` x ATR(`atr_period`) of the series' own candles.
    atr_period: Optional[int] = None
    atr_fraction: float = 0.5

    def __post_init__(self) -> None:
        if self.candle_minutes < 1:
            raise ValueError("candle_minutes must be at least 1")
        if not 0.0 < self.volume_percentile < 1.0:
            raise ValueError("volume_percentile must be strictly between 0 and 1")
        if self.volume_test not in VOLUME_TESTS:
            raise ValueError(f"volume_test must be one of {VOLUME_TESTS}")
        if self.burst_lookback < 1:
            raise ValueError("burst_lookback must be at least 1")
        if not 1 <= self.slot_min_sessions <= self.slot_sessions:
            raise ValueError("slot_min_sessions must be between 1 and slot_sessions")
        if self.atr_period is not None and self.atr_period < 1:
            raise ValueError("atr_period must be at least 1")
        if self.atr_fraction < 0:
            raise ValueError("atr_fraction cannot be negative")


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
        # The same average without the 09:00-09:15 pre-open -- recorded beside every reading so
        # whether the pre-open matters can be measured, never used to decide (#72).
        self._pv_session = 0.0
        self._v_session = 0.0
        self._cur: Optional[Candle] = None
        self._cur_index: Optional[int] = None
        self._volumes: Deque[float] = deque(maxlen=params.volume_lookback)
        #: `burst`: today's last few candle volumes. Restarts each session.
        self._burst: Deque[float] = deque(maxlen=params.burst_lookback)
        #: `slot`: candle index within the session -> that slot's volume in recent sessions.
        self._slots: dict[int, Deque[float]] = {}
        #: Today's running EMA once it has a seed from the previous session. None means there is
        #: no seed and the nine-candle SMA-seeded EMA over today's closes applies instead.
        self._ema: Optional[float] = None
        #: (the previous session's final EMA, its final candle close), for the gap shift.
        self._carry: Optional[tuple[float, float]] = None
        #: Wilder ATR over candle true ranges; None until `atr_period` candles exist.
        self._atr: Optional[float] = None
        self._atr_seed: list[float] = []
        #: The previous candle's close *this session*, for the true range. None at the open.
        self._tr_prev_close: Optional[float] = None

    # -- session state ---------------------------------------------------------------------

    def _roll_day(self, day: datetime.date) -> None:
        if day == self._day:
            return
        # Keep where the trend line ended, to be carried into the new session shifted by the
        # overnight gap -- see the module docstring. VWAP is not carried: it is an average of
        # one session's trading and means nothing across two.
        if self._closes and self.params.carry_ema:
            last = self._ema if self._ema is not None else ema_of(self._closes, self.params.ema_period)
            if last is not None:
                self._carry = (last, self._closes[-1])
        self._day = day
        self._closes = []
        self._pv = 0.0
        self._v = 0.0
        self._pv_session = 0.0
        self._v_session = 0.0
        self._cur = None
        self._cur_index = None
        self._ema = None
        self._burst.clear()
        self._tr_prev_close = None

    @property
    def session_vwap(self) -> Optional[float]:
        return self._pv / self._v if self._v > 0 else None

    @property
    def session_vwap_ex_preopen(self) -> Optional[float]:
        return self._pv_session / self._v_session if self._v_session > 0 else None

    def _candle_index(self, bar: Bar) -> int:
        start = bars_mod.session_start_ts(bars_mod.trading_date(bar.ts))
        return int((bar.ts - start) // (60 * self.params.candle_minutes))

    # -- feeding ---------------------------------------------------------------------------

    def on_bar(self, bar: Bar) -> Optional[Evaluation]:
        self._roll_day(bars_mod.trading_date(bar.ts))
        session = bars_mod.in_session(bar)
        if bar.volume is not None and bar.volume > 0 and (bars_mod.is_pre_open(bar) or session):
            self._pv += bar.ohlc4 * bar.volume
            self._v += bar.volume
            if session:
                self._pv_session += bar.ohlc4 * bar.volume
                self._v_session += bar.volume
        if not session:
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
        no reading until nine candles exist -- the old behaviour, kept for a cold start and for
        v1, which never carries."""
        p = self.params
        if len(self._closes) == 1 and self._carry is not None:
            carried_ema, prev_close = self._carry
            self._ema = carried_ema + (candle.open - prev_close)
        if self._ema is None:
            return ema_of(self._closes, p.ema_period)
        k = 2.0 / (p.ema_period + 1)
        self._ema = (candle.close - self._ema) * k + self._ema
        return self._ema

    def _advance_atr(self, candle: Candle) -> Optional[float]:
        """Wilder's ATR of candle true ranges, seeded with their simple mean. A short candle's
        range still counts -- only its volume is incomplete."""
        n = self.params.atr_period
        if n is None:
            return None
        tr = candle.high - candle.low
        prev = self._tr_prev_close
        if prev is not None:
            tr = max(tr, abs(candle.high - prev), abs(candle.low - prev))
        self._tr_prev_close = candle.close
        if self._atr is None:
            self._atr_seed.append(tr)
            if len(self._atr_seed) < n:
                return None
            self._atr = sum(self._atr_seed) / n
            self._atr_seed = []
            return self._atr
        self._atr = (self._atr * (n - 1) + tr) / n
        return self._atr

    def _volume_baseline(self, candle: Candle, volume: Optional[float]) -> tuple[list[float], bool]:
        """(the volumes this candle is judged against, whether that baseline is complete), then
        records this candle's volume for the candles after it."""
        p = self.params
        if p.volume_test == "burst":
            prior = list(self._burst)
            if volume is not None:
                self._burst.append(volume)
            return prior, len(prior) >= p.burst_lookback
        if p.volume_test == "slot":
            start = bars_mod.session_start_ts(bars_mod.trading_date(candle.start))
            slot = int((candle.start - start) // (60 * p.candle_minutes))
            held = self._slots.get(slot)
            prior = list(held) if held is not None else []
            if volume is not None:
                if held is None:
                    held = self._slots[slot] = deque(maxlen=p.slot_sessions)
                held.append(volume)
            return prior, len(prior) >= p.slot_min_sessions
        prior = list(self._volumes)
        if volume is not None:
            self._volumes.append(volume)
        return prior, len(prior) >= p.volume_lookback

    def _complete(self, candle: Candle, *, short: bool) -> Evaluation:
        p = self.params
        volume = None if short or candle.bars < p.candle_minutes else candle.volume
        self._closes.append(candle.close)
        prior, baseline_ready = self._volume_baseline(candle, volume)

        ema = self._advance_ema(candle)
        atr = self._advance_atr(candle)
        vwap = self.session_vwap
        vwap_ex = self.session_vwap_ex_preopen
        rank = percentile_rank(prior, volume) if volume is not None and baseline_ready else None
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
            "vwap_ex_preopen": None if vwap_ex is None else round(vwap_ex, 4),
            "volume_test": p.volume_test,
            "volume_rank": None if rank is None else round(rank, 4),
            "candles_today": len(self._closes),
            "volume_baseline": len(prior),
        }
        if p.atr_period is not None:
            components["atr"] = None if atr is None else round(atr, 4)
            components["ema_distance_atr"] = (
                round((candle.close - ema) / atr, 4) if ema is not None and atr else None
            )
        if ema is None or not baseline_ready or (p.atr_period is not None and atr is None):
            return Evaluation(None, None, components, REASON_WARMING_UP)
        if volume is None:
            return Evaluation(None, None, components, REASON_VOLUME_UNKNOWN)
        if vwap is None:
            return Evaluation(None, None, components, REASON_VWAP_UNKNOWN)

        trend = 1.0 if candle.close > ema else -1.0 if candle.close < ema else 0.0
        strength = trend * float(rank)
        components["strength"] = round(strength, 4)
        # A burst must out-trade every one of its few predecessors; the other tests take the
        # top fifth of a longer baseline.
        loud = rank >= 1.0 if p.volume_test == "burst" else rank >= p.volume_percentile
        if not loud:
            return Evaluation(None, strength, components, REASON_VOLUME_LOW)
        if candle.close > ema and candle.close > vwap:
            side = "bullish"
        elif candle.close < ema and candle.close < vwap:
            side = "bearish"
        else:
            return Evaluation(None, strength, components, REASON_NO_CONFLUENCE)
        if atr is not None and abs(candle.close - ema) < p.atr_fraction * atr:
            return Evaluation(None, strength, components, REASON_MOVE_TOO_SMALL)
        return Evaluation(side, strength, components, None)
