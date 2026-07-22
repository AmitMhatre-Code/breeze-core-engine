"""Integration tests for chain-builder canonical cache."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.db.redis_client import cache_get_json, close_redis
from icici_breeze_backend.app.services.chain_build_service import build_canonical_chain
from icici_breeze_backend.app.services.reference_data.keys import (
    canonical_chain_key,
    ws_raw_quote_key,
)
from icici_breeze_backend.app.db.redis_client import cache_set_json
from icici_breeze_backend.app.services.reference_data.scrip_index import publish_scrip_index_from_db
from icici_breeze_backend.app.services.reference_data.ws_token_index import publish_ws_token_map_from_db


def _raw_nifty_call_25000() -> dict:
    path = Path(__file__).resolve().parent / "fixtures" / "icici_ticks" / "nifty_call_25000_raw.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _raw_bfo_call_77000() -> dict:
    path = Path(__file__).resolve().parent / "fixtures" / "icici_ticks" / "bfo_call_77000_raw.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _seed_db(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cfg, "DATA_PATH", str(tmp_path) + "/")
    monkeypatch.setattr(cfg, "SCRIP_DB", "scrips.sqlite3")
    db_path = cfg.DATA_PATH + cfg.SCRIP_DB
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE scrip_master (
                ShortName TEXT,
                ExpiryDate TEXT,
                StrikePrice REAL,
                OptionType TEXT,
                LotSize INTEGER,
                SegmentCode TEXT,
                MarginPercentage INTEGER
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE ws_token_index (
                Token INTEGER PRIMARY KEY,
                SegmentCode TEXT,
                ShortName TEXT,
                ExpiryDate TEXT,
                StrikePrice REAL,
                OptionType TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO scrip_master
            (ShortName, ExpiryDate, StrikePrice, OptionType, LotSize, SegmentCode, MarginPercentage)
            VALUES ('NIFTY', '30-Jun-2026', 25000, 'CE', 75, 'NFO', 12)
            """
        )
        conn.execute(
            """
            INSERT INTO ws_token_index
            (Token, SegmentCode, ShortName, ExpiryDate, StrikePrice, OptionType)
            VALUES (71474, 'NFO', 'NIFTY', '30-Jun-2026', 25000, 'CE')
            """
        )
    ver = publish_scrip_index_from_db()
    publish_ws_token_map_from_db(ver)


@patch("icici_breeze_backend.app.services.chain_build_service.list_tradeable_strikes", return_value=[25000.0])
def test_build_canonical_chain_from_raw_tick(mock_strikes, monkeypatch, tmp_path):
    close_redis()
    _seed_db(tmp_path, monkeypatch)
    raw = _raw_nifty_call_25000()
    cache_set_json(
        ws_raw_quote_key(cfg.NFO, 71474),
        {"received_at": 1.0, "raw": raw},
        ex=300,
    )
    payload = build_canonical_chain("NIFTY", cfg.NFO, "30-Jun-2026", lot_size=75)
    assert payload is not None
    assert payload["quote_source"] == "websocket"
    rows = {r["strike_price"]: r for r in payload["chain_rows"]}
    assert rows[25000]["call"]["ltp"] == 1.4
    cached = cache_get_json(canonical_chain_key(cfg.NFO, "NIFTY", "30-Jun-2026"))
    assert cached is not None
    assert cached["chain_rows"][0]["call"]["ltp"] == 1.4
    close_redis()


def _seed_bfo_db(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cfg, "DATA_PATH", str(tmp_path) + "/")
    monkeypatch.setattr(cfg, "SCRIP_DB", "scrips.sqlite3")
    db_path = cfg.DATA_PATH + cfg.SCRIP_DB
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE scrip_master (
                ShortName TEXT,
                ExpiryDate TEXT,
                StrikePrice REAL,
                OptionType TEXT,
                LotSize INTEGER,
                SegmentCode TEXT,
                MarginPercentage INTEGER
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE ws_token_index (
                Token INTEGER PRIMARY KEY,
                SegmentCode TEXT,
                ShortName TEXT,
                ExpiryDate TEXT,
                StrikePrice REAL,
                OptionType TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO scrip_master
            (ShortName, ExpiryDate, StrikePrice, OptionType, LotSize, SegmentCode, MarginPercentage)
            VALUES ('BSESEN', '02-Jul-2026', 77000, 'CE', 20, 'BFO', 12)
            """
        )
        conn.execute(
            """
            INSERT INTO ws_token_index
            (Token, SegmentCode, ShortName, ExpiryDate, StrikePrice, OptionType)
            VALUES (820390, 'BFO', 'BSESEN', '02-Jul-2026', 77000, 'CE')
            """
        )
    ver = publish_scrip_index_from_db()
    publish_ws_token_map_from_db(ver)


@patch("icici_breeze_backend.app.services.chain_build_service.list_tradeable_strikes", return_value=[77000.0])
def test_build_canonical_chain_bfo_symbol_only_tick(mock_strikes, monkeypatch, tmp_path):
    close_redis()
    _seed_bfo_db(tmp_path, monkeypatch)
    raw = _raw_bfo_call_77000()
    cache_set_json(
        ws_raw_quote_key(cfg.BFO, 820390),
        {"received_at": 1.0, "raw": raw},
        ex=300,
    )
    payload = build_canonical_chain("BSESEN", cfg.BFO, "02-Jul-2026", lot_size=20)
    assert payload is not None
    assert payload["quote_source"] == "websocket"
    rows = {r["strike_price"]: r for r in payload["chain_rows"]}
    assert rows[77000]["call"]["ltp"] == 227.35
    cached = cache_get_json(canonical_chain_key(cfg.BFO, "BSESEN", "02-Jul-2026"))
    assert cached is not None
    assert cached["chain_rows"][0]["call"]["ltp"] == 227.35
    close_redis()
