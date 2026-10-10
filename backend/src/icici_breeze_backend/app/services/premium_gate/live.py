"""Live inputs for the premium gate (docs/premium-gate-plan.md section 2).

* **The index's recent sessions** come from the history cache's one-minute cash-index bars
  (`spot_candles`), the same table the bot backtests read spot from, so a live reading and its
  replay start from the same bars. Missing sessions are fetched once a day per index under the
  user's limiter: about eight calls on a cold cache, one a day after that.
* **The ATM pair** comes from chain rows the bot has already read, priced at the mid of a live,
  two-sided book no wider than 10%. A stand-in price is not a reading.

The forecast depends only on past sessions, so it is computed once per index per day.
"""
from __future__ import annotations

import datetime
import logging
import threading
from typing import Any, Optional, Sequence

from icici_breeze_backend.app.services.premium_gate import reading as pg

_logger = logging.getLogger(__name__)

#: Calendar days searched for LONG_SESSIONS + 1 sessions: a month and a half covers holidays.
_LOOKBACK_DAYS = 45
#: History calls one day's fetch may spend per index: ~21 sessions at ICICI's 1,000-bar cap.
_MAX_CALLS = 10
#: A two-sided book wider than this (ask - bid over mid) is not a price to read volatility from.
MAX_REL_SPREAD = 0.10

_lock = threading.Lock()
_sessions_cache: dict[tuple[str, datetime.date], list[pg.SessionVol]] = {}
_fetch_tried: set[tuple[str, datetime.date]] = set()


def _holidays() -> set[datetime.date]:
    from icici_breeze_backend.app.services.bots.backtest_service import holidays

    return holidays()


def needed_sessions(today: datetime.date, holidays: Optional[set[datetime.date]] = None) -> list[datetime.date]:
    """The trading days whose bars a forecast for `today` reads, oldest first."""
    from icici_breeze_backend.app.services.bots.scalping import backtest_regime as regime

    days = regime.trading_days(
        today - datetime.timedelta(days=_LOOKBACK_DAYS), today - datetime.timedelta(days=1),
        holidays if holidays is not None else _holidays(),
    )
    return days[-(pg.LONG_SESSIONS + 1):]


def sessions_after(today: datetime.date, expiry: datetime.date,
                   holidays: Optional[set[datetime.date]] = None) -> int:
    """Trading sessions after today up to and including expiry day: 0 on expiry day."""
    from icici_breeze_backend.app.services.bots.scalping import backtest_regime as regime

    if expiry <= today:
        return 0
    return len(regime.trading_days(today + datetime.timedelta(days=1), expiry,
                                   holidays if holidays is not None else _holidays()))


def _fetch_missing(index: str, days: list[datetime.date], user_id: str, cache_path: Optional[str]) -> int:
    from icici_breeze_backend.app.core.timezone import now_ist
    from icici_breeze_backend.app.services.bots import backtest_jobs as jobs
    from icici_breeze_backend.app.services.bots.scalping import backtest_store as store
    from icici_breeze_backend.app.services.bots.scalping.backtest_fetch import Fetcher
    from icici_breeze_backend.app.services.processor import processor

    if not days or not jobs.broker_live():
        return 0
    sdk = processor().get_session_breeze(user_id)
    if sdk is None:
        return 0
    fetcher = Fetcher(sdk, holidays=_holidays(), max_calls=_MAX_CALLS, path=cache_path, log=_logger.info)
    try:
        with jobs._broker_scope(user_id):  # noqa: SLF001 -- the shared advisory limiter scope
            fetcher.fetch_spot(index, min(days), max(days))
    except Exception:  # noqa: BLE001 -- a failed fetch only means no reading today
        _logger.warning("premium gate: fetching %s index bars %s..%s failed", index, min(days),
                        max(days), exc_info=True)
    finally:
        if fetcher.calls:
            store.add_calls(now_ist().date(), fetcher.calls, path=cache_path)
    return fetcher.calls


