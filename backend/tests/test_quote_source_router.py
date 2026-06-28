"""Tests for quote source routing."""
from datetime import date, datetime
from unittest.mock import MagicMock, patch

from icici_breeze_backend.app.core.timezone import IST
from icici_breeze_backend.app.services.quote_source_router import (
    _cell_to_icici_row,
    _flatten_chain_side_rows,
    bhavcopy_is_fresh,
    fetch_chain_side_icici_response,
    fetch_quote_icici_response,
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


def test_flatten_chain_side_rows():
    payload = {
        "chain_rows": [
            {
                "strike_price": 23500,
                "call": {"strike_price": 23500, "ltp": 10, "total_buy_qty": 1, "total_sell_qty": 2},
                "put": {"strike_price": 23500, "ltp": 11, "total_buy_qty": 3, "total_sell_qty": 4},
            }
        ]
    }
    calls = _flatten_chain_side_rows(payload, "Call")
    puts = _flatten_chain_side_rows(payload, "Put")
    assert len(calls) == 1
    assert len(puts) == 1
    assert calls[0]["strike_price"] == 23500
    assert calls[0]["buy_sell_ratio"] == 0.5


def test_cell_to_icici_row_computes_ratio():
    row = _cell_to_icici_row(
        {
            "strike_price": 24000,
            "ltp": 5.5,
            "total_buy_qty": 10,
            "total_sell_qty": 5,
            "spot_price": 23900,
        }
    )
    assert row["buy_sell_ratio"] == 2.0
    assert row["strike_price"] == 24000


@patch("icici_breeze_backend.app.services.quote_source_router.fetch_chain_payload_routed")
def test_fetch_chain_side_uses_cache_payload(mock_payload):
    proc = MagicMock()
    mock_payload.return_value = {
        "chain_rows": [
            {
                "strike_price": 23500,
                "call": {
                    "strike_price": 23500,
                    "ltp": 12,
                    "total_buy_qty": 1,
                    "total_sell_qty": 1,
                    "spot_price": 23500,
                },
                "put": None,
            }
        ],
        "quote_source": "websocket",
    }
    out = fetch_chain_side_icici_response(proc, "u1", "NIFTY", "NFO", "09-Jun-2025", "Call")
    assert out["Status"] == 200
    assert out["quote_source"] == "websocket"
    assert len(out["Success"]) == 1
    proc._fetch_icici_chain_side_raw.assert_not_called()


@patch("icici_breeze_backend.app.services.quote_source_router._fetch_cell_from_cache")
def test_fetch_quote_returns_cached_cell(mock_cache):
    proc = MagicMock()
    mock_cache.return_value = (
        {
            "strike_price": 23500,
            "ltp": 9.5,
            "total_buy_qty": 2,
            "total_sell_qty": 4,
            "spot_price": 23480,
        },
        "bhavcopy",
    )
    out = fetch_quote_icici_response(
        proc, "u1", "NIFTY", "NFO", "09-Jun-2025", "Call", "23500"
    )
    assert out["Status"] == 200
    assert out["quote_source"] == "bhavcopy"
    assert out["Success"][0]["strike_price"] == 23500


@patch("icici_breeze_backend.app.services.quote_source_router.resolve_quote_source", return_value="icici_api")
@patch("icici_breeze_backend.app.services.quote_source_router._fetch_quote_icici_rest")
@patch("icici_breeze_backend.app.services.quote_source_router._fetch_cell_from_cache", return_value=(None, None))
def test_fetch_quote_rest_fallback(mock_cell, mock_rest, _mock_source):
    proc = MagicMock()
    mock_rest.return_value = {"Status": 200, "Success": [{"strike_price": 23500}], "quote_source": "icici_api"}
    out = fetch_quote_icici_response(
        proc, "u1", "NIFTY", "NFO", "09-Jun-2025", "Call", "23500"
    )
    assert out["Status"] == 200
    mock_rest.assert_called_once()


@patch("icici_breeze_backend.app.services.quote_source_router.resolve_quote_source", return_value="bhavcopy")
@patch("icici_breeze_backend.app.services.quote_source_router._fetch_quote_icici_rest")
@patch("icici_breeze_backend.app.services.quote_source_router._fetch_cell_from_cache", return_value=(None, None))
def test_fetch_quote_skips_rest_when_bhavcopy_active(mock_cell, mock_rest, _mock_source):
    proc = MagicMock()
    out = fetch_quote_icici_response(
        proc, "u1", "NIFTY", "NFO", "09-Jun-2025", "Call", "23500"
    )
    assert out["Status"] == 404
    assert out["quote_source"] == "bhavcopy"
    mock_rest.assert_not_called()


@patch("icici_breeze_backend.app.services.quote_source_router.resolve_quote_source", return_value="bhavcopy")
@patch("icici_breeze_backend.app.services.quote_source_router.fetch_chain_payload_routed", return_value=None)
def test_fetch_chain_side_skips_rest_when_bhavcopy_active(mock_payload, _mock_source):
    proc = MagicMock()
    out = fetch_chain_side_icici_response(proc, "u1", "NIFTY", "NFO", "09-Jun-2025", "Call")
    assert out["Status"] == 404
    assert out["quote_source"] == "bhavcopy"
    proc._fetch_icici_chain_side_raw.assert_not_called()
