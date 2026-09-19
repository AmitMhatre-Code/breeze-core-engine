"""One-minute bars: the only input any signal may read (docs/signals-streamline-plan.md section 2).

A bar is exactly what ICICI's `get_historical_data_v2` serves for a futures contract: the minute's
start, OHLC, traded quantity and open interest. Nothing else -- no depth, no bid/ask, no tick
`avgPrice`. That restriction is what makes every live reading reproducible from history once the
session is over: the live path builds these bars from ticks, the replay reads them from the cache,
and both hand them to the same engine.

Pure: no I/O and no clock of its own. `LiveBarBuilder` is fed ticks with their arrival time.

Bar times
---------
`ts` is the minute's START in epoch seconds, as ICICI labels its bars (verified 2026-09-15). A
bar is known only once it has *closed*, at `ts + 60`, and every reading is taken at that moment
in both paths -- a reading timed at the bar's start would give a call one minute more life in a
replay than the live publisher could ever see.

Unknown is never zero
---------------------
`volume` and `oi` are None when unknown. The first live bar after a (re)start has no counter
baseline, so its volume is unknown; ICICI serves OI 0 on pre-open bars and on every BSE bar, a
zero that means absent. Engines treat None as "no reading", never as a low number.

Quiet minutes
-------------
ICICI serves a minute with no trade as a flat bar with volume 0, not as a missing bar. A thin
contract (SENSEX futures trade in about half the minutes) can go a whole minute without a tick,
so the live builder writes the same flat bar for a skipped minute -- but only across a short hole
(`MAX_SYNTH_GAP_SECONDS`). A longer silence is a feed problem, and inventing flat bars across it
would hide the gap the engines refuse to read across.
"""
from __future__ import annotations

import datetime
import math
from dataclasses import dataclass
from typing import Any, Optional

from icici_breeze_backend.app.core.timezone import IST

BAR_SECONDS = 60

# The continuous session a reading may come from. Bars from 09:00 are pre-open (they feed the
# session VWAP only); from 15:15 the closing auction runs (#CAS), which is not continuous
# trading. The last reading is taken as the 15:14 bar closes.
PRE_OPEN_START = datetime.time(9, 0)
SESSION_START = datetime.time(9, 15)
SESSION_END = datetime.time(15, 15)

# Flat bars are synthesised across a hole no longer than this; beyond it the gap is real.
MAX_SYNTH_GAP_SECONDS = 300.0


@dataclass(frozen=True)
class Bar:
    """One completed one-minute bar. Positional order (ts, close, volume, oi) is kept from the
    original expansion bar so older callers still construct it the same way."""

    ts: float  # epoch seconds, the minute's START
    close: float
    volume: Optional[float] = None
    oi: Optional[float] = None
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None

    @property
    def close_ts(self) -> float:
        return self.ts + BAR_SECONDS

    @property
    def ohlc4(self) -> float:
        o = self.open if self.open is not None else self.close
        h = self.high if self.high is not None else self.close
        low = self.low if self.low is not None else self.close
        return (o + h + low + self.close) / 4.0

    @property
    def moment(self) -> datetime.datetime:
        return datetime.datetime.fromtimestamp(self.ts, IST)


def ist(ts: float) -> datetime.datetime:
    return datetime.datetime.fromtimestamp(ts, IST)


def trading_date(ts: float) -> datetime.date:
    return ist(ts).date()


def minute_of(ts: float) -> datetime.time:
    return ist(ts).time()


def is_pre_open(bar: Bar) -> bool:
    t = minute_of(bar.ts)
    return PRE_OPEN_START <= t < SESSION_START


def in_session(bar: Bar) -> bool:
    """A bar a reading may be taken from: its start is inside 09:15-15:14."""
    t = minute_of(bar.ts)
    return SESSION_START <= t < SESSION_END


def reading_window_open(now: float) -> bool:
    """Whether `now` falls where a reading can exist: after the first session bar has closed
    and no later than the last one's close."""
    t = ist(now)
    first_close = datetime.datetime.combine(t.date(), SESSION_START, IST) + datetime.timedelta(
        seconds=BAR_SECONDS
    )
    last_close = datetime.datetime.combine(t.date(), SESSION_END, IST)
    return first_close <= t <= last_close + datetime.timedelta(seconds=BAR_SECONDS - 1)


def session_start_ts(day: datetime.date) -> float:
    return datetime.datetime.combine(day, SESSION_START, IST).timestamp()


def _positive(raw: Any) -> Optional[float]:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value > 0 else None


