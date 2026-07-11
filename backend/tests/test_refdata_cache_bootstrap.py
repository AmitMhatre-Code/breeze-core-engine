"""Tests for reference data cache bootstrap."""
import sqlite3

import pytest

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.db.redis_client import get_redis
from icici_breeze_backend.app.services.reference_data.cache_bootstrap import (
    ensure_all_reference_data_cached,
    is_bhavcopy_cached,
    is_scrip_cached,
    is_span_cached,
)


def _flush_refdata_keys() -> None:
    """These tests assert a clean refdata:* namespace; a real local Redis (not the
    in-process fallback) persists keys across separate pytest runs, so this must be
    flushed explicitly rather than assumed empty."""
    redis = get_redis()
    for key in redis.keys("refdata:*"):
        redis.delete(key)


@pytest.fixture(autouse=True)
def _clean_refdata_namespace():
    _flush_refdata_keys()
    yield
    _flush_refdata_keys()


def _init_db(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cfg, "DATA_PATH", str(tmp_path) + "/")
    monkeypatch.setattr(cfg, "SCRIP_DB", "scrips.sqlite3")
    db_path = cfg.DATA_PATH + cfg.SCRIP_DB
    with sqlite3.connect(db_path, timeout=30) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scrip_master (
                ShortName TEXT,
                CompanyName TEXT,
                ExpiryDate TEXT,
                ExchangeCode TEXT,
                StrikePrice REAL,
                SegmentCode TEXT,
                LotSize INTEGER,
                OptionType TEXT,
                MarginPercentage REAL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO scrip_master (
                ShortName, CompanyName, ExpiryDate, ExchangeCode,
                StrikePrice, SegmentCode, LotSize, OptionType, MarginPercentage
            )
            VALUES ('NIFTY', 'NIFTY 50', '2026-06-26', 'NIFTY', 23500, 'NFO', 75, 'CE', 12.5)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS exchange_margin_baseline (
                exchange_code TEXT NOT NULL,
                short_name TEXT NOT NULL,
                expiry_date TEXT NOT NULL,
                strike_price INTEGER NOT NULL,
                option_type TEXT NOT NULL,
                margin_per_lot REAL NOT NULL,
                lot_size INTEGER,
                source_file TEXT NOT NULL,
                source_date TEXT NOT NULL,
                source_version INTEGER NOT NULL,
                refreshed_at TEXT NOT NULL,
                PRIMARY KEY (exchange_code, short_name, expiry_date, strike_price, option_type)
            )
            """
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO exchange_margin_baseline (
                exchange_code, short_name, expiry_date, strike_price, option_type,
                margin_per_lot, lot_size, source_file, source_date, source_version, refreshed_at
            ) VALUES ('NFO', 'NIFTY', '26-Jun-2026', 23500, 'CE', 500.0, 75, 't.xml', '20260626', 1, datetime('now'))
            """
        )
        conn.commit()
    get_redis()


def test_ensure_all_reference_data_cached_publishes_scrip_and_span(monkeypatch, tmp_path):
    _init_db(tmp_path, monkeypatch)
    assert is_scrip_cached() is False
    assert is_span_cached("NFO") is False

    status = ensure_all_reference_data_cached()

    assert status.get("scrip_cached") is True
    assert status.get("span_nfo_cached") is True
    assert is_scrip_cached() is True
    assert is_span_cached("NFO") is True

    status2 = ensure_all_reference_data_cached()
    assert status2.get("scrip_cached") is True


def test_ensure_all_reference_data_cached_publishes_bhavcopy(monkeypatch, tmp_path):
    import datetime as dt

    from icici_breeze_backend.app.services.reference_data.bhavcopy_store import (
        ensure_fo_bhavcopy_table,
        persist_bhavcopy_rows,
    )

    _init_db(tmp_path, monkeypatch)
    ensure_fo_bhavcopy_table()
    day = dt.date(2026, 6, 26)
    persist_bhavcopy_rows(
        [
            {
                "stock_code": "NIFTY",
                "expiry_display": "26-Jun-2026",
                "expiry_date": "2026-06-26",
                "right": cfg.CALL,
                "strike_price": "23500",
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
        ],
        "nfo",
        day,
        "http://example/nse",
    )
    assert is_bhavcopy_cached("nfo") is False

    status = ensure_all_reference_data_cached()

    assert status.get("bhav_nfo_published") is True
    assert status.get("bhav_nfo_cached") is True
    assert is_bhavcopy_cached("nfo") is True
