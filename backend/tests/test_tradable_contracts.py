"""Tests for MarginPercentage-based tradeable contract filtering."""
from __future__ import annotations

import sqlite3

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.services.reference_data.tradable_contracts import (
    is_tradeable,
    is_tradeable_contract,
    list_tradeable_strikes,
)


def _init_db(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cfg, "DATA_PATH", str(tmp_path) + "/")
    monkeypatch.setattr(cfg, "SCRIP_DB", "scrips.sqlite3")
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
        rows = [
            ("NIFTY", "30-Jun-2026", 24000, "CE", 75, "NFO", 12),
            ("NIFTY", "30-Jun-2026", 24000, "PE", 75, "NFO", 0),
            ("NIFTY", "30-Jun-2026", 24100, "CE", 75, "NFO", 0),
            ("NIFTY", "30-Jun-2026", 24100, "PE", 75, "NFO", 0),
            ("NIFTY", "30-Jun-2026", 24200, "CE", 75, "NFO", 8),
            ("NIFTY", "30-Jun-2026", 24200, "PE", 75, "NFO", 8),
        ]
        conn.executemany(
            """
            INSERT INTO scrip_master
            (ShortName, ExpiryDate, StrikePrice, OptionType, LotSize, SegmentCode, MarginPercentage)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )


def test_is_tradeable():
    assert is_tradeable(12) is True
    assert is_tradeable(0) is False
    assert is_tradeable(None) is False


def test_list_tradeable_strikes_only_nonzero_margin(monkeypatch, tmp_path):
    _init_db(tmp_path, monkeypatch)
    strikes = list_tradeable_strikes("NIFTY", "30-Jun-2026", exchange_code=cfg.NFO)
    assert strikes == [24000.0, 24200.0]


def test_is_tradeable_contract_per_side(monkeypatch, tmp_path):
    _init_db(tmp_path, monkeypatch)
    assert is_tradeable_contract("NIFTY", "30-Jun-2026", 24000, "CE", exchange_code=cfg.NFO)
    assert not is_tradeable_contract("NIFTY", "30-Jun-2026", 24000, "PE", exchange_code=cfg.NFO)
    assert not is_tradeable_contract("NIFTY", "30-Jun-2026", 24100, "CE", exchange_code=cfg.NFO)
