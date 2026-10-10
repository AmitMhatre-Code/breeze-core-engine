"""The premium gate's reading: implied move over forecast move (docs/premium-gate-plan.md).

What these pin down:
* the forecast is built from the index's own past sessions and fails closed without enough of them;
* the time-of-day share is measured from those sessions, not assumed from the clock;
* the implied side recovers the volatility the options were priced at, whatever the carry;
* sellers trade at or above the threshold, the buyer at or below it, and nobody trades blind.
"""
from __future__ import annotations

import datetime
import math
import random
from dataclasses import dataclass

import pytest

from icici_breeze_backend.app.services.condor.pricing import bs_price
from icici_breeze_backend.app.services.premium_gate import reading as pg


@dataclass
class _Bar:
    ts: datetime.datetime
    open: float
    close: float


def _sessions(n: int, *, minute_sd: float = 0.0004, gap_sd: float = 0.003, seed: int = 7,
              start=datetime.date(2026, 6, 1), loud_minutes: int = 0) -> list[_Bar]:
    """`n` weekday sessions of a random walk. With `loud_minutes`, all movement happens in the
    session's first that-many minutes and the rest is flat."""
    rng = random.Random(seed)
    bars: list[_Bar] = []
    level, day = 25_000.0, start
    while len([d for d in {b.ts.date() for b in bars}]) < n:
        if day.weekday() < 5:
            level *= math.exp(rng.gauss(0, gap_sd))
            t0 = datetime.datetime.combine(day, pg.SESSION_OPEN)
            for m in range(pg.SESSION_MINUTES):
                sd = minute_sd if not loud_minutes or m < loud_minutes else 0.0
                prev = level
                level *= math.exp(rng.gauss(0, sd))
                bars.append(_Bar(t0 + datetime.timedelta(minutes=m), prev, level))
        day += datetime.timedelta(days=1)
    return bars


def _now(sessions: list[pg.SessionVol], hh: int, mm: int) -> datetime.datetime:
    day = sessions[-1].day + datetime.timedelta(days=1)
    while day.weekday() >= 5:
        day += datetime.timedelta(days=1)
    return datetime.datetime.combine(day, datetime.time(hh, mm))


def test_a_session_measures_its_intraday_movement_and_the_gap_into_it():
    s = pg.session_vols(_sessions(3))
    assert len(s) == 3
    # 375 minutes at 0.04% each: about 375 x 0.0004^2 of variance.
    assert s[1].intraday == pytest.approx(pg.SESSION_MINUTES * 0.0004 ** 2, rel=0.25)
    assert s[0].overnight is None and s[1].overnight is not None


def test_a_session_with_too_few_bars_is_a_gap_not_a_session():
    bars = _sessions(2)
    short = [b for b in bars if b.ts.date() != bars[-1].ts.date()] + [
        b for b in bars if b.ts.date() == bars[-1].ts.date()][:100]
    assert len(pg.session_vols(short)) == 1


def test_no_forecast_without_enough_history_and_so_no_trade():
    sessions = pg.session_vols(_sessions(pg.MIN_SESSIONS - 1))
    fc = pg.forecast(sessions, _now(sessions, 9, 30), 0)
    assert fc is None
    r = pg.reading(1e-4, fc)
    assert r.reason == pg.REASON_NO_HISTORY
    assert not r.allows("sell", 1.0) and not r.allows("buy", 1.0)


def test_the_forecast_shrinks_through_the_day_and_grows_with_each_session_to_expiry():
    sessions = pg.session_vols(_sessions(25))
    at_open = pg.forecast(sessions, _now(sessions, 9, 15), 0)
    midday = pg.forecast(sessions, _now(sessions, 12, 22), 0)
    closed = pg.forecast(sessions, _now(sessions, 15, 30), 0)
    two_days = pg.forecast(sessions, _now(sessions, 9, 15), 2)
    assert at_open.share_today == 1.0 and closed.variance == 0.0
    assert midday.share_today == pytest.approx(0.5, abs=0.1)
    assert two_days.variance == pytest.approx(
        at_open.variance + 2 * (two_days.overnight + two_days.intraday))
    assert at_open.sessions_used == pg.LONG_SESSIONS


def test_time_of_day_is_measured_not_assumed():
    """When every past session moved only in its first half hour, an hour in nothing is left."""
    sessions = pg.session_vols(_sessions(20, loud_minutes=30))
    fc = pg.forecast(sessions, _now(sessions, 10, 15), 0)
    assert fc.share_today < 0.01


def test_the_implied_side_recovers_the_volatility_the_pair_was_priced_at():
    spot, strike, years, sigma, q = 25_010.0, 25_000.0, 3 / 365, 0.14, 0.012
    call = bs_price("Call", spot, strike, years, sigma, pg.RISK_FREE, q)
    put = bs_price("Put", spot, strike, years, sigma, pg.RISK_FREE, q)
    w, why = pg.implied_variance(call, put, strike, spot, years)
    assert why is None
    assert math.sqrt(w / years) == pytest.approx(sigma, abs=1e-3)


def test_one_missing_side_is_no_reading():
    w, why = pg.implied_variance(120.0, None, 25_000, 25_000, 0.01)
    assert w is None and why == pg.REASON_NO_ATM_QUOTES
    r = pg.reading(w, pg.Forecast(1e-4, 1e-4, 0, 1, 0, 20), why)
    assert r.reason == pg.REASON_NO_ATM_QUOTES and not r.allows("buy", 5.0)


def test_sellers_need_rich_premium_and_the_buyer_cheap():
    fc = pg.Forecast(variance=1e-4, intraday=1e-4, overnight=0, share_today=1, sessions_after_today=0,
                     sessions_used=20)
    rich = pg.reading(4e-4, fc)        # implied move twice the forecast
    cheap = pg.reading(0.25e-4, fc)    # half of it
    assert rich.ratio == pytest.approx(2.0) and cheap.ratio == pytest.approx(0.5)
    assert rich.allows("sell", 1.0) and not rich.allows("buy", 1.0)
    assert cheap.allows("buy", 1.0) and not cheap.allows("sell", 1.0)
    assert "2.00x the forecast move" in rich.describe()


def test_a_strike_by_implied_move_sits_symmetrically_in_log_terms():
    up = pg.strike_target(25_000, 0.0001, 2.5, up=True)
    down = pg.strike_target(25_000, 0.0001, 2.5, up=False)
    assert up * down == pytest.approx(25_000 ** 2)
    assert up == pytest.approx(25_000 * math.exp(0.025))
