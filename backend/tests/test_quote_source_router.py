"""Tests for quote source routing."""
import datetime as dt
from datetime import date, datetime
from unittest.mock import MagicMock, patch

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.timezone import IST
from icici_breeze_backend.app.db.redis_client import cache_delete_pattern, get_redis
from icici_breeze_backend.app.services.quote_source_router import (
    _cell_to_icici_row,
    _enrich_quote_metadata,
    _flatten_chain_side_rows,
    _get_or_build_icici_rest_chain,
    _max_cell_updated_at,
    _spot_from_bhavcopy,
    bhavcopy_is_fresh,
    chain_fetch_error_response,
    fetch_chain_payload_routed,
    fetch_chain_side_icici_response,
    fetch_quote_icici_response,
    latest_concluded_trading_day,
    offline_source_order,
    resolve_quote_source,
)
from icici_breeze_backend.app.services.reference_data.bhavcopy_store import publish_bhavcopy_rows
from icici_breeze_backend.app.services.reference_data.keys import icici_rest_chain_key


@patch("icici_breeze_backend.app.services.quote_source_router.is_market_open", return_value=True)
def test_resolve_quote_source_market_open(_mock_open):
    assert resolve_quote_source("NFO") == "websocket"


@patch("icici_breeze_backend.app.services.quote_source_router.snapshot_is_fresh", return_value=False)
@patch("icici_breeze_backend.app.services.quote_source_router.is_market_open", return_value=False)
@patch("icici_breeze_backend.app.services.quote_source_router.bhavcopy_is_fresh", return_value=True)
def test_resolve_quote_source_bhavcopy(_fresh, _open, _snap):
    assert resolve_quote_source("NFO") == "bhavcopy"


@patch("icici_breeze_backend.app.services.quote_source_router.snapshot_is_fresh", return_value=False)
@patch("icici_breeze_backend.app.services.quote_source_router.is_market_open", return_value=False)
@patch("icici_breeze_backend.app.services.quote_source_router.bhavcopy_is_fresh", return_value=False)
def test_resolve_quote_source_api_fallback(_fresh, _open, _snap):
    assert resolve_quote_source("NFO") == "icici_api"


@patch("icici_breeze_backend.app.services.quote_source_router.snapshot_is_fresh", return_value=True)
@patch("icici_breeze_backend.app.services.quote_source_router.is_market_open", return_value=False)
@patch("icici_breeze_backend.app.services.quote_source_router.bhavcopy_is_fresh", return_value=True)
def test_resolve_quote_source_snapshot_outranks_bhavcopy(_fresh, _open, _snap):
    """The snapshot is the only closed-market source carrying real depth."""
    assert resolve_quote_source("BFO") == "snapshot"


@patch("icici_breeze_backend.app.services.quote_source_router.snapshot_is_fresh", return_value=False)
@patch("icici_breeze_backend.app.services.quote_source_router.is_market_open", return_value=False)
@patch("icici_breeze_backend.app.services.quote_source_router.bhavcopy_is_fresh", return_value=True)
def test_offline_order_fresh_bhavcopy_never_calls_rest(_fresh, _open, _snap):
    assert offline_source_order("NFO") == ["snapshot", "bhavcopy"]


@patch("icici_breeze_backend.app.services.quote_source_router.snapshot_is_fresh", return_value=False)
@patch("icici_breeze_backend.app.services.quote_source_router.is_market_open", return_value=False)
@patch("icici_breeze_backend.app.services.quote_source_router.bhavcopy_is_fresh", return_value=False)
def test_offline_order_stale_bhavcopy_prefers_rest(_fresh, _open, _snap):
    """A stale bhavcopy is last-session data; REST at least covers this one."""
    assert offline_source_order("BFO") == ["snapshot", "icici_api", "bhavcopy"]


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


@patch(
    "icici_breeze_backend.app.services.quote_source_router.bhavcopy_is_stale",
    return_value=True,
)
def test_enrich_quote_metadata_bhavcopy_stale(mock_stale):
    payload = {
        "quote_source": "bhavcopy",
        "bhavcopy_date": "2026-06-27",
        "chain_rows": [],
    }
    out = _enrich_quote_metadata(payload)
    assert out["bhavcopy_stale"] is True
    mock_stale.assert_called_once_with(date(2026, 6, 27))


