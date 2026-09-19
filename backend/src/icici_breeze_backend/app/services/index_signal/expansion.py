"""Volume-confirmed price expansion, with open-interest quadrants: the pure part (#33, #34).

    price move   = return of the underlying over a window W, in bps
    participation= traded quantity summed over the same W
    expansion    = |price move| AND participation both in the top tail of their own recent
                   distributions, judged by PERCENTILE RANK, never by a multiple of a mean
    direction    = the sign of the price move
    confirmation = the sign of the open-interest change over W, where OI is available

No I/O and no clock reads -- every call takes its timestamp, so irregular bar spacing and
staleness are testable exactly. Callers push completed bars in; `publisher` asks for snapshots.

Why percentile rank and not "above average volume"
--------------------------------------------------
Bar volume is right-skewed: a handful of huge bars drag the mean above where most bars sit.
Measured on NIFTY futures (2026-09-15), `volume / 20-bar mean` had median 0.67, p90 1.14 and
p95 1.40 -- so the scalper's `volume > 1.5 x mean` test sat past the 95th percentile and fired
on 4.9% of bars. Across 122 logged bars its full condition never once co-occurred, which would
have read as "no edge" when it was really "never fired". A percentile of the trailing
distribution says what it means: `volume_percentile = 0.80` fires on the top fifth of bars,
whatever the day's absolute level. The price threshold is a percentile for the same reason --
a 15 bps minute is unremarkable at 09:15 and extraordinary at 13:00.

Why the window floor is 15 minutes
----------------------------------
Open interest is a position count and it moves slowly. Measured on NIFTY futures over four
sessions, the median one-minute |OI change| is 0.0093% of outstanding -- about 1,700 contracts
out of 18.5 million, which is noise. Over 15 minutes the median is 0.132%. An OI window shorter
than W_MIN_MINUTES is reading rounding error, so the params refuse it. The floor is on the OI
reading only: the price-and-volume window may be shorter, with OI still judged over its own
15 minutes, and a variant that reads no OI at all has no floor (#38).

Why an unwind quadrant is neutral and not a reversal call
---------------------------------------------------------
With OI, the four cases are:

    price up   + OI up    -> new longs        -> bullish
    price up   + OI down  -> shorts covering  -> NEUTRAL (the rally is an unwind)
    price down + OI up    -> new shorts       -> bearish
    price down + OI down  -> longs liquidating-> NEUTRAL (the fall is an unwind)

OI is used as a *confirmation filter*, so it only ever confirms a continuation. Scoring an
unwind as a call in the opposite direction would be a reversal bet -- a much stronger claim
than "new money is not behind this move", and one nothing here has evidence for. Refusing to
call is the fail-closed reading, consistent with `unavailable` never being `neutral` (#30).

Why SENSEX runs without the OI half
-----------------------------------
ICICI serves no open interest for BSE. Measured 2026-09-16 on one session with a working NSE
control: NIFTY futures returned OI on 386 of 395 bars, while SENSEX futures and a SENSEX option
both returned `open_interest: 0` on every one of 376 bars. The live BFO tick *does* carry OI, so
a SENSEX OI signal could run live -- but it could never be backtested, which is the position
#33 exists to avoid. So `require_oi=False` runs price-and-volume alone: a deliberately weaker
signal that cannot tell a breakout from a blow-off, and must be labelled as such wherever it is
shown (#34).
"""
from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from datetime import date
from typing import Any, Deque, Literal, Optional

from icici_breeze_backend.app.services.index_signal.engine import (
    REASON_MARKET_CLOSED,
    REASON_WARMING_UP,
    DirectionalState,
    SignalState,
)

# Below this the OI reading is rounding error -- see the module docstring.
W_MIN_MINUTES = 15

REASON_NO_BARS = "no_bars"
REASON_STALE = "stale"
REASON_NO_EXPANSION = "no_expansion"
REASON_NO_OI = "no_open_interest"
REASON_UNWIND = "unwind"
REASON_EXCLUDED_SESSION = "excluded_session"

Quadrant = Literal["new_longs", "short_covering", "new_shorts", "long_liquidation"]