def sessions_for(
    index: str,
    today: datetime.date,
    *,
    user_id: Optional[str] = None,
    cache_path: Optional[str] = None,
    holidays: Optional[set[datetime.date]] = None,
) -> list[pg.SessionVol]:
    """The complete sessions before `today` for one index ("NIFTY" or "BSESEN"). With a
    `user_id`, sessions the cache lacks are fetched first -- once a day, whatever the outcome."""
    key = (index, today)
    with _lock:
        cached = _sessions_cache.get(key)
    if cached is not None:
        return cached
    from icici_breeze_backend.app.services.bots.scalping import backtest_store as store

    days = needed_sessions(today, holidays)
    if not days:
        return []
    store.ensure_tables(cache_path)
    if user_id is not None and key not in _fetch_tried:
        counts = store.day_bar_counts(stock_code=index, table="spot_candles", path=cache_path)
        missing = [d for d in days if counts.get(d, 0) < store.COMPLETE_DAY_BARS]
        with _lock:
            _fetch_tried.add(key)
        if missing:
            _fetch_missing(index, missing, user_id, cache_path)
    bars = store.load_candles(stock_code=index, from_date=days[0], to_date=days[-1],
                              path=cache_path, table="spot_candles")
    sessions = pg.session_vols(bars)
    if len(sessions) >= pg.MIN_SESSIONS:
        with _lock:
            _sessions_cache.clear() if len(_sessions_cache) > 16 else None
            _sessions_cache[key] = sessions
    return sessions


def reset() -> None:
    """Forget cached sessions and fetch attempts (tests, and a cache wipe from Storage)."""
    with _lock:
        _sessions_cache.clear()
        _fetch_tried.clear()


# --------------------------------------------------------------------------------------
# The ATM pair
# --------------------------------------------------------------------------------------


def _f(value: Any) -> Optional[float]:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def _mid(row: Optional[dict[str, Any]]) -> Optional[float]:
    from icici_breeze_backend.app.services.quote_source_router import row_is_live

    if row is None or not row_is_live(row):
        return None
    bid, ask = _f(row.get("best_bid_price")), _f(row.get("best_offer_price"))
    if bid is None or ask is None or ask < bid:
        return None
    mid = (bid + ask) / 2.0
    return mid if (ask - bid) / mid <= MAX_REL_SPREAD else None


def _strike(row: dict[str, Any]) -> Optional[float]:
    from icici_breeze_backend.app.core.strike import parse_strike

    s = parse_strike(row.get("strike_price"))
    return float(s) if s is not None else None


def atm_pair(
    call_rows: Sequence[dict[str, Any]], put_rows: Sequence[dict[str, Any]], spot: float
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """(strike, call mid, put mid) at the listed strike nearest spot that both sides list."""
    calls = {k: r for r in call_rows if (k := _strike(r)) is not None}
    puts = {k: r for r in put_rows if (k := _strike(r)) is not None}
    common = sorted(set(calls) & set(puts))
    if not common or not spot > 0:
        return None, None, None
    strike = min(common, key=lambda k: (abs(k - spot), k))
    return strike, _mid(calls[strike]), _mid(puts[strike])


def live_reading(
    index: str,
    expiry: datetime.date,
    spot: float,
    call_rows: Sequence[dict[str, Any]],
    put_rows: Sequence[dict[str, Any]],
    now: datetime.datetime,
    *,
    user_id: Optional[str] = None,
    cache_path: Optional[str] = None,
) -> tuple[pg.Reading, Optional[float]]:
    """(reading, implied total variance) for one index and expiry, from chain rows already read.
    The variance is returned so a strike set "by implied move" uses the very figure the gate did."""
    from icici_breeze_backend.app.services.condor.pricing import years_to_expiry_close

    try:
        holidays = _holidays()
        sessions = sessions_for(index, now.date(), user_id=user_id, cache_path=cache_path,
                                holidays=holidays)
        fc = pg.forecast(sessions, now.replace(tzinfo=None), sessions_after(now.date(), expiry, holidays))
    except Exception:  # noqa: BLE001 -- a broken history read is no reading, never a trade
        _logger.warning("premium gate: forecast for %s failed", index, exc_info=True)
        fc = None
    strike, call, put = atm_pair(call_rows, put_rows, spot)
    implied, why = (None, pg.REASON_NO_ATM_QUOTES) if strike is None else pg.implied_variance(
        call, put, strike, spot, years_to_expiry_close(expiry, now))
    return pg.reading(implied, fc, why), implied
