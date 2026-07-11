"""Unit tests for NSE/BSE market hours and exchange holiday calendar."""
from __future__ import annotations

from datetime import date, datetime

import pytest

from icici_breeze_backend.app.core import market_hours as mh
from icici_breeze_backend.app.core.timezone import IST


def _ist(y, m, d, hour=0, minute=0) -> datetime:
    return datetime(y, m, d, hour, minute, tzinfo=IST)


def test_weekend_not_trading_day():
    assert mh.is_india_trading_day(_ist(2026, 6, 27, 10, 0)) is False  # Saturday
    assert mh.is_india_market_open(_ist(2026, 6, 27, 10, 0)) is False


def test_exchange_holiday_not_trading_day():
    # Muharram — 2026-06-26 is a Friday holiday in exchange_holidays.json
    assert mh.is_india_trading_day(_ist(2026, 6, 26, 10, 0)) is False
    assert mh.is_india_market_open(_ist(2026, 6, 26, 10, 0)) is False
    assert "Muharram" in mh.market_closed_reason(_ist(2026, 6, 26, 10, 0))


def test_regular_weekday_open_window():
    dt = _ist(2026, 6, 25, 10, 0)  # Thursday
    assert mh.is_india_trading_day(dt) is True
    assert mh.is_india_market_open(dt) is True
    assert mh.market_closed_reason(dt) == "market open"


def test_before_open():
    dt = _ist(2026, 6, 25, 9, 0)
    assert mh.is_india_trading_day(dt) is True
    assert mh.is_india_market_open(dt) is False
    assert "before market open" in mh.market_closed_reason(dt)


def test_after_close():
    dt = _ist(2026, 6, 25, 16, 0)
    assert mh.is_india_trading_day(dt) is True
    assert mh.is_india_market_open(dt) is False
    assert "after market close" in mh.market_closed_reason(dt)


def test_get_reference_time_skips_holiday():
    # Friday holiday → previous close should be Thursday 15:30
    ref = mh.get_reference_time_for_iv_ist(_ist(2026, 6, 26, 18, 0))
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
    assert mh.is_india_market_open(dt) is open_


# Thursday 2026-06-25: regular trading day.
# Friday 2026-06-26: exchange holiday (Muharram).
# Saturday 2026-06-27: weekend.


def test_latest_opened_trading_day_during_session():
    dt = _ist(2026, 6, 25, 10, 0)
    assert mh.latest_opened_trading_day(dt) == date(2026, 6, 25)


def test_latest_opened_trading_day_holiday_falls_back():
    dt = _ist(2026, 6, 26, 10, 0)  # holiday, no session opens
    assert mh.latest_opened_trading_day(dt) == date(2026, 6, 25)


def test_latest_opened_trading_day_weekend_falls_back():
    dt = _ist(2026, 6, 27, 10, 0)
    assert mh.latest_opened_trading_day(dt) == date(2026, 6, 25)


def test_bhavcopy_is_stale_none_date():
    assert mh.bhavcopy_is_stale(None, _ist(2026, 6, 25, 10, 0)) is False


def test_bhavcopy_is_stale_same_session_not_stale():
    dt = _ist(2026, 6, 25, 10, 0)
    assert mh.bhavcopy_is_stale(date(2026, 6, 25), dt) is False


def test_bhavcopy_is_stale_once_market_reopens():
    dt = _ist(2026, 6, 25, 10, 0)  # Thursday session open
    assert mh.bhavcopy_is_stale(date(2026, 6, 24), dt) is True


def test_bhavcopy_is_stale_weekend_not_yet_stale():
    # Saturday: bhavcopy from Thursday's close hasn't been superseded yet.
    dt = _ist(2026, 6, 27, 10, 0)
    assert mh.bhavcopy_is_stale(date(2026, 6, 25), dt) is False


def test_bhavcopy_is_stale_holiday_not_yet_stale():
    # Friday holiday: market hasn't opened, Thursday's bhavcopy still current.
    dt = _ist(2026, 6, 26, 10, 0)
    assert mh.bhavcopy_is_stale(date(2026, 6, 25), dt) is False
