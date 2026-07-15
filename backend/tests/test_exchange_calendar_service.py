"""Unit tests for the global market calendar (app.services.market_calendar).

Defaults now live in the `exchange_calendar` singleton table rather than
module constants. The autouse `exchange_calendar_db` fixture in conftest.py
seeds a fresh temp DB with bundled defaults (9:15-15:30 IST +
exchange_holidays.json) before every test in the suite.
"""
from __future__ import annotations

from datetime import date, datetime

import pytest

from icici_breeze_backend.app.repositories import exchange_calendar as ec_repo
from icici_breeze_backend.app.services import market_calendar as mc
from icici_breeze_backend.app.core.timezone import IST


def _ist(y, m, d, hour=0, minute=0) -> datetime:
    return datetime(y, m, d, hour, minute, tzinfo=IST)


def test_weekend_not_trading_day():
    assert mc.is_trading_day(_ist(2026, 6, 27, 10, 0)) is False  # Saturday
    assert mc.is_market_open(_ist(2026, 6, 27, 10, 0)) is False


def test_exchange_holiday_not_trading_day():
    # Muharram — 2026-06-26 is a Friday holiday in exchange_holidays.json
    assert mc.is_trading_day(_ist(2026, 6, 26, 10, 0)) is False
    assert mc.is_market_open(_ist(2026, 6, 26, 10, 0)) is False
    assert "Muharram" in mc.market_closed_reason(_ist(2026, 6, 26, 10, 0))


def test_regular_weekday_open_window():
    dt = _ist(2026, 6, 25, 10, 0)  # Thursday
    assert mc.is_trading_day(dt) is True
    assert mc.is_market_open(dt) is True
    assert mc.market_closed_reason(dt) == "market open"


def test_before_open():
    dt = _ist(2026, 6, 25, 9, 0)
    assert mc.is_trading_day(dt) is True
    assert mc.is_market_open(dt) is False
    assert "before market open" in mc.market_closed_reason(dt)


def test_after_close():
    dt = _ist(2026, 6, 25, 16, 0)
    assert mc.is_trading_day(dt) is True
    assert mc.is_market_open(dt) is False
    assert "after market close" in mc.market_closed_reason(dt)


def test_get_reference_time_skips_holiday():
    # Friday holiday → previous close should be Thursday 15:30
    ref = mc.get_reference_time_for_iv_ist(_ist(2026, 6, 26, 18, 0))
    assert ref == _ist(2026, 6, 25, 15, 30)


@pytest.mark.parametrize(
    "hour,minute,open_",
    [
        (9, 14, False),
        (9, 15, True),
        (15, 29, True),
        (15, 30, False),
    ],
)
def test_open_window_boundaries(hour, minute, open_):
    dt = _ist(2026, 6, 25, hour, minute)
    assert mc.is_market_open(dt) is open_


# Thursday 2026-06-25: regular trading day.
# Friday 2026-06-26: exchange holiday (Muharram).
# Saturday 2026-06-27: weekend.


def test_latest_opened_trading_day_during_session():
    dt = _ist(2026, 6, 25, 10, 0)
    assert mc.latest_opened_trading_day(dt) == date(2026, 6, 25)


def test_latest_opened_trading_day_holiday_falls_back():
    dt = _ist(2026, 6, 26, 10, 0)  # holiday, no session opens
    assert mc.latest_opened_trading_day(dt) == date(2026, 6, 25)


def test_latest_opened_trading_day_weekend_falls_back():
    dt = _ist(2026, 6, 27, 10, 0)
    assert mc.latest_opened_trading_day(dt) == date(2026, 6, 25)


def test_bhavcopy_is_stale_none_date():
    assert mc.bhavcopy_is_stale(None, _ist(2026, 6, 25, 10, 0)) is False


def test_bhavcopy_is_stale_same_session_not_stale():
    dt = _ist(2026, 6, 25, 10, 0)
    assert mc.bhavcopy_is_stale(date(2026, 6, 25), dt) is False


def test_bhavcopy_is_stale_once_market_reopens():
    dt = _ist(2026, 6, 25, 10, 0)  # Thursday session open
    assert mc.bhavcopy_is_stale(date(2026, 6, 24), dt) is True


def test_bhavcopy_is_stale_weekend_not_yet_stale():
    # Saturday: bhavcopy from Thursday's close hasn't been superseded yet.
    dt = _ist(2026, 6, 27, 10, 0)
    assert mc.bhavcopy_is_stale(date(2026, 6, 25), dt) is False


def test_bhavcopy_is_stale_holiday_not_yet_stale():
    # Friday holiday: market hasn't opened, Thursday's bhavcopy still current.
    dt = _ist(2026, 6, 26, 10, 0)
    assert mc.bhavcopy_is_stale(date(2026, 6, 25), dt) is False


def test_market_open_respects_custom_hours():
    ec_repo.save_calendar(
        open_hour=10,
        open_minute=0,
        close_hour=16,
        close_minute=0,
        holidays={},
        source="local",
    )
    open_dt = _ist(2026, 6, 25, 10, 30)
    closed_dt = _ist(2026, 6, 25, 9, 30)
    assert mc.is_market_open(open_dt) is True
    assert mc.is_market_open(closed_dt) is False


def test_market_open_respects_custom_holiday():
    # A hand-entered special session/holiday (e.g. Muhurat trading eve)
    # not present in the bundled defaults.
    ec_repo.save_calendar(
        open_hour=9,
        open_minute=15,
        close_hour=15,
        close_minute=30,
        holidays={"2026-06-25": "Special closure"},
        source="local",
    )
    dt = _ist(2026, 6, 25, 11, 0)
    assert mc.is_market_open(dt) is False
    assert "Special closure" in mc.market_closed_reason(dt)


def test_market_hours_override_env_var(monkeypatch):
    monkeypatch.setenv("MARKET_HOURS_OVERRIDE", "open")
    assert mc.is_market_open() is True
    monkeypatch.setenv("MARKET_HOURS_OVERRIDE", "closed")
    assert mc.is_market_open() is False


def test_market_hours_override_ignored_with_explicit_now(monkeypatch):
    monkeypatch.setenv("MARKET_HOURS_OVERRIDE", "closed")
    dt = _ist(2026, 6, 25, 10, 0)  # would be open
    assert mc.is_market_open(dt) is True
