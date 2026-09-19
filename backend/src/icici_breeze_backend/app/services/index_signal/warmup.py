"""The bars before today that the live engines start from (decision 15).

A replay warms each series on the sessions before its range. For a live reading to equal its
replay, the live engines must start from those same bars, so each trading day they are rebuilt
from the history cache -- never from whatever the process happened to see yesterday, which can
differ from ICICI's record wherever a tick went missing.

What carries over is only ever a *size* ranking: expansion's move and volume percentiles and
momentum's volume rank. Levels (EMA, VWAP, a window's anchor price) are rebuilt from today's bars,
and no window may span the overnight break, so a gap at the open is never read as a move.

When the cache lacks the last two sessions, they are fetched: about one call per index a day,
under the user's limiter and marked advisory (#24). It is the only history call made during
market hours; the backtest fetcher still refuses 09:00-15:45 (#36).
"""
from __future__ import annotations

import datetime
import logging
from typing import Optional

from icici_breeze_backend.app.services.index_signal import bars as bars_mod
from icici_breeze_backend.app.services.index_signal.bars import Bar
from icici_breeze_backend.app.services.index_signal.mechanisms import STOCK_CODES

_logger = logging.getLogger(__name__)

WARMUP_SESSIONS = 2
# Enough calendar days to find two sessions across a long weekend and a holiday.
_LOOKBACK_DAYS = 12
# History calls one warm-up fetch may spend (two sessions fit in one call per index).
_MAX_CALLS = 4


def _holidays() -> set[datetime.date]:
    from icici_breeze_backend.app.services.bots.backtest_service import holidays

    return holidays()


def previous_sessions(
    today: datetime.date, n: int = WARMUP_SESSIONS, holidays: Optional[set[datetime.date]] = None
) -> list[datetime.date]:
    """The `n` trading days before `today`, oldest first."""
    from icici_breeze_backend.app.services.bots.scalping import backtest_regime as regime

    days = regime.trading_days(
        today - datetime.timedelta(days=_LOOKBACK_DAYS), today - datetime.timedelta(days=1),
        holidays if holidays is not None else _holidays(),
    )
    return days[-n:]


def missing_sessions(
    index: str,
    today: datetime.date,
    *,
    cache_path: Optional[str] = None,
    holidays: Optional[set[datetime.date]] = None,
) -> list[datetime.date]:
    from icici_breeze_backend.app.services.bots.scalping import backtest_store as store

    store.ensure_tables(cache_path)
    counts = store.day_bar_counts(stock_code=STOCK_CODES[index], table="futures_candles", path=cache_path)
    return [
        d for d in previous_sessions(today, holidays=holidays)
        if counts.get(d, 0) < store.COMPLETE_DAY_BARS
    ]


def cached_bars(
    index: str,
    today: datetime.date,
    *,
    cache_path: Optional[str] = None,
    holidays: Optional[set[datetime.date]] = None,
) -> list[Bar]:
    """The cached bars of the last two sessions before today, oldest first.

    Two sessions cover every window a series keeps (expansion ranks the last 120 windows; a
    15-minute momentum candle's volume is ranked against the last 20), so the engines reach the
    same state as a replay warmed on ten days -- at a fraction of the work."""
    from icici_breeze_backend.app.services.bots.scalping import backtest_store as store

    sessions = previous_sessions(today, holidays=holidays)
    if not sessions:
        return []
    store.ensure_tables(cache_path)
    candles = store.load_candles(
        stock_code=STOCK_CODES[index],
        from_date=sessions[0],
        to_date=today - datetime.timedelta(days=1),
        path=cache_path,
    )
    return [bars_mod.from_hist(c) for c in candles]


def fetch_missing(index: str, days: list[datetime.date], user_id: str) -> int:
    """Fetch the missing warm-up sessions into the history cache. Returns ICICI calls spent.

    Live broker only; a failure is logged and costs only a slower warm-up."""
    if not days:
        return 0
    from icici_breeze_backend.app.core.timezone import now_ist
    from icici_breeze_backend.app.services.bots import backtest_jobs as jobs
    from icici_breeze_backend.app.services.bots.scalping import backtest_store as store
    from icici_breeze_backend.app.services.bots.scalping.backtest_fetch import Fetcher
    from icici_breeze_backend.app.services.processor import processor

    if not jobs.broker_live():
        return 0
    sdk = processor().get_session_breeze(user_id)
    if sdk is None:
        return 0
    fetcher = Fetcher(sdk, holidays=_holidays(), max_calls=_MAX_CALLS, log=_logger.info)
    try:
        with jobs._broker_scope(user_id):  # noqa: SLF001 -- the shared advisory limiter scope
            fetcher.fetch_futures(STOCK_CODES[index], min(days), max(days))
    except Exception:  # noqa: BLE001 -- a failed warm-up only delays the first reading
        _logger.warning("signal warm-up: fetching %s %s..%s failed", index, min(days), max(days),
                        exc_info=True)
    finally:
        if fetcher.calls:
            store.add_calls(now_ist().date(), fetcher.calls)
    return fetcher.calls
