"""India NSE/BSE market hours. Used to block integration tests during trading."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
# NSE/BSE regular market: 9:15 AM - 3:30 PM IST, Mon-Fri
MARKET_OPEN_HOUR = 9
MARKET_OPEN_MINUTE = 15
MARKET_CLOSE_HOUR = 15
MARKET_CLOSE_MINUTE = 30


def get_reference_time_for_iv_ist(now: datetime | None = None) -> datetime:
    """
    Reference time for IV calculation (IST): now if market is open,
    else previous market close (3:30 PM IST on the last trading day).
    When market is closed, option LTP is stale so T is measured from last close to expiry LTT.
    """
    dt = (now or datetime.now(IST)).astimezone(IST)
    if is_india_market_open(dt):
        return dt
    close_today = dt.replace(
        hour=MARKET_CLOSE_HOUR,
        minute=MARKET_CLOSE_MINUTE,
        second=0,
        microsecond=0,
        tzinfo=IST,
    )
    if dt.weekday() < 5:  # Mon–Fri
        if dt >= close_today:
            return close_today
        prev = dt - timedelta(days=1)
        while prev.weekday() >= 5:
            prev -= timedelta(days=1)
        return prev.replace(
            hour=MARKET_CLOSE_HOUR,
            minute=MARKET_CLOSE_MINUTE,
            second=0,
            microsecond=0,
            tzinfo=IST,
        )
    # Weekend: last close = Friday 3:30 PM
    prev = dt
    while prev.weekday() >= 5:
        prev -= timedelta(days=1)
    return prev.replace(
        hour=MARKET_CLOSE_HOUR,
        minute=MARKET_CLOSE_MINUTE,
        second=0,
        microsecond=0,
        tzinfo=IST,
    )


def is_india_market_open(now: datetime | None = None) -> bool:
    """
    Return True if India NSE/BSE market is open (9:15 AM - 3:30 PM IST, Mon-Fri).
    Returns False on weekends and outside trading hours.
    """
    dt = (now or datetime.now(IST)).astimezone(IST)
    if dt.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    open_time = dt.replace(
        hour=MARKET_OPEN_HOUR,
        minute=MARKET_OPEN_MINUTE,
        second=0,
        microsecond=0,
    )
    close_time = dt.replace(
        hour=MARKET_CLOSE_HOUR,
        minute=MARKET_CLOSE_MINUTE,
        second=0,
        microsecond=0,
    )
    return open_time <= dt < close_time