@patch(
    "icici_breeze_backend.app.services.quote_source_router.bhavcopy_is_stale",
    return_value=False,
)
def test_enrich_quote_metadata_bhavcopy_not_stale(mock_stale):
    payload = {
        "quote_source": "bhavcopy",
        "bhavcopy_date": "2026-06-27",
        "chain_rows": [],
    }
    out = _enrich_quote_metadata(payload)
    assert out["bhavcopy_stale"] is False


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


@patch("icici_breeze_backend.app.services.quote_source_router._fetch_cell_from_cache")
def test_fetch_quote_returns_rest_sourced_cell(mock_cache):
    """_fetch_cell_from_cache is the one place that decides websocket vs bhavcopy vs
    REST (icici_api); fetch_quote_icici_response just has to echo whatever it hands
    back, including the REST fallback source."""
    proc = MagicMock()
    mock_cache.return_value = (
        {
            "strike_price": 23500,
            "ltp": 9.5,
            "total_buy_qty": 0,
            "total_sell_qty": 0,
            "spot_price": 23480,
        },
        "icici_api",
    )
    out = fetch_quote_icici_response(
        proc, "u1", "NIFTY", "NFO", "09-Jun-2025", "Call", "23500"
    )
    assert out["Status"] == 200
    assert out["quote_source"] == "icici_api"
    assert out["Success"][0]["strike_price"] == 23500


@patch("icici_breeze_backend.app.services.quote_source_router.resolve_quote_source", return_value="icici_api")
@patch("icici_breeze_backend.app.services.quote_source_router._fetch_cell_from_cache", return_value=(None, None))
def test_fetch_quote_miss_reports_icici_api_source(mock_cell, _mock_source):
    """A genuine REST-fallback miss (e.g. circuit breaker open) should say so,
    not blame bhavcopy for it."""
    proc = MagicMock()
    out = fetch_quote_icici_response(
        proc, "u1", "NIFTY", "NFO", "09-Jun-2025", "Call", "23500"
    )
    assert out["Status"] == 404
    assert out["quote_source"] == "icici_api"


