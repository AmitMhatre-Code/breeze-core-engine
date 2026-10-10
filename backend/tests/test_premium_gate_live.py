"""The premium gate's live inputs: the cash-index history cache and the ATM pair from chain rows."""
from __future__ import annotations

import datetime
import math

import pytest

from icici_breeze_backend.app.services.bots.scalping import backtest_store as store
from icici_breeze_backend.app.services.condor.pricing import bs_price, years_to_expiry_close
from icici_breeze_backend.app.services.premium_gate import live
from icici_breeze_backend.app.services.premium_gate import reading as pg

TODAY = datetime.date(2026, 7, 14)  # a Tuesday
_NO_HOLIDAYS: set[datetime.date] = set()


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    live.reset()
    monkeypatch.setattr(live, "_holidays", lambda: _NO_HOLIDAYS)
    yield
    live.reset()


def _seed(path: str, index: str, days: list[datetime.date], minute_sd: float = 0.0004) -> None:
    rows, level, k = [], 25_000.0, 0
    for day in days:
        t0 = datetime.datetime.combine(day, datetime.time(9, 15))
        for m in range(pg.SESSION_MINUTES):
            k += 1
            prev = level
            level *= math.exp(minute_sd * (1 if k % 2 else -1))  # a steady zig-zag
            rows.append({"datetime": (t0 + datetime.timedelta(minutes=m)).strftime("%Y-%m-%d %H:%M:%S"),
                         "open": prev, "high": max(prev, level), "low": min(prev, level), "close": level,
                         "volume": 0})
    store.store_candles(rows, stock_code=index, table="spot_candles", path=path)


def _row(strike: float, bid: float, ask: float, source: str = "websocket") -> dict:
    return {"strike_price": strike, "best_bid_price": bid, "best_offer_price": ask, "quote_source": source}


def test_the_forecast_reads_the_cached_sessions_before_today(tmp_path):
    path = str(tmp_path / "cache.sqlite3")
    store.ensure_tables(path)
    days = live.needed_sessions(TODAY, _NO_HOLIDAYS)
    assert len(days) == pg.LONG_SESSIONS + 1 and days[-1] < TODAY
    _seed(path, "NIFTY", days)
    sessions = live.sessions_for("NIFTY", TODAY, cache_path=path, holidays=_NO_HOLIDAYS)
    assert [s.day for s in sessions] == days
    assert sessions[-1].intraday == pytest.approx(pg.SESSION_MINUTES * 0.0004 ** 2, rel=1e-3)


def test_sessions_to_expiry_count_trading_days_after_today():
    assert live.sessions_after(TODAY, TODAY, _NO_HOLIDAYS) == 0
    # Tue -> next Tue: Wed, Thu, Fri, Mon, Tue.
    assert live.sessions_after(TODAY, TODAY + datetime.timedelta(days=7), _NO_HOLIDAYS) == 5


def test_the_atm_pair_needs_a_live_two_sided_book_on_both_sides():
    calls = [_row(24_950, 160, 162), _row(25_000, 130, 131)]
    puts = [_row(24_950, 100, 101), _row(25_000, 120, 121)]
    assert live.atm_pair(calls, puts, 25_010) == (25_000, 130.5, 120.5)
    # A stand-in price is not a reading; neither is a book 20% wide.
    assert live.atm_pair([_row(25_000, 130, 131, source="bhavcopy")], puts, 25_010)[1] is None
    assert live.atm_pair([_row(25_000, 110, 135)], puts, 25_010)[1] is None


def test_a_live_reading_end_to_end(tmp_path):
    path = str(tmp_path / "cache.sqlite3")
    store.ensure_tables(path)
    _seed(path, "NIFTY", live.needed_sessions(TODAY, _NO_HOLIDAYS))
    now = datetime.datetime.combine(TODAY, datetime.time(9, 30))
    years = years_to_expiry_close(TODAY, now)
    # Price the ATM pair at the volatility the forecast expects, scaled up by 1.5.
    sessions = live.sessions_for("NIFTY", TODAY, cache_path=path)
    fc = pg.forecast(sessions, now, 0)
    sigma = 1.5 * math.sqrt(fc.variance / years)
    call = bs_price("Call", 25_000, 25_000, years, sigma, pg.RISK_FREE, pg.RISK_FREE)
    put = bs_price("Put", 25_000, 25_000, years, sigma, pg.RISK_FREE, pg.RISK_FREE)
    r, implied = live.live_reading(
        "NIFTY", TODAY, 25_000, [_row(25_000, call - 0.05, call + 0.05)],
        [_row(25_000, put - 0.05, put + 0.05)], now, cache_path=path)
    assert r.ratio == pytest.approx(1.5, rel=0.03)
    assert implied == pytest.approx(r.implied_move_pct ** 2 / 1e4, rel=1e-3)
    assert r.allows("sell", 1.2) and not r.allows("buy", 1.2)


def test_no_cached_history_is_no_reading(tmp_path):
    path = str(tmp_path / "cache.sqlite3")
    now = datetime.datetime.combine(TODAY, datetime.time(9, 30))
    r, _ = live.live_reading("BSESEN", TODAY, 82_000, [_row(82_000, 300, 301)],
                             [_row(82_000, 290, 291)], now, cache_path=path)
    assert r.reason == pg.REASON_NO_HISTORY and not r.allows("sell", 0.1)
