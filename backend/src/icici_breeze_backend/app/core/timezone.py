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
