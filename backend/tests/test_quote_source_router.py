"""Tests for quote source routing."""
import datetime as dt
from datetime import date, datetime
from unittest.mock import MagicMock, patch

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.timezone import IST
from icici_breeze_backend.app.db.redis_client import get_redis
from icici_breeze_backend.app.services.quote_source_router import (
    _cell_to_icici_row,
    _enrich_quote_metadata,
    _flatten_chain_side_rows,
    _max_cell_updated_at,
    bhavcopy_is_fresh,
    fetch_chain_payload_routed,
    fetch_chain_side_icici_response,
    fetch_quote_icici_response,
    latest_concluded_trading_day,
    resolve_quote_source,
)
from icici_breeze_backend.app.services.reference_data.bhavcopy_store import publish_bhavcopy_rows


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


def test_max_cell_updated_at_picks_latest():
    rows = [
        {
            "strike_price": 23500,
            "call": {"updated_at": 1000.0},
            "put": {"updated_at": 2000.5},
        },
        {
            "strike_price": 23600,
            "call": {"updated_at": 1500.0},
            "put": None,
        },
    ]
    assert _max_cell_updated_at(rows) == 2000.5


def test_enrich_quote_metadata_websocket():
    payload = {
        "quote_source": "websocket",
        "chain_rows": [
            {
                "strike_price": 23500,
                "call": {"updated_at": 1_700_000_000.0},
                "put": None,
            }
        ],
    }
    out = _enrich_quote_metadata(payload)
    assert out["quote_as_of"] is not None
    assert "1700000000" in out["quote_as_of"] or "2023" in out["quote_as_of"]


def test_enrich_quote_metadata_bhavcopy():
    payload = {
        "quote_source": "bhavcopy",
        "bhavcopy_date": "2026-06-27",
        "chain_rows": [],
    }
    out = _enrich_quote_metadata(payload)
    assert out["quote_as_of"] == "2026-06-27"


@patch("icici_breeze_backend.app.services.quote_source_router.now_ist")
def test_enrich_quote_metadata_icici_api(mock_now):
    mock_now.return_value = datetime(2026, 6, 27, 16, 0, tzinfo=IST)
    payload = {"quote_source": "icici_api", "chain_rows": []}
    out = _enrich_quote_metadata(payload)
    assert out["quote_as_of"] == mock_now.return_value.isoformat()


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


def _nifty_bhav_row(strike: int, right: str, ltp: str) -> dict[str, str]:
    return {
        "stock_code": "NIFTY",
        "expiry_display": "30-Jun-2026",
        "expiry_date": "2026-06-30",
        "right": right,
        "strike_price": str(strike),
        "ltp": ltp,
        "best_bid_price": ltp,
        "best_offer_price": ltp,
        "total_buy_qty": "10",
        "total_sell_qty": "12",
        "open_interest": "5000",
        "spot_price": "23946.25",
        "open": ltp,
        "high": ltp,
        "low": ltp,
        "previous_close": ltp,
        "segment": cfg.NFO,
    }


