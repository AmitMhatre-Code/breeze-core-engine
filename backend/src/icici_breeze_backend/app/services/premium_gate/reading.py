"""Is premium rich or cheap? The pure part of the premium gate (docs/premium-gate-plan.md).

One number: the move the options price to expiry, divided by the move the index's own recent
history forecasts over the same span. Sellers (Bot 2, the Iron Fly) trade only when it is at
least their threshold; the Long Scalper buys only when it is at most its threshold.

    implied   total variance to expiry, w = sigma^2 * T, from the ATM call and put
    forecast  variance to expiry, V, from the cash index's one-minute bars of recent sessions
    ratio     R = sqrt(w / V)

No I/O and no clock reads: every input is passed in, so live and replay compute the reading with
exactly this code.

Why total variance, not an IV figure
------------------------------------
An IV is quoted per year on some day count -- the Portfolio model uses calendar time to 15:30 --
and a forecast built from trading sessions has its own. Their product with the time each uses,
the total variance to expiry, is the same whatever the convention, so it is what is compared.

Why the cash index, not futures
-------------------------------
BSESEN futures go untraded in about half of all minutes, so their bars understate SENSEX's
movement. The cash index is computed every second from its constituents and always moves. Bars
after 15:15 carry the closing auction's indicative value; they are kept, because an option on its
expiry day settles on that auction.

Why the time-of-day share is measured
-------------------------------------
Index volatility is U-shaped: the first and last half hours carry far more than the middle. "A
third of the session is left" is not "a third of the day's movement is left", so each past
session's cumulative variance is kept minute by minute, and the share still to come after the
current minute is averaged over the same sessions the level comes from.

Why a fixed 5/20 blend
----------------------
Recent volatility predicts the next day's better than the month's average does, but a single week
is noisy. Half the last 5 sessions and half the last 20 is the simplest blend that uses both. It is
not fitted to anything; a fitted blend would be the first thing the backtest flattered.
"""
from __future__ import annotations

import datetime
import math
from dataclasses import dataclass
from typing import Iterable, Literal, Optional, Protocol, Sequence

#: Raise in any change to what a reading computes for the same inputs. Stamped on every backtest
#: run and in each bot's settings fingerprint, like a signal's version (#72).
GATE_VERSION = 1

SESSION_OPEN = datetime.time(9, 15)
SESSION_CLOSE = datetime.time(15, 30)
SESSION_MINUTES = 375
SHORT_SESSIONS = 5
LONG_SESSIONS = 20
#: Fewer complete sessions than this and there is no forecast: the gate fails closed.
MIN_SESSIONS = 15
#: A session with fewer one-minute bars than this is a data gap, not a session.
MIN_SESSION_BARS = 300
#: The longest calendar gap that still counts as one overnight (a long weekend).
MAX_OVERNIGHT_DAYS = 4

RISK_FREE = 0.07  # as condor.pricing.DEFAULT_R

Side = Literal["sell", "buy"]

REASON_NO_HISTORY = "no_history"
REASON_NO_ATM_QUOTES = "no_atm_quotes"
REASON_NO_IV = "iv_unsolvable"
REASON_EXPIRED = "expired"
REASON_NO_SPOT = "no_live_spot"


class BarLike(Protocol):
    ts: datetime.datetime  # the minute's start, naive IST
    open: float
    close: float


# --------------------------------------------------------------------------------------
# Forecast
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionVol:
    """One past session's movement: its intraday variance, minute by minute, and the gap into it."""

    day: datetime.date
    open: float
    close: float
    intraday: float
    #: Cumulative variance after each minute of the session, SESSION_MINUTES long.
    cumulative: tuple[float, ...]
    overnight: Optional[float] = None

    def share_after(self, minutes_done: int) -> float:
        """The share of this session's variance still to come after `minutes_done` minutes."""
        if minutes_done <= 0 or self.intraday <= 0:
            return 1.0
        if minutes_done >= SESSION_MINUTES:
            return 0.0
        return max(0.0, 1.0 - self.cumulative[minutes_done - 1] / self.intraday)