@dataclass(frozen=True)
class ExpansionParams:
    #: The price-and-volume window: the move and the traded quantity behind it are measured
    #: over this span and ranked against earlier spans of the same length.
    window_minutes: int = 15
    #: The open-interest window, when it differs from the price window (#38). None means the
    #: same span. Only the OI reading has a floor -- a 5-minute price move confirmed by 15
    #: minutes of OI is legitimate; 5 minutes of OI is rounding error.
    oi_window_minutes: Optional[int] = None
    #: Percentile of the trailing distribution a reading must beat. 0.80 = the top fifth.
    price_percentile: float = 0.80
    volume_percentile: float = 0.80
    #: Trailing bars the percentiles are measured against, and the floor below which there is
    #: no distribution worth ranking against yet.
    baseline_bars: int = 120
    min_baseline_bars: int = 60
    #: Whether the OI quadrant is required. False for SENSEX, where ICICI serves no OI.
    require_oi: bool = True
    #: A call stands for this long after it fires, then lapses to neutral unless re-fired.
    #: It is the window that produced it: a 15-minute expansion is a statement about the next
    #: 15 minutes, not a standing opinion.
    hold_minutes: int = 15
    #: A bar older than this means the feed has stopped; there is no reading, not a stale one.
    stale_seconds: float = 180.0
    #: The largest gap allowed between consecutive bars *inside* a window. A window that spans
    #: a bigger one is not a reading at all: the overnight break would otherwise be measured as
    #: a 17-hour "15-minute" expansion at every open, and a mid-session feed hole would have
    #: its missing volume silently omitted from the sum.
    max_gap_seconds: float = 300.0

    @property
    def oi_window(self) -> int:
        return self.window_minutes if self.oi_window_minutes is None else self.oi_window_minutes

    @property
    def span_minutes(self) -> int:
        """The longest window a reading needs, i.e. how many bars back it reaches."""
        return max(self.window_minutes, self.oi_window) if self.require_oi else self.window_minutes

    def __post_init__(self) -> None:
        if self.window_minutes < 1:
            raise ValueError("window_minutes must be at least 1")
        if self.require_oi and self.oi_window < W_MIN_MINUTES:
            raise ValueError(
                f"the open-interest window must be at least {W_MIN_MINUTES} minutes: below that "
                "the median one-minute OI change is under 0.01% of outstanding, which is noise"
            )
        if self.hold_minutes < 1:
            raise ValueError("hold_minutes must be at least 1")
        for name in ("price_percentile", "volume_percentile"):
            value = getattr(self, name)
            if not 0.0 < value < 1.0:
                raise ValueError(f"{name} must be a percentile strictly between 0 and 1")
        if self.min_baseline_bars > self.baseline_bars:
            raise ValueError("min_baseline_bars cannot exceed baseline_bars")
        if self.min_baseline_bars <= self.span_minutes:
            raise ValueError(
                "the baseline must be longer than the window it ranks, or the first reading "
                "is ranked against the bars that produced it"
            )


@dataclass(frozen=True)
class Bar:
    """One completed one-minute bar of the underlying.

    `volume` and `oi` are None when *unknown*, never zero-as-unknown. A dropped tick leaves
    volume unknown (`candles.Candle` says why), and ICICI serves OI as 0 on pre-open bars and
    on every BSE bar -- a zero that means absent. Callers normalise both to None; this module
    treats None as "no reading" and never as a low number.
    """

    ts: float  # epoch seconds, the bar's START
    close: float
    volume: Optional[float] = None
    oi: Optional[float] = None


def percentile_rank(values: list[float], reading: float) -> Optional[float]:
    """Share of `values` strictly below `reading`, in [0, 1]. None for an empty baseline.

    Strictly below, so a run of identical values (a dead market where every bar is the same
    volume) ranks at 0 and cannot fire -- the tail has to be a real tail."""
    if not values:
        return None
    return sum(1 for v in values if v < reading) / len(values)


def quadrant(price_delta: float, oi_delta: float) -> Quadrant:
    """Which of the four price/OI cases this is. See the module docstring for the mapping."""
    if price_delta > 0:
        return "new_longs" if oi_delta > 0 else "short_covering"
    return "new_shorts" if oi_delta > 0 else "long_liquidation"


QUADRANT_SIDE: dict[str, Optional[DirectionalState]] = {
    "new_longs": "bullish",
    "new_shorts": "bearish",
    # An unwind is not a reversal call -- see the module docstring.
    "short_covering": None,
    "long_liquidation": None,
}


def _window_reading(
    bars: list[Bar], end: int, window: int, max_gap_seconds: float = 300.0
) -> Optional[tuple[float, Optional[float], Optional[float]]]:
    """(price move in bps, summed volume, OI change) for the window ENDING at index `end`.

    None when the window does not fit, or when its anchor price is unusable. Volume and OI come
    back as None if any bar in the window is missing one -- a partial sum understates the
    window, and understating participation is what suppresses the filter exactly when the feed
    has just come up (`candles.Candle`)."""
    start = end - window
    if start < 0 or end >= len(bars):
        return None
    first, last = bars[start], bars[end]
    if not first.close > 0:
        return None
    span_bars = bars[start : end + 1]
    if any(b.ts - a.ts > max_gap_seconds for a, b in zip(span_bars, span_bars[1:])):
        # Spans a session boundary or a feed hole -- see `max_gap_seconds`.
        return None
    price_bps = (last.close - first.close) / first.close * 1e4

    span = bars[start + 1 : end + 1]
    volume: Optional[float] = None
    if all(b.volume is not None for b in span):
        volume = sum(float(b.volume) for b in span)  # type: ignore[arg-type]
    return price_bps, volume, _oi_change(first, last)


