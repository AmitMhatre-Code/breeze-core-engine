"""Global (single source of truth) NSE/BSE market calendar.

Backed by the `exchange_calendar` singleton table (see
`app/repositories/exchange_calendar.py`), seeded from bundled defaults
(9:15-15:30 IST + `backend/data/exchange_holidays.json`) but fully
operator-editable via Settings -> Exchange Calendar. There is no exchange
API for one-off special sessions (e.g. Muhurat trading), so this DB-backed
value — not a hardcoded constant — is what every part of the backend must
consult. See docs/design-decisions.md for the full rationale.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Mapping

from icici_breeze_backend.app.core.timezone import IST
from icici_breeze_backend.app.repositories import exchange_calendar as ec_repo


@dataclass(frozen=True)
class CalendarConfig:
    open_hour: int
    open_minute: int
    close_hour: int
    close_minute: int
    holidays: Mapping[str, str]

    def is_holiday(self, d: date) -> bool:
        return d.isoformat() in self.holidays

    def holiday_name(self, d: date) -> str | None:
        return self.holidays.get(d.isoformat())

    def open_time(self, dt: datetime) -> datetime:
        return dt.replace(
            hour=self.open_hour,
            minute=self.open_minute,
            second=0,
            microsecond=0,
        )

    def close_time(self, dt: datetime) -> datetime:
        return dt.replace(
            hour=self.close_hour,
            minute=self.close_minute,
            second=0,
            microsecond=0,
        )


def get_calendar_config() -> CalendarConfig:
    row = ec_repo.get_calendar()
    return CalendarConfig(
        open_hour=row.open_hour,
        open_minute=row.open_minute,
        close_hour=row.close_hour,
        close_minute=row.close_minute,
        holidays=row.holidays,
    )


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


def is_trading_day(now: datetime | None = None) -> bool:
    """True on weekdays that are not exchange holidays (per the configured calendar)."""
    cal = get_calendar_config()
    dt = (now or datetime.now(IST)).astimezone(IST)
    if dt.weekday() >= 5:
        return False
    return not cal.is_holiday(dt.date())


def is_market_open(now: datetime | None = None) -> bool:
    """Return True if the market is open per the configured calendar."""
    if now is None:
        override = _market_hours_override()
        if override is not None:
            return override
    cal = get_calendar_config()
    dt = (now or datetime.now(IST)).astimezone(IST)
    if dt.weekday() >= 5 or cal.is_holiday(dt.date()):
        return False
    return cal.open_time(dt) <= dt < cal.close_time(dt)


def market_closed_reason(now: datetime | None = None) -> str:
    """Human-readable reason the market is closed at the given IST instant."""
    if now is None:
        override = _market_hours_override()
        if override is True:
            return "market open"
        if override is False:
            return "after market close (simulated via MARKET_HOURS_OVERRIDE)"
    cal = get_calendar_config()
    dt = (now or datetime.now(IST)).astimezone(IST)
    if dt.weekday() >= 5:
        return "weekend"
    holiday = cal.holiday_name(dt.date())
    if holiday:
        return f"exchange holiday ({holiday})"
    if dt < cal.open_time(dt):
        return f"before market open ({cal.open_hour:02d}:{cal.open_minute:02d} IST)"
    if dt >= cal.close_time(dt):
        return f"after market close ({cal.close_hour:02d}:{cal.close_minute:02d} IST)"
    return "market open"


def _previous_trading_day(d: date) -> date:
    prev = d - timedelta(days=1)
    while not is_trading_day(datetime(prev.year, prev.month, prev.day, 12, 0, tzinfo=IST)):
        prev -= timedelta(days=1)
    return prev


def latest_opened_trading_day(now: datetime | None = None) -> date:
    """Most recent trading day whose session has already opened as of `now`.

    Used to detect stale EOD (bhavcopy) prices: once a session opens, that
    day's prices are known to have moved, regardless of whether it has
    closed yet.
    """
    cal = get_calendar_config()
    dt = (now or datetime.now(IST)).astimezone(IST)
    if is_trading_day(dt) and dt >= cal.open_time(dt):
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
    else previous market close on the last trading day.
    When market is closed, option LTP is stale so T is measured from last close to expiry LTT.
    """
    cal = get_calendar_config()
    dt = (now or datetime.now(IST)).astimezone(IST)
    if is_market_open(dt):
        return dt
    close_today = cal.close_time(dt)
    if is_trading_day(dt) and dt >= close_today:
        return close_today
    prev_day = _previous_trading_day(dt.date())
    return datetime(
        prev_day.year,
        prev_day.month,
        prev_day.day,
        cal.close_hour,
        cal.close_minute,
        0,
        0,
        tzinfo=IST,
    )
