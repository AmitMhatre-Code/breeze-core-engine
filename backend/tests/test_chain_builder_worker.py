"""Integration tests for chain-builder canonical cache."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from unittest.mock import patch

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.db.redis_client import cache_get_json, close_redis
from icici_breeze_backend.app.services.chain_build_service import (
    _normalize_and_cache_cell,
    build_canonical_chain,
)
from icici_breeze_backend.app.services.reference_data.keys import (
    canonical_chain_key,
    ws_quote_key,
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
        {"received_at": time.time(), "raw": raw},
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
        {"received_at": time.time(), "raw": raw},
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


# --- B-04: a rebuild must not make an old tick look freshly quoted -----------------------


def _store_nifty_tick(age_seconds: float) -> float:
    received_at = time.time() - age_seconds
    cache_set_json(
        ws_raw_quote_key(cfg.NFO, 71474),
        {"received_at": received_at, "raw": _raw_nifty_call_25000()},
        ex=300,
    )
    return received_at


def _build_nifty_cell() -> dict | None:
    return _normalize_and_cache_cell(
        exchange_code=cfg.NFO,
        stock_code="NIFTY",
        expiry_display="30-Jun-2026",
        strike=25000.0,
        right="call",
        lot_size=75,
    )


def test_cell_keeps_the_time_its_tick_arrived(monkeypatch, tmp_path):
    close_redis()
    _seed_db(tmp_path, monkeypatch)
    monkeypatch.setattr(cfg, "WS_RAW_QUOTE_TTL_SECONDS", 120)
    received_at = _store_nifty_tick(110)

    cell = _build_nifty_cell()

    assert cell is not None
    assert cell["updated_at"] == received_at
    key = ws_quote_key(cfg.NFO, "NIFTY", "30-Jun-2026", 25000.0, "call")
    assert cache_get_json(key)["updated_at"] == received_at
    close_redis()


def test_cell_lives_only_as_long_as_its_tick(monkeypatch, tmp_path):
    close_redis()
    _seed_db(tmp_path, monkeypatch)
    monkeypatch.setattr(cfg, "WS_RAW_QUOTE_TTL_SECONDS", 120)
    _store_nifty_tick(110)
    ttls: list[int] = []
    import icici_breeze_backend.app.services.chain_build_service as cbs

    real_set = cbs.cache_set_json
    monkeypatch.setattr(
        cbs, "cache_set_json", lambda key, value, ex=None: (ttls.append(ex), real_set(key, value, ex=ex))[1]
    )

    _build_nifty_cell()
    _build_nifty_cell()

    # 10 s of life left, and a second rebuild does not extend it.
    assert len(ttls) == 2 and all(1 <= ttl <= 10 for ttl in ttls)
    close_redis()


def test_tick_past_its_life_builds_no_cell(monkeypatch, tmp_path):
    close_redis()
    _seed_db(tmp_path, monkeypatch)
    monkeypatch.setattr(cfg, "WS_RAW_QUOTE_TTL_SECONDS", 120)
    _store_nifty_tick(121)
    stats: dict[str, int] = {}

    cell = _normalize_and_cache_cell(
        exchange_code=cfg.NFO, stock_code="NIFTY", expiry_display="30-Jun-2026",
        strike=25000.0, right="call", lot_size=75, stats=stats,
    )

    assert cell is None
    assert stats == {"stale_raw": 1}
    key = ws_quote_key(cfg.NFO, "NIFTY", "30-Jun-2026", 25000.0, "call")
    assert cache_get_json(key) is None
    close_redis()


def test_pubsub_loop_resubscribes_after_a_redis_error(monkeypatch):
    """One Redis error used to end the pubsub thread, leaving the worker poll-only
    until it was restarted."""
    import threading

    from icici_breeze_backend.app.db import redis_client
    from icici_breeze_backend.workers import chain_builder as worker

    stop = threading.Event()
    attempts = []

    def listen():
        attempts.append(1)
        if len(attempts) == 1:
            raise ConnectionError("Connection reset by peer")
        stop.set()

    monkeypatch.setattr(worker, "_stop", stop)
    monkeypatch.setattr(worker, "_PUBSUB_RETRY_INITIAL_SECONDS", 0.01)
    monkeypatch.setattr(worker, "_pubsub_listen", listen)
    monkeypatch.setattr(redis_client, "redis_using_memory_fallback", lambda: False)
    worker._pubsub_loop()
    assert len(attempts) == 2
