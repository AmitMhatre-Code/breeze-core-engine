"""India NSE/BSE market hours and trading calendar."""
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


def market_closed_reason(now: datetime | None = None) -> str:
    """Human-readable reason the market is closed at the given IST instant."""
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
    dt = (now or datetime.now(IST)).astimezone(IST)
    if not is_india_trading_day(dt):
        return False
    return _open_time(dt) <= dt < _close_time(dt)


def _previous_trading_day(d: date) -> date:
    prev = d - timedelta(days=1)
    while not is_india_trading_day(datetime(prev.year, prev.month, prev.day, 12, 0, tzinfo=IST)):
        prev -= timedelta(days=1)
    return prev


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
