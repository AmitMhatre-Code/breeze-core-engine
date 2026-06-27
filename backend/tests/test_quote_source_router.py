"""Tests for quote source routing."""
from datetime import date, datetime
from unittest.mock import patch

from icici_breeze_backend.app.core.timezone import IST
from icici_breeze_backend.app.services.quote_source_router import (
    bhavcopy_is_fresh,
    latest_concluded_trading_day,
    resolve_quote_source,
)


@patch("icici_breeze_backend.app.services.quote_source_router.is_india_market_open", return_value=True)
def test_resolve_quote_source_market_open(_mock_open):
    assert resolve_quote_source("NFO") == "websocket"


@patch("icici_breeze_backend.app.services.quote_source_router.is_india_market_open", return_value=False)
@patch("icici_breeze_backend.app.services.quote_source_router.bhavcopy_is_fresh", return_value=True)
def test_resolve_quote_source_bhavcopy(_fresh, _open):
    assert resolve_quote_source("NFO") == "bhavcopy"


@patch("icici_breeze_backend.app.services.quote_source_router.is_india_market_open", return_value=False)
@patch("icici_breeze_backend.app.services.quote_source_router.bhavcopy_is_fresh", return_value=False)
def test_resolve_quote_source_api_fallback(_fresh, _open):
    assert resolve_quote_source("NFO") == "icici_api"


def test_latest_concluded_trading_day_after_close():
    # Saturday -> previous Friday
    sat = datetime(2026, 6, 27, 18, 0, tzinfo=IST)
    d = latest_concluded_trading_day(sat)
    assert isinstance(d, date)
