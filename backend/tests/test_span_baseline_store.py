"""Tests for SPAN baseline Redis store."""
import json
import sqlite3

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.db.redis_client import get_redis
from icici_breeze_backend.app.services.nsccl_baseline import ensure_exchange_margin_baseline_table
from icici_breeze_backend.app.services.reference_data.scrip_index import (
    publish_scrip_index_from_db,
)
from icici_breeze_backend.app.services.reference_data.span_baseline_store import (
    compute_span_margin_required,
    get_span_baseline_sheet,
    is_span_baseline_cached,
    publish_span_baseline_from_db,
    resolve_margin_from_store,
)


def _seed_baseline(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cfg, "DATA_PATH", str(tmp_path) + "/")
    monkeypatch.setattr(cfg, "SCRIP_DB", "scrips.sqlite3")
    db = tmp_path / "scrips.sqlite3"
    conn = sqlite3.connect(db)
    ensure_exchange_margin_baseline_table()
    conn.execute(
        """
        INSERT OR REPLACE INTO exchange_margin_baseline (
            exchange_code, short_name, expiry_date, strike_price, option_type,
            margin_per_lot, lot_size, risk_array, source_file, source_date, source_version, refreshed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        (
            "NFO",
            "NIFTY",
            "26-Jun-2026",
            23500,
            "CE",
            1000.0,
            75,
            json.dumps([100.0] * 16),
            "test.xml",
            "20260626",
            1,
        ),
    )
    conn.commit()
    conn.close()
    get_redis()


def test_publish_and_compute_span_margin(monkeypatch, tmp_path):
    _seed_baseline(tmp_path, monkeypatch)
    ver = publish_span_baseline_from_db()
    assert ver >= 1
    assert is_span_baseline_cached("NFO")

    sheet = get_span_baseline_sheet("NFO", "NIFTY", "26-Jun-2026")
    assert sheet["found"] is True
    assert "23500:CE" in sheet["contracts"]

    out = compute_span_margin_required(sheet["contracts"], 23500, "Call", 75)
    assert out["found"] is True
    assert out["span_margin_required"] == 1000.0

    out2 = compute_span_margin_required(sheet["contracts"], 23500, "Call", 150)
    assert out2["found"] is True
    assert out2["span_margin_required"] == 2000.0

    resolved = resolve_margin_from_store("NFO", "NIFTY", "26-Jun-2026", 23500, "Call", 75)
    assert resolved["found"] is True
    assert resolved["span_margin_required"] == 1000.0


def test_fractional_strike_contract_key(monkeypatch, tmp_path):
    _seed_baseline(tmp_path, monkeypatch)
    db = tmp_path / "scrips.sqlite3"
    conn = sqlite3.connect(db)
    conn.execute(
        """
        INSERT OR REPLACE INTO exchange_margin_baseline (
            exchange_code, short_name, expiry_date, strike_price, option_type,
            margin_per_lot, lot_size, risk_array, source_file, source_date, source_version, refreshed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        (
            "NFO",
            "BANKINDIA",
            "30-Jun-2026",
            150.35,
            "PE",
            500.0,
            675,
            json.dumps([50.0] * 16),
            "test.xml",
            "20260625",
            1,
        ),
    )
    conn.commit()
    conn.close()

    publish_span_baseline_from_db()
    sheet = get_span_baseline_sheet("NFO", "BANKINDIA", "30-Jun-2026")
    assert sheet["found"] is True
    assert "150.35:PE" in sheet["contracts"]

    out = compute_span_margin_required(sheet["contracts"], 150.35, "Put", 675)
    assert out["found"] is True
    assert out["span_margin_required"] == 500.0


def _seed_scrip_master_stock(tmp_path) -> None:
    """One ICICI contract for ADANIENSOL, whose ICICI ShortName shares nothing with its NSE
    symbol -- the shape every stock has and the one the alias table never covered."""
    conn = sqlite3.connect(tmp_path / "scrips.sqlite3")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS scrip_master (
            ShortName TEXT, CompanyName TEXT, ExpiryDate TEXT, ExchangeCode TEXT,
            StrikePrice REAL, SegmentCode TEXT, LotSize INTEGER, OptionType TEXT,
            MarginPercentage REAL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO scrip_master (
            ShortName, CompanyName, ExpiryDate, ExchangeCode,
            StrikePrice, SegmentCode, LotSize, OptionType, MarginPercentage
        ) VALUES ('ADATRA', 'ADANI ENERGY SOLUTIONS LIMITED', '29-Sep-2026', 'ADANIENSOL',
                  1820, 'NFO', 675, 'CE', 22.5)
        """
    )
    conn.commit()
    conn.close()
    publish_scrip_index_from_db()


def test_stock_sheet_resolves_through_exchange_ticker(monkeypatch, tmp_path):
    """SPAN keys the sheet on the NSE symbol; Strategy Builder asks with the ICICI code."""
    _seed_baseline(tmp_path, monkeypatch)
    _seed_scrip_master_stock(tmp_path)
    conn = sqlite3.connect(tmp_path / "scrips.sqlite3")
    conn.execute(
        """
        INSERT OR REPLACE INTO exchange_margin_baseline (
            exchange_code, short_name, expiry_date, strike_price, option_type,
            margin_per_lot, lot_size, risk_array, source_file, source_date, source_version, refreshed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        (
            "NFO",
            "ADANIENSOL",
            "29-Sep-2026",
            1820,
            "CE",
            60682.5,
            675,
            json.dumps([-89.9] * 16),
            "nsccl.20260907.i1.zip:nsccl.20260907.i01.spn",
            "20260907",
            1,
        ),
    )
    conn.commit()
    conn.close()
    publish_span_baseline_from_db()

    sheet = get_span_baseline_sheet("NFO", "ADATRA", "29-Sep-2026")
    assert sheet["found"] is True
    assert "1820:CE" in sheet["contracts"]

    resolved = resolve_margin_from_store(
        exchange_code="NFO",
        stock_code="ADATRA",
        expiry_display="29-Sep-2026",
        strike_price=1820,
        right="Call",
        quantity=675,
    )
    assert resolved["found"] is True
    assert resolved["span_margin_required"] == 60682.5


def test_unknown_stock_still_misses(monkeypatch, tmp_path):
    """The bridge must not turn a genuinely absent contract into a false hit."""
    _seed_baseline(tmp_path, monkeypatch)
    _seed_scrip_master_stock(tmp_path)
    publish_span_baseline_from_db()

    assert get_span_baseline_sheet("NFO", "ADATRA", "29-Sep-2026")["found"] is False
