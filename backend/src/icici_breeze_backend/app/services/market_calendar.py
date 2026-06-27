"""User-scoped and system-default market calendar resolution."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Mapping

from icici_breeze_backend.app.core.exchange_calendar import _load_holidays, clear_holiday_cache
from icici_breeze_backend.app.core.timezone import IST
from icici_breeze_backend.app.repositories import user_exchange_calendar as uec_repo

DEFAULT_OPEN_HOUR = 9
DEFAULT_OPEN_MINUTE = 15
DEFAULT_CLOSE_HOUR = 15
DEFAULT_CLOSE_MINUTE = 30


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


def system_default_calendar() -> CalendarConfig:
    from icici_breeze_backend.app.core.exchange_calendar import _load_holidays

    return CalendarConfig(
        open_hour=DEFAULT_OPEN_HOUR,
        open_minute=DEFAULT_OPEN_MINUTE,
        close_hour=DEFAULT_CLOSE_HOUR,
        close_minute=DEFAULT_CLOSE_MINUTE,
        holidays=_load_holidays(),
    )


def get_user_calendar_config(user_id: str) -> CalendarConfig:
    row = uec_repo.get_user_calendar(user_id)
    return CalendarConfig(
        open_hour=row.open_hour,
        open_minute=row.open_minute,
        close_hour=row.close_hour,
        close_minute=row.close_minute,
        holidays=row.holidays,
    )


def is_trading_day(calendar: CalendarConfig, now: datetime | None = None) -> bool:
    dt = (now or datetime.now(IST)).astimezone(IST)
    if dt.weekday() >= 5:
        return False
    return not calendar.is_holiday(dt.date())


def is_market_open(user_id: str, now: datetime | None = None) -> bool:
    cal = get_user_calendar_config(user_id)
    dt = (now or datetime.now(IST)).astimezone(IST)
    if not is_trading_day(cal, dt):
        return False
    return cal.open_time(dt) <= dt < cal.close_time(dt)


def market_closed_reason(user_id: str, now: datetime | None = None) -> str:
    cal = get_user_calendar_config(user_id)
    dt = (now or datetime.now(IST)).astimezone(IST)
    if dt.weekday() >= 5:
        return "weekend"
    name = cal.holiday_name(dt.date())
    if name:
        return f"exchange holiday ({name})"
    if dt < cal.open_time(dt):
        return f"before market open ({cal.open_hour:02d}:{cal.open_minute:02d} IST)"
    if dt >= cal.close_time(dt):
        return f"after market close ({cal.close_hour:02d}:{cal.close_minute:02d} IST)"
    return "market open"


def _previous_trading_day(calendar: CalendarConfig, d: date) -> date:
    prev = d - timedelta(days=1)
    while not is_trading_day(
        calendar, datetime(prev.year, prev.month, prev.day, 12, 0, tzinfo=IST)
    ):
        prev -= timedelta(days=1)
    return prev


def get_reference_time_for_iv_ist_user(
    user_id: str, now: datetime | None = None
) -> datetime:
    cal = get_user_calendar_config(user_id)
    dt = (now or datetime.now(IST)).astimezone(IST)
    if is_market_open(user_id, dt):
        return dt
    close_today = cal.close_time(dt)
    if is_trading_day(cal, dt) and dt >= close_today:
        return close_today
    prev_day = _previous_trading_day(cal, dt.date())
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


# System-default helpers (bundled JSON) for callers without user context.
def is_system_market_open(now: datetime | None = None) -> bool:
    from icici_breeze_backend.app.core import market_hours as mh

    return mh.is_india_market_open(now)


def system_market_closed_reason(now: datetime | None = None) -> str:
    from icici_breeze_backend.app.core import market_hours as mh

    return mh.market_closed_reason(now)
