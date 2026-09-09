"""1-minute OHLCV candles and indicators built from raw ICICI F&O ticks.

Pure: no I/O, no clock of its own, no broker. Everything here is a function of the samples
it is handed, which is what makes the whole signal path testable without a session --
`futures_feed` is the only part that needs a live feed (docs/bots-scalping-plan.md, 5.1).

Volume: difference the cumulative counter, never sum ltq
-------------------------------------------------------
An NSE F&O tick carries `ttq` (day-cumulative traded quantity) alongside `ltq` (last traded
quantity). Per-candle volume is taken by **differencing `ttq`**, because summing `ltq`
permanently understates a bar whenever a tick is dropped -- and the pipeline drops on a full
queue by design (`ws_tick_pipeline._dropped_ticks`). Differencing self-corrects: the next
tick carries the whole day's total, so a gap costs nothing past the boundary it straddles.

VWAP: `avgPrice`, cross-checked against `ttv`/`ttq`
--------------------------------------------------
Verified against the real captures in `tests/fixtures/icici_ticks/` (2026-09-06):

    nifty_call_24000:  ttv 4473.68C, ttq 505558040  ->  88.48994   avgPrice 88.49
    nifty_call_25000:  ttv   10.08C, ttq  77532130  ->   1.30011   avgPrice 1.3

`ttv` is a **string in crores with a unit suffix**, and `ttv x 1e7 / ttq` is exactly the
tick's own `avgPrice` -- i.e. the day's turnover-weighted average price, which for a
contract traded only in this session *is* the session VWAP. Both readings are cumulative
from the exchange's session open, so VWAP needs no history and no warm-up; only the EMA and
the volume MA do.

`avgPrice` is the primary source: it is a plain float in every capture, while `ttv` carries a
unit suffix whose full vocabulary is unknown ("C" is the only one observed). Making a string
suffix load-bearing would mean an unrecognised unit leaves the bot unable to compute VWAP and
therefore refusing to trade for a whole session. `ttv`/`ttq` is still parsed and compared, so
a broker-side change to either is caught and logged rather than silently trusted.

Bucketing uses **arrival time, not `ltt`**. Confirmed from the same captures: `ltt` arrives
as `"Mon Jun 29 09:59:59 2026"` -- the SDK's `datetime.fromtimestamp(...).strftime('%c')`,
a locale-formatted local-time string -- while `MockBreezeSdk` emits a bare int. Two types for
one field, neither reliable. WebSocket latency is far below a one-minute bar, so arrival time
is accurate for this purpose and is the only reading available in both environments.

Empty strings are real: the same captures carry `"CHNGOI": ""`. Every numeric read here goes
through `coerce_float`, which treats '' as absent rather than zero.
"""
from __future__ import annotations

import datetime
import logging
from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, Iterable, Optional

from icici_breeze_backend.app.core.timezone import IST

_logger = logging.getLogger(__name__)

BUCKET_SECONDS = 60

# Enough for a 20-period volume MA plus recent bars for the run log, and cheap to hold: a
# full session is 375 bars.
_MAX_CANDLES = 512

# Relative gap between `avgPrice` and `ttv`/`ttq` beyond which the two are reported as
# disagreeing. Generous, because the two are computed at slightly different instants inside
# one payload; the check is meant to catch a *unit* or semantic change, not rounding.
VWAP_CROSS_CHECK_TOLERANCE = 0.005

# Observed on real ticks: "4473.68C" == 4473.68 crore. The other entries are inferred from
# the same convention and are why an unknown suffix disables the cross-check rather than the
# bot -- see the module docstring.
_TTV_UNIT_MULTIPLIERS = {"": 1.0, "C": 1e7, "L": 1e5, "K": 1e3}


@dataclass(frozen=True)
class Candle:
    """One completed bar. `volume`/`turnover` are None when no baseline existed.

    A None volume is *unknown*, not zero -- it happens on the first bar after a connect or a
    counter reset, where there is no earlier cumulative reading to difference against.
    Counting it as zero would drag the volume MA down and suppress the volume filter exactly
    when the feed has just come up.
    """

    start: int  # epoch seconds, aligned to BUCKET_SECONDS
    open: float
    high: float
    low: float
    close: float
    volume: Optional[int]
    turnover: Optional[float]
    ticks: int

    @property
    def typical_price(self) -> float:
        return (self.high + self.low + self.close) / 3.0