def _minute_index(ts: datetime.datetime) -> int:
    start = datetime.datetime.combine(ts.date(), SESSION_OPEN)
    return int((ts - start).total_seconds() // 60)


def session_vols(bars: Iterable[BarLike]) -> list[SessionVol]:
    """Each complete session in `bars`, oldest first, with the overnight gap into it where the
    session before it is also present and no more than a long weekend away."""
    by_day: dict[datetime.date, list[BarLike]] = {}
    for bar in bars:
        m = _minute_index(bar.ts)
        if 0 <= m < SESSION_MINUTES and bar.close and bar.close > 0:
            by_day.setdefault(bar.ts.date(), []).append(bar)
    out: list[SessionVol] = []
    for day in sorted(by_day):
        day_bars = sorted(by_day[day], key=lambda b: b.ts)
        if len(day_bars) < MIN_SESSION_BARS:
            continue
        first = day_bars[0]
        session_open = first.open if first.open and first.open > 0 else first.close
        cumulative = [0.0] * SESSION_MINUTES
        total, prev = 0.0, session_open
        for bar in day_bars:
            r = math.log(bar.close / prev)
            total += r * r
            prev = bar.close
            cumulative[_minute_index(bar.ts)] = total
        running = 0.0
        for i, v in enumerate(cumulative):  # minutes with no bar keep the total so far
            running = max(running, v)
            cumulative[i] = running
        overnight = None
        if out and (day - out[-1].day).days <= MAX_OVERNIGHT_DAYS and out[-1].close > 0:
            overnight = math.log(session_open / out[-1].close) ** 2
        out.append(SessionVol(day, session_open, prev, total, tuple(cumulative), overnight))
    return out


@dataclass(frozen=True)
class Forecast:
    variance: float
    intraday: float
    overnight: float
    share_today: float
    sessions_after_today: int
    sessions_used: int


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def forecast(
    sessions: Sequence[SessionVol], now: datetime.datetime, sessions_after_today: int
) -> Optional[Forecast]:
    """Forecast variance of the index's log return from `now` to the expiry close.

    `sessions` are the complete sessions before today, oldest first; `sessions_after_today` is
    how many trading sessions come after today up to and including expiry day (0 on expiry day).
    None with fewer than MIN_SESSIONS: no forecast, so no trade."""
    past = [s for s in sessions if s.day < now.date()][-LONG_SESSIONS:]
    if len(past) < MIN_SESSIONS:
        return None
    intraday = 0.5 * _mean([s.intraday for s in past[-SHORT_SESSIONS:]]) + 0.5 * _mean(
        [s.intraday for s in past]
    )
    gaps = [s.overnight for s in past if s.overnight is not None]
    overnight = _mean(gaps) if gaps else 0.0
    done = _minute_index(now)
    share = _mean([s.share_after(done) for s in past])
    after = max(0, int(sessions_after_today))
    variance = intraday * share + after * (overnight + intraday)
    return Forecast(variance, intraday, overnight, share, after, len(past))


# --------------------------------------------------------------------------------------
# Implied
# --------------------------------------------------------------------------------------


def implied_variance(
    call: Optional[float],
    put: Optional[float],
    strike: float,
    spot: float,
    years: float,
    r: float = RISK_FREE,
) -> tuple[Optional[float], Optional[str]]:
    """Total implied variance to expiry from the ATM pair, or (None, why).

    The forward comes from put-call parity at the strike, so the carry is the market's own; each
    side's IV is solved at it and the two are averaged."""
    from icici_breeze_backend.app.services.condor.pricing import implied_volatility

    if not (call and call > 0 and put and put > 0 and strike > 0 and spot > 0):
        return None, REASON_NO_ATM_QUOTES
    if years <= 0:
        return None, REASON_EXPIRED
    forward = strike + (call - put) * math.exp(r * years)
    if not forward > 0:
        return None, REASON_NO_IV
    q = r - math.log(forward / spot) / years
    ivs = [
        iv for iv in (
            implied_volatility("Call", call, spot, strike, years, r, q),
            implied_volatility("Put", put, spot, strike, years, r, q),
        ) if iv is not None and iv > 0
    ]
    if len(ivs) < 2:
        return None, REASON_NO_IV
    sigma = _mean(ivs)
    return sigma * sigma * years, None


# --------------------------------------------------------------------------------------
# The reading
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Reading:
    """Implied move over forecast move, both one standard deviation to expiry, in % of spot.
    `reason` is set, and the figures are None, when no reading could be made."""

    ratio: Optional[float]
    implied_move_pct: Optional[float]
    forecast_move_pct: Optional[float]
    reason: Optional[str] = None

    @property
    def available(self) -> bool:
        return self.ratio is not None

    def allows(self, side: Side, threshold: float) -> bool:
        """Sellers trade at or above the threshold, the buyer at or below it. No reading, no trade."""
        if self.ratio is None:
            return False
        return self.ratio >= threshold if side == "sell" else self.ratio <= threshold

    def describe(self) -> str:
        if self.ratio is None:
            return _REASON_TEXT.get(self.reason or "", self.reason or "no reading")
        return (f"premium {self.ratio:.2f}x the forecast move (implied ±{self.implied_move_pct:.2f}%, "
                f"forecast ±{self.forecast_move_pct:.2f}% to expiry)")

    def to_dict(self) -> dict:
        return {"ratio": self.ratio, "implied_move_pct": self.implied_move_pct,
                "forecast_move_pct": self.forecast_move_pct, "reason": self.reason,
                "gate_version": GATE_VERSION}


_REASON_TEXT = {
    REASON_NO_HISTORY: f"no premium reading: fewer than {MIN_SESSIONS} sessions of index history",
    REASON_NO_ATM_QUOTES: "no premium reading: no live price for the at-the-money call and put",
    REASON_NO_IV: "no premium reading: the at-the-money prices imply no volatility",
    REASON_EXPIRED: "no premium reading: the option has expired",
    REASON_NO_SPOT: "no premium reading: no live index tick",
}


def describe_reason(reason: Optional[str]) -> str:
    """The plain-words text for a no-reading reason, as `Reading.describe` gives it."""
    return _REASON_TEXT.get(reason or "", reason or "no reading")


def reading(implied: Optional[float], fc: Optional[Forecast], why: Optional[str] = None) -> Reading:
    if fc is None or fc.variance <= 0:
        return Reading(None, None, None, REASON_NO_HISTORY)
    if implied is None or implied <= 0:
        return Reading(None, None, None, why or REASON_NO_ATM_QUOTES)
    return Reading(
        ratio=round(math.sqrt(implied / fc.variance), 4),
        implied_move_pct=round(math.sqrt(implied) * 100.0, 4),
        forecast_move_pct=round(math.sqrt(fc.variance) * 100.0, 4),
    )


def reading_from_variance(
    implied: Optional[float], forecast_variance: Optional[float], why: Optional[str] = None
) -> Reading:
    """`reading` for a caller that already holds the forecast variance (the condor engine, #78)."""
    if forecast_variance is None or forecast_variance <= 0:
        return Reading(None, None, None, why or REASON_NO_HISTORY)
    fc = Forecast(forecast_variance, 0.0, 0.0, 0.0, 0, 0)
    return reading(implied, fc, None if implied else REASON_NO_IV)


def strike_target(spot: float, implied: float, multiple: float, *, up: bool) -> float:
    """The index level `multiple` implied standard deviations away from spot, to expiry -- where a
    strike set "by implied move" sits before it is rounded away from the money."""
    step = multiple * math.sqrt(max(0.0, implied))
    return spot * math.exp(step if up else -step)
