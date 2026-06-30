"""Regression: scrip_master ExpiryDate SQL uses DD-Mon-YYYY (ICICI) with ISO fallback."""
import sqlite3
import ssl

# breeze_connect downloads SecurityMaster at import time; allow tests on MITM networks.
ssl._create_default_https_context = ssl._create_unverified_context

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.services.processor import processor


def _init_scrip_db(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cfg, "DATA_PATH", str(tmp_path) + "/")
    monkeypatch.setattr(cfg, "SCRIP_DB", "scrips.sqlite3")
    db_path = cfg.DATA_PATH + cfg.SCRIP_DB
    with sqlite3.connect(db_path, timeout=30) as conn:
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
        conn.execute(
            """
            INSERT INTO scrip_master (ShortName, ExpiryDate, StrikePrice, OptionType, LotSize, SegmentCode, MarginPercentage)
            VALUES ('NIFTY', '30-Jun-2026', 24000, 'CE', 75, 'NFO', 10)
            """
        )


def test_list_option_strikes_matches_dd_mon_yyyy_in_db(monkeypatch, tmp_path):
    _init_scrip_db(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.reference_data.scrip_index.get_strikes",
        lambda *_a, **_k: None,
    )
    proc = processor()
    assert proc.list_option_strikes("NIFTY", "30-Jun-2026") == [24000.0]
    assert proc.list_option_strikes("NIFTY", "2026-06-30") == [24000.0]


def test_fetch_lot_size_matches_dd_mon_yyyy_in_db(monkeypatch, tmp_path):
    _init_scrip_db(tmp_path, monkeypatch)
    proc = processor()
    assert proc.fetch_lot_size("NIFTY", "30-Jun-2026") == 75
    assert proc.fetch_lot_size("NIFTY", "2026-06-30") == 75