@patch("icici_breeze_backend.app.services.quote_source_router.resolve_quote_source", return_value="bhavcopy")
@patch("icici_breeze_backend.app.services.reference_data.scrip_index.get_strikes", return_value=None)
def test_fetch_chain_payload_routed_bhavcopy_with_sqlite_strikes(
    _mock_get_strikes,
    _mock_source,
    monkeypatch,
    tmp_path,
):
    """Router passes scrip-master strikes into Bhavcopy build when Redis index is empty."""
    monkeypatch.setattr(cfg, "DATA_PATH", str(tmp_path) + "/")
    monkeypatch.setattr(cfg, "SCRIP_DB", "scrips.sqlite3")
    get_redis()

    day = dt.date(2026, 6, 29)
    publish_bhavcopy_rows(
        [
            _nifty_bhav_row(23900, cfg.CALL, "117.90"),
            _nifty_bhav_row(23900, cfg.PUT, "50.35"),
            _nifty_bhav_row(24000, cfg.CALL, "64.30"),
            _nifty_bhav_row(24000, cfg.PUT, "96.45"),
        ],
        segment="nfo",
        source_date=day,
        source_url="http://example/nse",
    )

    proc = MagicMock()
    proc.fetch_lot_size.return_value = 65
    proc.fetch_qty_limits.return_value = None
    proc.list_option_strikes.return_value = [23900, 24000]

    payload = fetch_chain_payload_routed(proc, "u1", "NIFTY", cfg.NFO, "30-Jun-2026")
    assert payload is not None
    assert payload["quote_source"] == "bhavcopy"
    assert payload["bhavcopy_date"] == "2026-06-29"
    by_strike = {r["strike_price"]: r for r in payload["chain_rows"]}
    assert by_strike[23900]["call"]["ltp"] == 117.90
    proc._get_full_option_chain_icici_rest.assert_not_called()


@patch("icici_breeze_backend.app.services.quote_source_router.resolve_quote_source", return_value="websocket")
@patch("icici_breeze_backend.app.services.breeze_websocket_manager.ensure_chain_subscriptions")
@patch("icici_breeze_backend.app.services.quote_source_router._resolve_chain_metadata")
def test_fetch_chain_payload_routed_websocket_enriches_missing_spot(
    mock_meta,
    mock_ws,
    _mock_source,
):
    mock_meta.return_value = (65, None, [24000, 24100])
    mock_ws.return_value = {
        "chain_rows": [
            {
                "strike_price": 24000,
                "call": {"strike_price": 24000, "ltp": 10.0, "updated_at": 1_700_000_000.0},
                "put": None,
            }
        ],
        "spot_price": None,
        "quote_source": "websocket",
        "exchange_code": cfg.NFO,
        "stock_code": "NIFTY",
        "expiry_display": "30-Jun-2026",
    }
    proc = MagicMock()
    with patch(
        "icici_breeze_backend.app.services.quote_source_router._resolve_chain_spot",
        return_value=23946.25,
    ) as mock_spot:
        payload = fetch_chain_payload_routed(proc, "u1", "NIFTY", cfg.NFO, "30-Jun-2026")
    assert payload is not None
    assert payload["quote_source"] == "websocket"
    assert payload["spot_price"] == 23946.25
    assert payload["atm_strike"] == 24000
    mock_spot.assert_called_once()


@patch("icici_breeze_backend.app.services.quote_source_router.resolve_quote_source", return_value="websocket")
@patch("icici_breeze_backend.app.services.breeze_websocket_manager.ensure_chain_subscriptions")
@patch("icici_breeze_backend.app.services.quote_source_router._resolve_chain_metadata")
def test_fetch_chain_payload_routed_websocket_enriches_missing_spot(
    mock_meta,
    mock_ws,
    _mock_source,
):
    mock_meta.return_value = (65, None, [24000, 24100])
    mock_ws.return_value = {
        "chain_rows": [
            {
                "strike_price": 24000,
                "call": {"strike_price": 24000, "ltp": 10.0, "updated_at": 1_700_000_000.0},
                "put": None,
            }
        ],
        "spot_price": None,
        "quote_source": "websocket",
        "exchange_code": cfg.NFO,
        "stock_code": "NIFTY",
        "expiry_display": "30-Jun-2026",
    }
    proc = MagicMock()
    with patch(
        "icici_breeze_backend.app.services.quote_source_router._resolve_chain_spot",
        return_value=23946.25,
    ) as mock_spot:
        payload = fetch_chain_payload_routed(proc, "u1", "NIFTY", cfg.NFO, "30-Jun-2026")
    assert payload is not None
    assert payload["quote_source"] == "websocket"
    assert payload["spot_price"] == 23946.25
    assert payload["atm_strike"] == 24000
    mock_spot.assert_called_once()