@patch("icici_breeze_backend.app.services.quote_source_router.resolve_quote_source", return_value="bhavcopy")
@patch("icici_breeze_backend.app.services.quote_source_router._fetch_cell_from_cache", return_value=(None, None))
def test_fetch_quote_skips_rest_when_bhavcopy_active(mock_cell, _mock_source):
    proc = MagicMock()
    out = fetch_quote_icici_response(
        proc, "u1", "NIFTY", "NFO", "09-Jun-2025", "Call", "23500"
    )
    assert out["Status"] == 404
    assert out["quote_source"] == "bhavcopy"


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
def test_fetch_chain_payload_routed_bhavcopy_with_sqlite_strikes(
    _mock_source,
    monkeypatch,
    tmp_path,
):
    """Router builds a complete Bhavcopy chain from in-memory scrip + bhavcopy mirrors."""
    monkeypatch.setattr(cfg, "DATA_PATH", str(tmp_path) + "/")
    monkeypatch.setattr(cfg, "SCRIP_DB", "scrips.sqlite3")
    get_redis()

    import sqlite3

    from icici_breeze_backend.app.services.reference_data.scrip_index import publish_scrip_index_from_db

    db_path = cfg.DATA_PATH + cfg.SCRIP_DB
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE scrip_master (
                ShortName TEXT,
                ExpiryDate TEXT,
                StrikePrice INTEGER,
                OptionType TEXT,
                LotSize INTEGER,
                SegmentCode TEXT,
                MarginPercentage INTEGER
            )
            """
        )
        for strike in (23900, 24000):
            for opt in ("CE", "PE"):
                conn.execute(
                    """
                    INSERT INTO scrip_master
                    (ShortName, ExpiryDate, StrikePrice, OptionType, LotSize, SegmentCode, MarginPercentage)
                    VALUES ('NIFTY', '30-Jun-2026', ?, ?, 65, 'NFO', 10)
                    """,
                    (strike, opt),
                )
    publish_scrip_index_from_db()

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
@patch("icici_breeze_backend.app.services.chain_readiness.is_chain_complete", return_value=True)
def test_fetch_chain_payload_routed_websocket_enriches_missing_spot(
    _mock_complete,
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


@patch("icici_breeze_backend.app.services.quote_source_router.resolve_quote_source", return_value="icici_api")
@patch("icici_breeze_backend.app.services.quote_source_router._resolve_chain_metadata")
@patch("icici_breeze_backend.app.services.quote_source_router._get_or_build_icici_rest_chain")
@patch("icici_breeze_backend.app.services.chain_readiness.is_chain_complete", return_value=True)
def test_fetch_chain_payload_routed_uses_icici_rest_fallback(
    _mock_complete,
    mock_rest_chain,
    mock_meta,
    _mock_source,
):
    """Post-close, pre-bhavcopy gap: resolve_quote_source says icici_api, and
    fetch_chain_payload_routed should build the chain via the shared REST cache
    instead of returning None."""
    mock_meta.return_value = (65, None, [24000, 24100])
    mock_rest_chain.return_value = {
        "chain_rows": [
            {"strike_price": 24000, "call": {"strike_price": 24000, "ltp": 10.0}, "put": None},
        ],
        "spot_price": 23946.25,
        "quote_source": "icici_api",
        "bhavcopy_date": None,
        "exchange_code": cfg.NFO,
        "stock_code": "NIFTY",
        "expiry_display": "30-Jun-2026",
    }
    proc = MagicMock()
    with patch(
        "icici_breeze_backend.app.services.quote_source_router._resolve_chain_spot",
        return_value=23946.25,
    ):
        payload = fetch_chain_payload_routed(proc, "u1", "NIFTY", cfg.NFO, "30-Jun-2026")
    assert payload is not None
    assert payload["quote_source"] == "icici_api"
    mock_rest_chain.assert_called_once()


@patch(
    "icici_breeze_backend.app.services.quote_source_router.list_tradeable_strikes_memory",
    return_value=[24000],
)
@patch("icici_breeze_backend.app.services.quote_source_router.is_market_open", return_value=False)
@patch("icici_breeze_backend.app.services.quote_source_router.bhavcopy_is_fresh", return_value=False)
def test_chain_fetch_error_response_reports_rest_gap(_bhav, _open, _strikes):
    """When source is icici_api and the REST attempt itself failed, the error
    should say so instead of blaming a missing bhavcopy load."""
    out = chain_fetch_error_response(cfg.NFO, "NIFTY", "30-Jun-2026")
    assert out["Status"] == 503
    assert "REST" in out["Error"]


def test_get_or_build_icici_rest_chain_shares_cache_across_users():
    """One REST fetch per (exchange, stock, expiry) should serve every user for
    the rest of the post-close gap -- a second caller must hit the cache, not
    fire a second REST call pair."""
    stock = "RESTCACHETEST"
    expiry = "30-Jun-2026"
    cache_delete_pattern(icici_rest_chain_key(cfg.NFO, stock, expiry))

    proc = MagicMock()
    proc._get_full_option_chain_icici_rest.return_value = {
        "Status": 200,
        "Error": None,
        "Success": {
            "chain_rows": [{"strike_price": 24000, "call": {"ltp": 10.0}, "put": None}],
            "spot_price": 23980,
            "atm_strike": 24000,
        },
    }

    payload1 = _get_or_build_icici_rest_chain(
        proc, "u1", stock, cfg.NFO, expiry, [24000], lot_size=65, freeze_quantity=None
    )
    assert payload1 is not None
    assert payload1["quote_source"] == "icici_api"
    proc._get_full_option_chain_icici_rest.assert_called_once()

    payload2 = _get_or_build_icici_rest_chain(
        proc, "u2", stock, cfg.NFO, expiry, [24000], lot_size=65, freeze_quantity=None
    )
    assert payload2 is not None
    assert payload2["chain_rows"] == payload1["chain_rows"]
    # Second user hit the shared cache -- no second REST call pair.
    proc._get_full_option_chain_icici_rest.assert_called_once()


@patch(
    "icici_breeze_backend.app.services.reference_data.tradable_contracts.is_tradeable_contract",
    return_value=True,
)
def test_get_full_option_chain_icici_rest_keeps_illiquid_but_quoted_strikes(_mock_tradeable, monkeypatch):
    """After market close there is no live order-book depth even for strikes with a
    perfectly good closing LTP -- the REST chain builder must not drop those rows
    the way the old buy/sell-qty-based illiquidity filter did."""
    from icici_breeze_backend.app.services.processor import processor

    p = processor()
    monkeypatch.setattr(p, "fetch_lot_size", lambda *a, **k: 65)
    monkeypatch.setattr(p, "fetch_qty_limits", lambda *a, **k: None)

    def fake_chain_side_raw(user_id, stock_code, exchange_code, expiry_api, right):
        ltp = 10.5 if right == cfg.CALL else 8.2
        rows = [
            {
                "strike_price": "24000",
                "ltp": ltp,
                "total_buy_qty": 0,
                "total_sell_qty": 0,
                "spot_price": 23980,
                "open_interest": 100,
            }
        ]
        return {"Status": 200, "Success": rows, "Error": None}

    monkeypatch.setattr(p, "_fetch_icici_chain_side_raw", fake_chain_side_raw)

    result = p._get_full_option_chain_icici_rest(
        "u1", "NIFTY", cfg.NFO, "30-Jun-2026", strikes=[24000]
    )
    assert result["Status"] == 200
    chain_rows = result["Success"]["chain_rows"]
    assert len(chain_rows) == 1
    row = chain_rows[0]
    assert row["call"] is not None and row["call"]["ltp"] == 10.5
    assert row["put"] is not None and row["put"]["ltp"] == 8.2
    assert result["Success"]["quote_source"] == "icici_api"


def _bhav_row_with_spot(stock_code: str, strike: int, right: str, spot: str, exchange_code: str) -> dict[str, str]:
    return {
        "stock_code": stock_code,
        "expiry_display": "16-Jul-2026",
        "expiry_date": "2026-07-16",
        "right": right,
        "strike_price": str(strike),
        "ltp": "10.0",
        "best_bid_price": "10.0",
        "best_offer_price": "10.0",
        "total_buy_qty": "1",
        "total_sell_qty": "1",
        "open_interest": "100",
        "spot_price": spot,
        "open": "10.0",
        "high": "10.0",
        "low": "10.0",
        "previous_close": "10.0",
        "segment": exchange_code,
    }


def test_spot_from_bhavcopy_scans_past_the_lowest_few_strikes():
    """Deep extremes of a thin chain (e.g. Sensex weeklies) routinely have no
    bhavcopy row at all -- the lookup must keep scanning instead of giving up
    after a handful of the lowest strikes, or `is_chain_complete` falls back to
    requiring every tradeable strike to tick live (see chain_readiness.py),
    which a less liquid chain may never satisfy."""
    day = dt.date(2026, 7, 15)
    publish_bhavcopy_rows(
        [_bhav_row_with_spot("BSESEN", 83000, cfg.CALL, "82500.0", cfg.BFO)],
        segment="bfo",
        source_date=day,
        source_url="http://example/bse",
    )
    # First 20 strikes (deep ITM/OTM, far from spot) have no bhavcopy row at all;
    # only the 21st (nearest to the actual spot) does.
    strikes = list(range(60000, 60000 + 20 * 100, 100)) + [83000]
    spot = _spot_from_bhavcopy("BSESEN", "16-Jul-2026", cfg.BFO, strikes)
    assert spot == 82500.0


@patch(
    "icici_breeze_backend.app.services.reference_data.tradable_contracts.is_tradeable_contract",
    return_value=True,
)
def test_get_full_option_chain_icici_rest_builds_full_skeleton(_mock_tradeable, monkeypatch):
    """Every strike passed in gets a chain_row, even when ICICI returned nothing
    for it -- chain_readiness.is_chain_complete requires the full skeleton."""
    from icici_breeze_backend.app.services.processor import processor

    p = processor()
    monkeypatch.setattr(p, "fetch_lot_size", lambda *a, **k: 65)
    monkeypatch.setattr(p, "fetch_qty_limits", lambda *a, **k: None)

    def fake_chain_side_raw(user_id, stock_code, exchange_code, expiry_api, right):
        # ICICI only returned data for strike 24000, not 24100.
        rows = [
            {
                "strike_price": "24000",
                "ltp": 10.0,
                "total_buy_qty": 1,
                "total_sell_qty": 1,
                "spot_price": 23980,
                "open_interest": 100,
            }
        ]
        return {"Status": 200, "Success": rows, "Error": None}

    monkeypatch.setattr(p, "_fetch_icici_chain_side_raw", fake_chain_side_raw)

    result = p._get_full_option_chain_icici_rest(
        "u1", "NIFTY", cfg.NFO, "30-Jun-2026", strikes=[24000, 24100]
    )
    chain_rows = result["Success"]["chain_rows"]
    strikes_seen = {r["strike_price"] for r in chain_rows}
    assert strikes_seen == {24000, 24100}
    missing_row = next(r for r in chain_rows if r["strike_price"] == 24100)
    assert missing_row["call"] is None
    assert missing_row["put"] is None


@patch("icici_breeze_backend.app.services.quote_source_router.resolve_quote_source", return_value="snapshot")
@patch("icici_breeze_backend.app.services.quote_source_router._resolve_chain_metadata")
@patch(
    "icici_breeze_backend.app.services.quote_source_router._resolve_chain_spot",
    return_value=76391.39,
)
@patch(
    "icici_breeze_backend.app.services.quote_source_router.offline_source_order",
    return_value=["snapshot"],
)
@patch("icici_breeze_backend.app.services.quote_source_router.is_tradeable_contract", return_value=True)
@patch("icici_breeze_backend.app.services.quote_source_router.build_chain_from_snapshot")
def test_atm_gated_serves_ws_snapshot_post_close(
    mock_snapshot,
    _mock_tradeable,
    _mock_order,
    _mock_spot,
    mock_meta,
    _mock_source,
):
    """Regression: post-close the captured WS snapshot is the primary source. The
    payoff/dashboard ATM-gated path must build from it, not fall through to a
    spurious "Bhavcopy not loaded yet" error. Before the fix `_fetch_chain_payload_atm_gated`
    had no `snapshot` branch, so a `source == "snapshot"` resolution returned None."""
    from icici_breeze_backend.app.services.quote_source_router import (
        assemble_payoff_quote_with_router,
    )
    from icici_breeze_backend.app.services.ws_quote_snapshot import contract_field

    atm = 76400
    mock_meta.return_value = (20, None, [76300, atm])
    mock_snapshot.return_value = {
        contract_field(atm, "call"): {
            "strike_price": atm,
            "ltp": 12.5,
            "open_interest": 100,
            "updated_at": 1.0,
        },
        contract_field(atm, "put"): {
            "strike_price": atm,
            "ltp": 8.0,
            "open_interest": 90,
            "updated_at": 1.0,
        },
    }

    out = assemble_payoff_quote_with_router(
        MagicMock(), "u1", "BSESEN", cfg.BFO, "23-Jul-2026"
    )

    assert out["Status"] == 200
    payload = out["Success"]
    assert payload["quote_source"] == "snapshot"
    assert payload["atm_strike"] == atm
    assert _find_chain_row_strike(payload, atm) is not None
    mock_snapshot.assert_called_once()


def _find_chain_row_strike(payload, strike):
    return next(
        (r for r in payload.get("chain_rows", []) if r.get("strike_price") == strike),
        None,
    )


def _bsesen_bhav_row(strike, right, ltp):
    return {
        "stock_code": "BSESEN",
        "expiry_display": "27-Aug-2026",
        "right": right,
        "strike_price": str(strike),
        "ltp": ltp,
        "best_bid_price": ltp,
        "best_offer_price": ltp,
        "total_buy_qty": "10",
        "total_sell_qty": "12",
        "open_interest": "5000",
        "spot_price": "82000.0",
        "segment": cfg.BFO,
    }


@patch("icici_breeze_backend.app.services.index_spot_feed.ensure_underlying_spot_subscription")
@patch(
    "icici_breeze_backend.app.services.breeze_websocket_manager.ensure_chain_subscriptions",
    return_value=None,
)
@patch(
    "icici_breeze_backend.app.services.quote_source_router._resolve_chain_spot",
    return_value=82000.0,
)
@patch("icici_breeze_backend.app.services.quote_source_router._resolve_chain_metadata")
@patch("icici_breeze_backend.app.services.chain_readiness.list_tradeable_strikes_memory")
@patch("icici_breeze_backend.app.services.chain_readiness.is_tradeable_contract", return_value=True)
@patch("icici_breeze_backend.app.services.quote_source_router.is_tradeable_contract", return_value=True)
@patch(
    "icici_breeze_backend.app.services.quote_source_router.offline_source_order",
    return_value=["bhavcopy"],
)
@patch(
    "icici_breeze_backend.app.services.quote_source_router.resolve_quote_source",
    return_value="websocket",
)
def test_offline_fallback_survives_untraded_wings(
    _mock_source,
    _mock_order,
    _mock_tradeable_router,
    _mock_tradeable_readiness,
    mock_strikes_memory,
    mock_meta,
    _mock_spot,
    _mock_ws,
    _mock_spot_sub,
    monkeypatch,
):
    """Regression (far-dated BSESEN): the websocket gate can never pass on a thin
    chain whose deep wings don't trade at all, and the offline fallback used to
    emit rows only for the strikes bhavcopy could fill -- which then failed
    `is_chain_complete`'s full-skeleton structural check, so the endpoint returned
    "Live option chain not ready" forever and Strategy Builder stayed locked."""
    strikes = [81000.0 + 100 * i for i in range(21)]  # ATM 82000 at index 10
    traded = set(strikes[5:16])  # only the band around ATM traded yesterday

    mock_strikes_memory.return_value = strikes
    mock_meta.return_value = (20, None, strikes)
    monkeypatch.setattr(cfg, "CHAIN_READY_ATM_STRIKE_WINDOW", 5)

    def fake_bhav_row(stock_code, expiry_display, right, strike, exchange_code):
        if strike not in traded:
            return None
        return _bsesen_bhav_row(strike, right, "120.5")

    monkeypatch.setattr(
        "icici_breeze_backend.app.services.quote_source_router._lookup_bhav_row",
        fake_bhav_row,
    )

    payload = fetch_chain_payload_routed(
        MagicMock(), "u1", "BSESEN", cfg.BFO, "27-Aug-2026"
    )

    assert payload is not None
    assert payload["quote_source"] == "bhavcopy"
    # Full skeleton: every tradeable strike is a row, untraded wings carry null cells.
    by_strike = {r["strike_price"]: r for r in payload["chain_rows"]}
    assert set(by_strike) == set(strikes)
    assert by_strike[82000.0]["call"]["ltp"] == 120.5
    assert by_strike[81000.0]["call"] is None
    assert by_strike[83000.0]["put"] is None


@patch("icici_breeze_backend.app.services.index_spot_feed.ensure_underlying_spot_subscription")
@patch(
    "icici_breeze_backend.app.services.breeze_websocket_manager.ensure_chain_subscriptions",
    return_value=None,
)
@patch(
    "icici_breeze_backend.app.services.quote_source_router._resolve_chain_spot",
    return_value=82000.0,
)
@patch("icici_breeze_backend.app.services.quote_source_router._resolve_chain_metadata")
@patch("icici_breeze_backend.app.services.chain_readiness.list_tradeable_strikes_memory")
@patch("icici_breeze_backend.app.services.chain_readiness.is_tradeable_contract", return_value=True)
@patch("icici_breeze_backend.app.services.quote_source_router.is_tradeable_contract", return_value=True)
@patch(
    "icici_breeze_backend.app.services.quote_source_router.offline_source_order",
    return_value=["bhavcopy"],
)
@patch(
    "icici_breeze_backend.app.services.quote_source_router.resolve_quote_source",
    return_value="websocket",
)
def test_offline_fallback_still_rejects_unquoted_atm_band(
    _mock_source,
    _mock_order,
    _mock_tradeable_router,
    _mock_tradeable_readiness,
    mock_strikes_memory,
    mock_meta,
    _mock_spot,
    _mock_ws,
    _mock_spot_sub,
    monkeypatch,
):
    """The skeleton must not turn the gate into a rubber stamp: a chain whose ATM
    band itself has no quotes is still incomplete."""
    strikes = [81000.0 + 100 * i for i in range(21)]
    traded = {strikes[0], strikes[20]}  # only the far wings, nothing near ATM

    mock_strikes_memory.return_value = strikes
    mock_meta.return_value = (20, None, strikes)
    monkeypatch.setattr(cfg, "CHAIN_READY_ATM_STRIKE_WINDOW", 5)

    monkeypatch.setattr(
        "icici_breeze_backend.app.services.quote_source_router._lookup_bhav_row",
        lambda stock_code, expiry_display, right, strike, exchange_code: (
            _bsesen_bhav_row(strike, right, "120.5") if strike in traded else None
        ),
    )

    assert (
        fetch_chain_payload_routed(MagicMock(), "u1", "BSESEN", cfg.BFO, "27-Aug-2026")
        is None
    )
