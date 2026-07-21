"""India (NSE) wall clock: use for trading-day and broker-facing calendar logic.

Servers often run in UTC (e.g. AWS). Do not use naive ``datetime.now()`` / ``date.today()``
for those cases — use the helpers below.
"""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def now_ist() -> datetime:
    """Current time in Asia/Kolkata (timezone-aware)."""
    return datetime.now(IST)


def today_ist_date() -> date:
    """Current calendar date in Asia/Kolkata."""
    return now_ist().date()


def now_ist_naive() -> datetime:
    """Naive datetime whose components are the IST wall-clock (for legacy naive APIs)."""
    return now_ist().replace(tzinfo=None)


def ist_timestamp() -> str:
    """Now, as the `YYYY-MM-DD HH:MM:SS` string every stored timestamp column uses.

    This is the replacement for SQLite's ``CURRENT_TIMESTAMP``, which is UTC. The format
    is deliberately byte-identical to what ``CURRENT_TIMESTAMP`` produced, so stored rows
    keep the same shape and every reader that slices a date out of one (``raw[:10]``)
    keeps working — and starts being right, because the date is now the IST one.
    """
    return now_ist().strftime("%Y-%m-%d %H:%M:%S")


#: SQL expression producing the same value as :func:`ist_timestamp`, for the few places
#: that need it inside a statement (column ``DEFAULT``s, set-based migration UPDATEs)
#: rather than as a bound parameter. Prefer binding :func:`ist_timestamp` where you can.
SQLITE_NOW_IST = "datetime('now', '+5 hours', '+30 minutes')"