def _finite(raw: Any) -> Optional[float]:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def from_hist(candle: Any) -> Bar:
    """A stored history bar (`backtest_store.HistCandle`, naive IST) as a `Bar`.

    A negative volume (ICICI served -650 on a 2026-09-15 pre-open bar) is unknown, not a sale."""
    ts = candle.ts.replace(tzinfo=IST).timestamp() if candle.ts.tzinfo is None else candle.ts.timestamp()
    volume = _finite(candle.volume)
    return Bar(
        ts=ts,
        close=float(candle.close),
        volume=volume if volume is not None and volume >= 0 else None,
        oi=_positive(getattr(candle, "oi", None)),
        open=float(candle.open),
        high=float(candle.high),
        low=float(candle.low),
    )


def parse_quote(payload: Any) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """(last price, day-cumulative traded quantity, open interest) from an F&O quote tick."""
    if not isinstance(payload, dict):
        return None, None, None
    return _positive(payload.get("last")), _finite(payload.get("ttq")), _positive(payload.get("OI"))


@dataclass
class _Bucket:
    start: int
    open: float
    high: float
    low: float
    close: float
    ttq_last: Optional[float]
    oi_last: Optional[float]


class LiveBarBuilder:
    """Ticks in, completed one-minute bars out. Not thread-safe; the caller holds the lock.

    Bucketed on **arrival time**, never the tick's `ltt`: that field is a locale string from the
    SDK and a bare int from the mock (scalping-plan trap 5). Volume differences the cumulative
    `ttq` rather than summing `ltq`, so a dropped tick self-corrects on the next one.
    """

    def __init__(self) -> None:
        self._cur: Optional[_Bucket] = None
        self._prev_ttq: Optional[float] = None
        self._last_bar: Optional[Bar] = None
        self._last_tick_at: Optional[float] = None
        self._day: Optional[datetime.date] = None

    @staticmethod
    def _bucket(ts: float) -> int:
        return int(ts // BAR_SECONDS) * BAR_SECONDS

    @property
    def last_bar(self) -> Optional[Bar]:
        return self._last_bar

    @property
    def last_tick_at(self) -> Optional[float]:
        return self._last_tick_at

    def ingest(self, ts: float, payload: Any) -> list[Bar]:
        """Feed one tick. Returns every bar this tick completed (usually none, sometimes one,
        plus flat bars for quiet minutes it skipped over)."""
        last, ttq, oi = parse_quote(payload)
        if last is None:
            return []
        out: list[Bar] = []
        day = trading_date(ts)
        if self._day is not None and day != self._day:
            # The exchange's counters restart with the day: close yesterday, drop its baseline.
            if self._cur is not None:
                out.append(self._close(self._cur))
                self._cur = None
            self._prev_ttq = None
            self._last_bar = None
        self._day = day
        start = self._bucket(ts)
        if self._cur is not None and start < self._cur.start:
            return out  # an out-of-order tick for a minute already closed
        if self._cur is not None and start > self._cur.start:
            out.append(self._close(self._cur))
            self._cur = None
        out.extend(self._synth_until(start))
        self._last_tick_at = ts
        if self._cur is None:
            self._cur = _Bucket(start, last, last, last, last, ttq, oi)
        else:
            b = self._cur
            b.high = max(b.high, last)
            b.low = min(b.low, last)
            b.close = last
            if ttq is not None:
                b.ttq_last = ttq
            if oi is not None:
                b.oi_last = oi
        return out

    def flush(self, now: float) -> list[Bar]:
        """Close what the clock has left behind, even with no further tick: the open bar, then a
        flat bar for each quiet minute since, while the contract printed recently enough."""
        out: list[Bar] = []
        start = self._bucket(now)
        if self._cur is not None and start > self._cur.start:
            out.append(self._close(self._cur))
            self._cur = None
        if self._cur is None and self._day == trading_date(now):
            out.extend(self._synth_until(start))
        return out

    def _synth_until(self, start: int) -> list[Bar]:
        """Flat bars for every whole minute between the last bar and `start`, within the gap."""
        prev = self._last_bar
        if prev is None or self._last_tick_at is None:
            return []
        out: list[Bar] = []
        t = int(prev.ts) + BAR_SECONDS
        while t < start:
            if t + BAR_SECONDS - self._last_tick_at > MAX_SYNTH_GAP_SECONDS:
                break
            bar = Bar(ts=float(t), close=prev.close, volume=0.0, oi=prev.oi,
                      open=prev.close, high=prev.close, low=prev.close)
            out.append(bar)
            self._last_bar = prev = bar
            t += BAR_SECONDS
        return out

    def _close(self, b: _Bucket) -> Bar:
        volume: Optional[float] = None
        if b.ttq_last is not None:
            if self._prev_ttq is not None and b.ttq_last >= self._prev_ttq:
                volume = b.ttq_last - self._prev_ttq
            # A counter running backwards is a stale packet or the day rolling over: restart the
            # baseline rather than emit a negative bar.
            self._prev_ttq = b.ttq_last
        bar = Bar(ts=float(b.start), close=b.close, volume=volume, oi=b.oi_last,
                  open=b.open, high=b.high, low=b.low)
        self._last_bar = bar
        return bar
