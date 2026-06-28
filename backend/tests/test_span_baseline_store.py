"""Tests for SPAN baseline Redis store."""
import sqlite3

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.db.redis_client import get_redis
from icici_breeze_backend.app.services.nsccl_baseline import ensure_exchange_margin_baseline_table
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
            margin_per_lot, lot_size, source_file, source_date, source_version, refreshed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        ("NFO", "NIFTY", "26-Jun-2026", 23500, "CE", 1000.0, 75, "test.xml", "20260626", 1),
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
