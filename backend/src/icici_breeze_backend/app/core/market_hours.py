"""India NSE/BSE market hours and trading calendar."""
import os
from datetime import date, datetime, timedelta

from icici_breeze_backend.app.core.exchange_calendar import get_holiday_name, is_exchange_holiday
from icici_breeze_backend.app.core.timezone import IST

# NSE/BSE regular market: 9:15 AM - 3:30 PM IST, Mon-Fri excluding exchange holidays
MARKET_OPEN_HOUR = 9
MARKET_OPEN_MINUTE = 15
MARKET_CLOSE_HOUR = 15
MARKET_CLOSE_MINUTE = 30


def _open_time(dt: datetime) -> datetime:
    return dt.replace(
        hour=MARKET_OPEN_HOUR,
        minute=MARKET_OPEN_MINUTE,
        second=0,
        microsecond=0,
    )


def _close_time(dt: datetime) -> datetime:
    return dt.replace(
        hour=MARKET_CLOSE_HOUR,
        minute=MARKET_CLOSE_MINUTE,
        second=0,
        microsecond=0,
    )


def is_india_trading_day(now: datetime | None = None) -> bool:
    """True on weekdays that are not NSE/BSE exchange holidays."""
    dt = (now or datetime.now(IST)).astimezone(IST)
    if dt.weekday() >= 5:
        return False
    return not is_exchange_holiday(dt.date())


def _market_hours_override() -> bool | None:
    """Dev-only forced open/closed state, for local testing without waiting for real market hours.

    Only consulted by callers using the real wall clock (`now is None`) --
    callers that pass an explicit `now` (unit tests, IV reference-time math
    walking other days) are never affected, so this can't make those
    deterministic call sites env-dependent.
    """
    raw = os.environ.get("MARKET_HOURS_OVERRIDE", "").strip().lower()
    if raw in ("open", "live", "1", "true"):
        return True
    if raw in ("closed", "close", "off_market", "0", "false"):
        return False
    return None


def market_closed_reason(now: datetime | None = None) -> str:
    """Human-readable reason the market is closed at the given IST instant."""
    if now is None:
        override = _market_hours_override()
        if override is True:
            return "market open"
        if override is False:
            return "after market close (simulated via MARKET_HOURS_OVERRIDE)"
    dt = (now or datetime.now(IST)).astimezone(IST)
    if dt.weekday() >= 5:
        return "weekend"
    holiday = get_holiday_name(dt.date())
    if holiday:
        return f"exchange holiday ({holiday})"
    if dt < _open_time(dt):
        return "before market open (9:15 AM IST)"
    if dt >= _close_time(dt):
        return "after market close (3:30 PM IST)"
    return "market open"


def is_india_market_open(now: datetime | None = None) -> bool:
    """
    Return True if India NSE/BSE market is open (9:15 AM - 3:30 PM IST on trading days).
    """
    if now is None:
        override = _market_hours_override()
        if override is not None:
            return override
    dt = (now or datetime.now(IST)).astimezone(IST)
    if not is_india_trading_day(dt):
        return False
    return _open_time(dt) <= dt < _close_time(dt)


def _previous_trading_day(d: date) -> date:
    prev = d - timedelta(days=1)
    while not is_india_trading_day(datetime(prev.year, prev.month, prev.day, 12, 0, tzinfo=IST)):
        prev -= timedelta(days=1)
    return prev


def latest_opened_trading_day(now: datetime | None = None) -> date:
    """Most recent trading day whose session has already opened as of `now`.

    Used to detect stale EOD (bhavcopy) prices: once a session opens, that
    day's prices are known to have moved, regardless of whether it has
    closed yet.
    """
    dt = (now or datetime.now(IST)).astimezone(IST)
    if is_india_trading_day(dt) and dt >= _open_time(dt):
        return dt.date()
    return _previous_trading_day(dt.date())


def bhavcopy_is_stale(bhavcopy_date: date | None, now: datetime | None = None) -> bool:
    """True once a trading session has opened after `bhavcopy_date` — the EOD
    prices are known to no longer reflect the live market."""
    if bhavcopy_date is None:
        return False
    return latest_opened_trading_day(now) > bhavcopy_date


def get_reference_time_for_iv_ist(now: datetime | None = None) -> datetime:
    """
    Reference time for IV calculation (IST): now if market is open,
    else previous market close (3:30 PM IST on the last trading day).
    When market is closed, option LTP is stale so T is measured from last close to expiry LTT.
    """
    dt = (now or datetime.now(IST)).astimezone(IST)
    if is_india_market_open(dt):
        return dt
    close_today = _close_time(dt)
    if is_india_trading_day(dt) and dt >= close_today:
        return close_today
    prev_day = _previous_trading_day(dt.date())
    return datetime(
        prev_day.year,
        prev_day.month,
        prev_day.day,
        MARKET_CLOSE_HOUR,
        MARKET_CLOSE_MINUTE,
        0,
        0,
        tzinfo=IST,
    )