def _oi_change(first: Bar, last: Bar) -> Optional[float]:
    # A non-positive OI is absent, never a reading. ICICI serves 0 on pre-open bars and on
    # *every* BSE bar, so a zero reaching here is likely rather than hypothetical -- and a zero
    # anchor would turn the next real value into the largest OI rise ever recorded.
    if first.oi is not None and last.oi is not None and first.oi > 0 and last.oi > 0:
        return float(last.oi) - float(first.oi)
    return None


def _oi_reading(bars: list[Bar], end: int, window: int, max_gap_seconds: float) -> Optional[float]:
    """The OI change over its own window ending at `end` (#38), under the same gap rule."""
    start = end - window
    if start < 0 or end >= len(bars):
        return None
    span_bars = bars[start : end + 1]
    if any(b.ts - a.ts > max_gap_seconds for a, b in zip(span_bars, span_bars[1:])):
        return None
    return _oi_change(bars[start], bars[end])


def evaluate(
    bars: list[Bar], params: ExpansionParams
) -> tuple[Optional[DirectionalState], Optional[float], dict[str, Any], Optional[str]]:
    """(side, strength, components, reason) for the latest bar in `bars`.

    `strength` is signed and in [-1, +1]: the direction of the price move times the weaker of
    the two percentile ranks. It is reported whether or not the signal fires, because the
    shadow log needs a continuous number to correlate against the next move -- a signal that
    only ever emits three states cannot be scored except by hit rate, and hit rate on a rarely
    firing signal is what #33 showed to be unreadable.
    """
    w = params.window_minutes
    needed = params.min_baseline_bars + params.span_minutes
    if len(bars) < needed:
        return None, None, {"bars": len(bars), "bars_required": needed}, REASON_WARMING_UP

    now = _window_reading(bars, len(bars) - 1, w, params.max_gap_seconds)
    if now is None:
        return None, None, {"bars": len(bars)}, REASON_STALE
    price_bps, volume, oi_delta = now
    if params.oi_window != w:
        oi_delta = _oi_reading(bars, len(bars) - 1, params.oi_window, params.max_gap_seconds)

    # Like against like: the current W-window reading is ranked against earlier W-window
    # readings, never against single bars.
    price_baseline: list[float] = []
    volume_baseline: list[float] = []
    for k in range(1, params.baseline_bars + 1):
        past = _window_reading(bars, len(bars) - 1 - k, w, params.max_gap_seconds)
        if past is None:
            # A gap does not end the baseline -- yesterday's windows are perfectly good
            # samples of what a typical window looks like. Only the ones straddling it are
            # dropped, which is what lets `seed` warm the distribution across sessions.
            continue
        price_baseline.append(abs(past[0]))
        if past[1] is not None:
            volume_baseline.append(past[1])

    price_rank = percentile_rank(price_baseline, abs(price_bps))
    volume_rank = percentile_rank(volume_baseline, volume) if volume is not None else None

    components: dict[str, Any] = {
        "price_bps": round(price_bps, 2),
        "volume": None if volume is None else round(volume, 0),
        "oi_delta": None if oi_delta is None else round(oi_delta, 0),
        "price_rank": None if price_rank is None else round(price_rank, 4),
        "volume_rank": None if volume_rank is None else round(volume_rank, 4),
        "quadrant": None,
    }

    if price_rank is None or volume_rank is None:
        return None, None, components, REASON_WARMING_UP

    strength = (1.0 if price_bps >= 0 else -1.0) * min(price_rank, volume_rank)
    components["strength"] = round(strength, 4)

    if price_rank < params.price_percentile or volume_rank < params.volume_percentile:
        # A reading, not a blank: the market was looked at and nothing expanded.
        return None, strength, components, REASON_NO_EXPANSION

    if not params.require_oi:
        # Price and volume alone. No continuation/exhaustion filter -- see the docstring.
        return ("bullish" if price_bps > 0 else "bearish"), strength, components, None

    if oi_delta is None:
        return None, strength, components, REASON_NO_OI

    quad = quadrant(price_bps, oi_delta)
    components["quadrant"] = quad
    side = QUADRANT_SIDE[quad]
    return side, strength, components, (None if side else REASON_UNWIND)


def in_rollover_window(today: date, futures_expiry: date, days_before: int = 2) -> bool:
    """True on the days around a futures contract's expiry, when OI moves for mechanical
    reasons: positions migrate to the next month and the expiring contract's OI collapses
    whatever anyone thinks of the index. The caller passes this in as `excluded`.

    Only the *futures* expiry matters here, not the weekly option expiries: the bars come from
    the near-month futures contract, which rolls once a month."""
    delta = (futures_expiry - today).days
    return 0 <= delta <= days_before


