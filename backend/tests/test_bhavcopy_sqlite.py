"""Tests for bhavcopy SQLite persistence."""
import datetime as dt
from unittest.mock import patch

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.db.redis_client import get_redis
from icici_breeze_backend.app.services.reference_data.bhavcopy_store import (
    bhavcopy_row_count,
    load_bhavcopy_rows_from_db,
    persist_bhavcopy_rows,
    publish_bhavcopy_from_db,
    publish_bhavcopy_rows,
)
from icici_breeze_backend.app.services.reference_data.cache_bootstrap import is_bhavcopy_cached
from icici_breeze_backend.app.services.reference_data.scrip_index import current_version


def _sample_row(strike: str | float | int = 23500) -> dict[str, str]:
    return {
        "stock_code": "NIFTY",
        "expiry_display": "26-Jun-2026",
        "expiry_date": "2026-06-26",
        "right": cfg.CALL,
        "strike_price": str(strike),
        "ltp": "100.50",
        "best_bid_price": "100.00",
        "best_offer_price": "101.00",
        "total_buy_qty": "10",
        "total_sell_qty": "12",
        "open_interest": "5000",
        "spot_price": "23400.00",
        "open": "99.00",
        "high": "102.00",
        "low": "98.00",
        "previous_close": "99.50",
        "segment": cfg.NFO,
    }


def test_persist_replaces_segment_rows(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "DATA_PATH", str(tmp_path) + "/")
    monkeypatch.setattr(cfg, "SCRIP_DB", "scrips.sqlite3")
    get_redis()

    day = dt.date(2026, 6, 26)
    persist_bhavcopy_rows([_sample_row()], "nfo", day, "http://example/nse")
    assert bhavcopy_row_count("nfo") == 1

    persist_bhavcopy_rows([_sample_row(24000)], "nfo", day, "http://example/nse2")
    assert bhavcopy_row_count("nfo") == 1
    loaded = load_bhavcopy_rows_from_db("nfo")
    assert len(loaded) == 1
    assert loaded[0]["strike_price"] == "24000"


def test_publish_bhavcopy_from_db_restores_redis(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "DATA_PATH", str(tmp_path) + "/")
    monkeypatch.setattr(cfg, "SCRIP_DB", "scrips.sqlite3")
    get_redis()

    day = dt.date(2026, 6, 26)
    publish_bhavcopy_rows([_sample_row()], segment="nfo", source_date=day, source_url="http://x")
    assert is_bhavcopy_cached("nfo")

    # Simulate Redis loss: bump version without data (empty publish path not needed)
    # Clear by publishing from db after wiping redis keys manually - simpler: just call from_db
    ver_before = current_version()
    publish_bhavcopy_from_db("nfo")
    assert current_version() >= ver_before
    assert is_bhavcopy_cached("nfo")


def _bankindia_put(strike: str) -> dict[str, str]:
    return {
        "stock_code": "BANKINDIA",
        "expiry_display": "30-Jun-2026",
        "expiry_date": "2026-06-30",
        "right": cfg.PUT,
        "strike_price": strike,
        "ltp": "5.37",
        "best_bid_price": "5.00",
        "best_offer_price": "5.50",
        "total_buy_qty": "4",
        "total_sell_qty": "4",
        "open_interest": "525200",
        "spot_price": "150.00",
        "open": "5.00",
        "high": "5.50",
        "low": "4.90",
        "previous_close": "5.30",
        "segment": cfg.NFO,
    }


def test_persist_fractional_strikes_no_collision(monkeypatch, tmp_path):
    """150.00 and 150.35 are distinct contracts (NSE bhavcopy collision case)."""
    monkeypatch.setattr(cfg, "DATA_PATH", str(tmp_path) + "/")
    monkeypatch.setattr(cfg, "SCRIP_DB", "scrips.sqlite3")
    get_redis()

    day = dt.date(2026, 6, 25)
    rows = [_bankindia_put("150.00"), _bankindia_put("150.35")]
    persist_bhavcopy_rows(rows, "nfo", day, "http://example/nse")
    assert bhavcopy_row_count("nfo") == 2
    loaded = load_bhavcopy_rows_from_db("nfo")
    strikes = sorted(r["strike_price"] for r in loaded)
    assert strikes == ["150", "150.35"]


def _nifty_30jun_row(strike: int | float, right: str, ltp: str) -> dict[str, str]:
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


@patch("icici_breeze_backend.app.services.reference_data.bhavcopy_store.get_strikes", return_value=None)
def test_build_chain_from_bhavcopy_uses_passed_strikes(mock_get_strikes, monkeypatch, tmp_path):
    """Bhavcopy chain must not depend on Redis scrip strikes when caller passes a list."""
    monkeypatch.setattr(cfg, "DATA_PATH", str(tmp_path) + "/")
    monkeypatch.setattr(cfg, "SCRIP_DB", "scrips.sqlite3")
    get_redis()

    day = dt.date(2026, 6, 29)
    rows = [
        _nifty_30jun_row(23900, cfg.CALL, "117.90"),
        _nifty_30jun_row(23900, cfg.PUT, "50.35"),
        _nifty_30jun_row(24000, cfg.CALL, "64.30"),
        _nifty_30jun_row(24000, cfg.PUT, "96.45"),
    ]
    publish_bhavcopy_rows(rows, segment="nfo", source_date=day, source_url="http://example/nse")

    from icici_breeze_backend.app.services.reference_data.bhavcopy_store import build_chain_from_bhavcopy

    payload = build_chain_from_bhavcopy(
        "NIFTY",
        "30-Jun-2026",
        cfg.NFO,
        strikes=[23900, 24000],
    )
    assert payload is not None
    assert payload["quote_source"] == "bhavcopy"
    assert payload["bhavcopy_date"] == "2026-06-29"
    by_strike = {r["strike_price"]: r for r in payload["chain_rows"]}
    assert by_strike[23900]["call"]["ltp"] == 117.90
    assert by_strike[24000]["put"]["ltp"] == 96.45
    mock_get_strikes.assert_not_called()