def coerce_float(raw: Any) -> Optional[float]:
    """ICICI sends numbers as strings and absent fields as '' rather than null."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)
    text = str(raw).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_ttv(raw: Any) -> Optional[float]:
    """Total traded value in rupees from ICICI's suffixed string, or None.

    Returns None for an unrecognised suffix rather than guessing a multiplier: this feeds a
    cross-check, and a wrong unit would raise a false alarm on every tick.
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)
    text = str(raw).strip().upper()
    if not text:
        return None
    suffix = ""
    if text and text[-1].isalpha():
        suffix = text[-1]
        text = text[:-1].strip()
    multiplier = _TTV_UNIT_MULTIPLIERS.get(suffix)
    if multiplier is None:
        return None
    try:
        return float(text) * multiplier
    except ValueError:
        return None


def bucket_start(ts: float) -> int:
    return int(ts // BUCKET_SECONDS) * BUCKET_SECONDS


def trading_date_of(ts: float) -> datetime.date:
    """The IST calendar date an epoch timestamp falls on.

    The builder's session boundary. Derived from the tick's own timestamp rather than
    `now_ist()` so a replayed or back-dated tick is filed against the day it belongs to.
    """
    return datetime.datetime.fromtimestamp(ts, tz=IST).date()


class CandleBuilder:
    """Accumulates ticks for ONE contract into 1-minute candles.

    Not thread-safe by itself; `futures_feed` owns the lock, because the SDK's tick callback
    and the bot's decision loop touch this from different threads.
    """

    def __init__(self, *, max_candles: int = _MAX_CANDLES) -> None:
        self._candles: Deque[Candle] = deque(maxlen=max_candles)
        self._reset_bucket()
        self._cum_ttq: Optional[float] = None
        self._cum_ttv: Optional[float] = None  # rupees
        self._avg_price: Optional[float] = None
        # Cumulative readings as at the close of the previous completed bucket.
        self._prev_ttq: Optional[float] = None
        self._prev_ttv: Optional[float] = None
        self._vwap_reason: Optional[str] = "no_ticks_yet"
        self._resets = 0
        self._stale_ticks = 0
        # The IST trading day this history belongs to. `None` until the first tick.
        self._session_date: Optional[datetime.date] = None
        self._cross_check_diff: Optional[float] = None
        self._cross_check_warned = False

    # ---------------------------------------------------------------- ingest

    def _reset_bucket(self) -> None:
        self._start: Optional[int] = None
        self._open = self._high = self._low = self._close = 0.0
        self._ticks = 0
        self._bucket_ttq: Optional[float] = None
        self._bucket_ttv: Optional[float] = None

    def ingest(
        self,
        ts: float,
        last: Any,
        ttq: Any = None,
        ttv: Any = None,
        avg_price: Any = None,
    ) -> list[Candle]:
        """Feed one tick. Returns candles completed by this tick (0 or 1 in practice).

        A tick with no usable `last` is discarded: it carries no price, and admitting it
        would let a malformed payload set a bar's open.

        Two admission checks run before the tick is allowed to touch anything, in this
        order, because both are reasons the tick does not belong to the history we hold:

        1. A new IST trading day starts a new session -- the exchange's cumulative counters
           restart there, so the previous day's baseline is meaningless against it.
        2. Counters that run *backwards* within a day mean a stale or out-of-order packet.
           It is dropped whole, price included: its `last` is as old as its counters, and
           admitting it would let a stale print set a bar's high or low.
        """
        price = coerce_float(last)
        if price is None:
            return []

        day = trading_date_of(ts)
        if self._session_date is None:
            self._session_date = day
        elif day != self._session_date:
            self._start_new_session(day)

        q, v, avg = coerce_float(ttq), parse_ttv(ttv), coerce_float(avg_price)
        if self._counters_regressed(q, v):
            self._stale_ticks += 1
            _logger.debug(
                "scalping candles: dropping stale tick (ttq=%s ttv=%s; held ttq=%s ttv=%s)",
                q, v, self._cum_ttq, self._cum_ttv,
            )
            return []

        completed = self._roll_to(bucket_start(ts))
        self._apply_price(price)
        self._apply_counters(q, v, avg)
        return completed

    def _counters_regressed(self, q: Optional[float], v: Optional[float]) -> bool:
        """True when this tick's cumulative counters are behind the ones already held."""
        return (q is not None and self._cum_ttq is not None and q < self._cum_ttq) or (
            v is not None and self._cum_ttv is not None and v < self._cum_ttv
        )

    def _apply_price(self, price: float) -> None:
        if self._ticks == 0:
            self._open = self._high = self._low = price
        else:
            self._high = max(self._high, price)
            self._low = min(self._low, price)
        self._close = price
        self._ticks += 1

    def _apply_counters(
        self, q: Optional[float], v: Optional[float], avg: Optional[float]
    ) -> None:
        """Track the latest cumulative readings.

        Regression is not handled here: `ingest` has already refused a tick whose counters
        run backwards, so anything reaching this point is at or ahead of what we hold.
        """
        if q is not None:
            self._cum_ttq = q
            self._bucket_ttq = q
        if v is not None:
            self._cum_ttv = v
            self._bucket_ttv = v
        if avg is not None and avg > 0:
            self._avg_price = avg
        self._refresh_vwap_state()

    def _start_new_session(self, day: datetime.date) -> None:
        """Drop history at an IST day boundary -- the one place it is legitimately discarded.

        The exchange's cumulative counters restart with the trading day, so the previous
        day's baseline would produce one enormous opening bar. A *within-day* regression is
        no longer routed here: that is a stale packet, and `ingest` drops the packet instead
        of six hours of history. Wiping on every out-of-order tick is what left the momentum
        bot below its 20-candle warm-up for most of a session.
        """
        self._resets += 1
        _logger.info(
            "scalping candles: new trading day %s (was %s); dropping %d candle(s)",
            day,
            self._session_date,
            len(self._candles),
        )
        self._session_date = day
        self._candles.clear()
        self._reset_bucket()
        self._cum_ttq = None
        self._cum_ttv = None
        self._avg_price = None
        self._prev_ttq = None
        self._prev_ttv = None
        self._refresh_vwap_state()

    def _refresh_vwap_state(self) -> None:
        self._vwap_reason = None if self._avg_price else "avg_price_missing"
        self._update_cross_check()

    def _update_cross_check(self) -> None:
        """Compare `avgPrice` against `ttv`/`ttq`; warn once per builder on divergence."""
        if self._avg_price is None or not self._cum_ttq or self._cum_ttv is None:
            self._cross_check_diff = None
            return
        derived = self._cum_ttv / self._cum_ttq
        self._cross_check_diff = abs(derived - self._avg_price) / max(self._avg_price, 1e-9)
        if self._cross_check_diff > VWAP_CROSS_CHECK_TOLERANCE and not self._cross_check_warned:
            self._cross_check_warned = True
            _logger.warning(
                "scalping candles: VWAP cross-check disagrees -- avgPrice=%.4f but "
                "ttv/ttq=%.4f (%.2f%%). One of the two changed meaning; VWAP is still "
                "taken from avgPrice.",
                self._avg_price,
                derived,
                self._cross_check_diff * 100,
            )

    def _roll_to(self, start: int) -> list[Candle]:
        """Close the open bucket if `start` has moved past it."""
        if self._start is None:
            self._start = start
            return []
        if start == self._start:
            return []
        if start < self._start:
            # Out-of-order arrival: fold it into the current bar rather than reopening a
            # closed one. A late tick must not un-publish a candle the signal already saw.
            _logger.debug("scalping candles: out-of-order tick for bucket %s", start)
            return []
        completed = self._close_bucket()
        self._start = start
        return [completed] if completed is not None else []

    def _close_bucket(self) -> Optional[Candle]:
        if self._start is None or self._ticks == 0:
            self._reset_bucket()
            return None
        volume: Optional[int] = None
        turnover: Optional[float] = None
        if self._bucket_ttq is not None and self._prev_ttq is not None:
            volume = int(max(0.0, self._bucket_ttq - self._prev_ttq))
        if self._bucket_ttv is not None and self._prev_ttv is not None:
            turnover = max(0.0, self._bucket_ttv - self._prev_ttv)
        candle = Candle(
            start=self._start,
            open=self._open,
            high=self._high,
            low=self._low,
            close=self._close,
            volume=volume,
            turnover=turnover,
            ticks=self._ticks,
        )
        if self._bucket_ttq is not None:
            self._prev_ttq = self._bucket_ttq
        if self._bucket_ttv is not None:
            self._prev_ttv = self._bucket_ttv
        self._candles.append(candle)
        self._reset_bucket()
        return candle

    def flush(self, now_ts: float) -> list[Candle]:
        """Close the open bucket once the clock has left it, with no tick to trigger it.

        A thin contract can go a whole minute without printing, and the signal must still see
        that minute close rather than waiting for the next trade.
        """
        if self._start is None:
            return []
        if bucket_start(now_ts) <= self._start:
            return []
        completed = self._close_bucket()
        self._start = bucket_start(now_ts)
        return [completed] if completed is not None else []

    # ---------------------------------------------------------------- read

    @property
    def candles(self) -> list[Candle]:
        """Completed candles, oldest first. The in-progress bar is deliberately excluded."""
        return list(self._candles)

    @property
    def counter_resets(self) -> int:
        return self._resets

    @property
    def stale_ticks(self) -> int:
        """Packets refused because their cumulative counters ran backwards."""
        return self._stale_ticks

    @property
    def vwap_unavailable_reason(self) -> Optional[str]:
        """None when VWAP is computable; otherwise a stable code for the run log."""
        return self._vwap_reason

    @property
    def vwap_cross_check_diff(self) -> Optional[float]:
        """Relative gap between `avgPrice` and `ttv`/`ttq`, or None when not comparable."""
        return self._cross_check_diff

    @property
    def session_vwap(self) -> Optional[float]:
        """Session VWAP, taken from the tick's own `avgPrice` -- see the module docstring."""
        return self._avg_price if self._vwap_reason is None else None

    def ema(self, period: int) -> Optional[float]:
        """EMA of candle closes, seeded with the SMA of the first `period` bars.

        Seeding from an SMA rather than the first close is the textbook construction and,
        more usefully here, makes the value independent of how long the process has been
        running: two deployments started at different times converge on the same number from
        the same candles.
        """
        return ema_of([c.close for c in self._candles], period)

    def volume_ma(self, period: int) -> Optional[float]:
        """Mean volume over the last `period` completed bars.

        Requires `period` bars with *known* volume: a bar whose volume is None makes the
        window incomplete rather than counting as zero.
        """
        vols = [c.volume for c in self._candles]
        if len(vols) < period:
            return None
        window = vols[-period:]
        if any(v is None for v in window):
            return None
        return sum(int(v) for v in window if v is not None) / float(period)

    def is_warm(self, *, ema_period: int, volume_ma_period: int) -> bool:
        """True when every indicator the signal needs is computable."""
        return (
            self.ema(ema_period) is not None
            and self.volume_ma(volume_ma_period) is not None
            and self.session_vwap is not None
        )

    def warmup_status(self, *, ema_period: int, volume_ma_period: int) -> dict[str, Any]:
        """Why the builder is or is not warm -- rendered into the run log, not just logged."""
        return {
            "candles": len(self._candles),
            "candles_required": max(ema_period, volume_ma_period),
            "ema": self.ema(ema_period),
            "volume_ma": self.volume_ma(volume_ma_period),
            "session_vwap": self.session_vwap,
            "vwap_unavailable_reason": self._vwap_reason,
            "vwap_cross_check_diff": self._cross_check_diff,
            "counter_resets": self._resets,
            "stale_ticks": self._stale_ticks,
            "warm": self.is_warm(ema_period=ema_period, volume_ma_period=volume_ma_period),
        }


def ema_of(values: Iterable[float], period: int) -> Optional[float]:
    """Standalone EMA so the backtest can reuse it without constructing a builder."""
    series = list(values)
    if period <= 0 or len(series) < period:
        return None
    seed = sum(series[:period]) / float(period)
    multiplier = 2.0 / (period + 1)
    ema = seed
    for value in series[period:]:
        ema = (value - ema) * multiplier + ema
    return ema