def _history_len(params: ExpansionParams) -> int:
    return params.baseline_bars + params.span_minutes + 1


class ExpansionEngine:
    """One index's live state.

    `on_bar` runs on whichever thread completes a candle and `snapshot` on the publisher's, so
    both take the lock; neither does I/O.
    """

    def __init__(self, label: str, params: ExpansionParams) -> None:
        self.label = label
        self._lock = threading.Lock()
        self._params = params
        self._bars: Deque[Bar] = deque(maxlen=_history_len(params))
        self._held: Optional[DirectionalState] = None
        self._held_until: float = 0.0
        #: When the call now held first fired. A call re-fired while it is still held is the
        #: same call, extended: "one trade per call" keys on this (#38).
        self._call_started: Optional[float] = None
        self._last: dict[str, Any] = {}

    @property
    def params(self) -> ExpansionParams:
        with self._lock:
            return self._params

    def set_params(self, params: ExpansionParams) -> None:
        """Swap tuning in place. A different window or baseline discards the bars: they were
        ranked on a distribution that no longer applies."""
        with self._lock:
            if params == self._params:
                return
            rebuild = _history_len(params) != _history_len(self._params) or (
                params.window_minutes != self._params.window_minutes
                or params.oi_window != self._params.oi_window
            )
            self._params = params
            if rebuild:
                self._bars = deque(maxlen=_history_len(params))
                self._held, self._held_until, self._call_started = None, 0.0, None

    def on_bar(self, bar: Bar) -> None:
        """Feed one completed bar. Out-of-order and duplicate bars are ignored rather than
        appended: the percentile baseline assumes an ordered series, and a replayed bar would
        rank against a window that already contains it."""
        with self._lock:
            if self._bars and bar.ts <= self._bars[-1].ts:
                return
            self._bars.append(bar)
            side, strength, components, reason = evaluate(list(self._bars), self._params)
            self._last = {"strength": strength, "components": components, "reason": reason}
            if side is not None:
                if side != self._held or bar.ts >= self._held_until:
                    self._call_started = bar.ts
                self._held = side
                self._held_until = bar.ts + self._params.hold_minutes * 60.0

    def seed(self, bars: list[Bar]) -> None:
        """Warm the baseline from history (the previous session's bars) so the first live call
        does not wait an hour for a distribution to rank against."""
        for bar in bars:
            self.on_bar(bar)

    def snapshot(self, now: float, *, session_open: bool, excluded: bool = False) -> dict[str, Any]:
        """The published view at `now`.

        `excluded` is passed in by the caller for the sessions where this mechanism must not
        speak at all -- expiry day and rollover week, where OI moves because contracts die
        rather than because anyone changed their mind. The engine owns no calendar.
        """
        with self._lock:
            p = self._params
            last_ts = self._bars[-1].ts if self._bars else None
            reason: Optional[str] = self._last.get("reason")
            state: SignalState

            if not session_open:
                reason = REASON_MARKET_CLOSED
            elif excluded:
                reason = REASON_EXCLUDED_SESSION
            elif last_ts is None:
                reason = REASON_NO_BARS
            elif now - last_ts > p.stale_seconds:
                reason = REASON_STALE

            if reason in (
                REASON_MARKET_CLOSED,
                REASON_EXCLUDED_SESSION,
                REASON_NO_BARS,
                REASON_STALE,
                REASON_WARMING_UP,
                REASON_NO_OI,
            ):
                self._held, self._held_until, self._call_started = None, 0.0, None
                state = "unavailable"
            elif self._held is not None and now < self._held_until:
                state = self._held
                reason = None
            else:
                # The call has lapsed, or nothing expanded. Both are readings: neutral.
                self._held, self._held_until, self._call_started = None, 0.0, None
                state = "neutral"

            return {
                "label": self.label,
                "state": state,
                "reason": reason,
                "signal": self._last.get("strength"),
                "raw_wobi": None,
                # Price and volume alone is a coverage of one half, and the payload says so.
                "coverage": 1.0 if p.require_oi else 0.5,
                "requires_oi": p.require_oi,
                "window_minutes": p.window_minutes,
                "oi_window_minutes": p.oi_window if p.require_oi else None,
                "hold_minutes": p.hold_minutes,
                "call_started_at": self._call_started if state in ("bullish", "bearish") else None,
                "thresholds": {
                    "price_percentile": p.price_percentile,
                    "volume_percentile": p.volume_percentile,
                },
                "components": dict(self._last.get("components") or {}),
                "held_until": self._held_until or None,
                "computed_at": now,
            }
