"""Tests for ICICI WebSocket tick parsing and normalization."""
from __future__ import annotations

import json
from pathlib import Path

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.services.ws_tick_normalize import (
    normalize_icici_tick,
    parse_icici_tick,
)

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "icici_ticks"


def _load_fixture(name: str) -> dict:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


def test_nifty_call_25000_normalization():
    raw = _load_fixture("nifty_call_25000_raw.json")
    result = normalize_icici_tick(raw, updated_at=1.0)
    assert result is not None
    parsed, cell = result
    assert parsed.exchange_code == cfg.NFO
    assert parsed.stock_code == "NIFTY"
    assert parsed.expiry_display == "30-Jun-2026"
    assert parsed.strike == 25000.0
    assert parsed.right == "call"
    assert cell["stock_code"] == "NIFTY"
    assert cell["strike_price"] == 25000.0
    assert cell["right"] == cfg.CALL
    assert cell["expiry_date"] == "30-Jun-2026"
    assert cell["ltp"] == 1.4
    assert cell["open_interest"] == 18500610
    assert cell["total_buy_qty"] == 4286295
    assert cell["total_sell_qty"] == 776425
    assert abs(cell["buy_sell_ratio"] - (4286295 / 776425)) < 1e-9
    assert cell["best_bid_price"] == 1.45
    assert cell["best_offer_price"] == 1.5
    assert cell["spot_price"] is None
    assert cell["lot_size"] is None
    assert cell["updated_at"] == 1.0


def test_nifty_call_24000_normalization():
    raw = _load_fixture("nifty_call_24000_raw.json")
    result = normalize_icici_tick(raw, updated_at=2.0)
    assert result is not None
    _, cell = result
    assert cell["ltp"] == 61.2
    assert cell["open_interest"] == 19460190
    assert cell["total_buy_qty"] == 1250405
    assert cell["total_sell_qty"] == 1641965
    assert abs(cell["buy_sell_ratio"] - (1250405 / 1641965)) < 1e-9
    assert cell["best_bid_price"] == 61.4
    assert cell["best_offer_price"] == 61.65


def test_nifty_put_25000_normalization():
    raw = _load_fixture("nifty_put_25000_raw.json")
    result = normalize_icici_tick(raw, updated_at=3.0)
    assert result is not None
    parsed, cell = result
    assert parsed.right == "put"
    assert cell["right"] == cfg.PUT
    assert cell["ltp"] == 118.25
    assert cell["open_interest"] == 5000000


def test_iso_expiry_normalized_to_display():
    raw = _load_fixture("nifty_call_25000_raw.json")
    raw = dict(raw)
    raw["expiry_date"] = "2026-06-30"
    parsed = parse_icici_tick(raw)
    assert parsed is not None
    assert parsed.expiry_display == "30-Jun-2026"


def test_malformed_ticks_return_none():
    assert parse_icici_tick(None) is None
    assert parse_icici_tick("not a dict") is None
    assert parse_icici_tick({}) is None
    assert parse_icici_tick({"stock_name": "NIFTY 50"}) is None
    assert parse_icici_tick({"stock_name": "NIFTY 50", "expiry_date": "30-Jun-2026"}) is None
    assert normalize_icici_tick({"stock_name": "", "expiry_date": "30-Jun-2026", "strike_price": "25000"}) is None


def test_bse_exchange_maps_to_bfo():
    """Field-path mapping, so the tick carries no `symbol`: when one resolves, the
    segment comes from the token index rather than this string."""
    raw = _load_fixture("nifty_call_25000_raw.json")
    raw = dict(raw)
    raw.pop("symbol", None)
    raw["exchange"] = "BSE Futures & Options"
    parsed = parse_icici_tick(raw)
    assert parsed is not None
    assert parsed.exchange_code == cfg.BFO


def _seed_token_index(monkeypatch, tmp_path, rows) -> None:
    """Point the ws_token_index lookup at a temp scrip DB holding exactly `rows`
    (Token, SegmentCode, ShortName, ExpiryDate, StrikePrice, OptionType)."""
    import sqlite3

    monkeypatch.setattr(cfg, "DATA_PATH", str(tmp_path) + "/")
    monkeypatch.setattr(cfg, "SCRIP_DB", "scrips.sqlite3")
    with sqlite3.connect(cfg.DATA_PATH + cfg.SCRIP_DB, timeout=30) as conn:
        conn.execute(
            """
            CREATE TABLE ws_token_index (
                Token INTEGER PRIMARY KEY,
                SegmentCode TEXT,
                ShortName TEXT,
                ExpiryDate DATE,
                StrikePrice REAL,
                OptionType TEXT
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO ws_token_index (Token, SegmentCode, ShortName, ExpiryDate, StrikePrice, OptionType)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    from icici_breeze_backend.app.services.reference_data.ws_token_index import clear_token_lookup_cache

    clear_token_lookup_cache()


def _seed_bfo_token_index(monkeypatch, tmp_path) -> None:
    _seed_token_index(
        monkeypatch,
        tmp_path,
        [(820390, "BFO", "BSESEN", "2026-07-02", 77000, "CE")],
    )


def test_stock_option_tick_identified_by_scrip_short_name(monkeypatch, tmp_path):
    """A single-stock tick carries the SecurityMaster *company name* ("INFOSYS LTD"),
    which is not the ShortName every chain/quote key in this app is built from. Taking
    identity from the token index instead is what stops these ticks being discarded by
    `chain_build_service._parsed_matches_contract` -- the failure that left INFTEC and
    TCS priced off the previous close all session."""
    _seed_token_index(
        monkeypatch,
        tmp_path,
        [(115689, "NFO", "INFTEC", "2026-09-29", 1140, "CE")],
    )
    raw = _load_fixture("infy_call_1140_raw.json")
    assert raw["stock_name"] == "INFOSYS LTD"

    result = normalize_icici_tick(raw, updated_at=5.0)
    assert result is not None
    parsed, cell = result
    assert parsed.exchange_code == cfg.NFO
    assert parsed.stock_code == "INFTEC"
    assert parsed.expiry_display == "29-Sep-2026"
    assert parsed.strike == 1140.0
    assert parsed.right == "call"
    assert cell["stock_code"] == "INFTEC"
    assert cell["right"] == cfg.CALL
    assert cell["ltp"] == 22.15
    assert cell["best_bid_price"] == 22.1
    assert cell["best_offer_price"] == 22.35


def test_non_nifty_index_tick_not_collapsed_to_nifty(monkeypatch, tmp_path):
    """"NIFTY BANK".split()[0] is "NIFTY", so the field path labels a BANKNIFTY tick as
    a NIFTY contract -- staging it into the P&L buffer and quote snapshot under another
    underlying's key. The token index keeps the two apart."""
    _seed_token_index(
        monkeypatch,
        tmp_path,
        [(35000, "NFO", "CNXBAN", "2026-09-29", 72600, "CE")],
    )
    raw = _load_fixture("infy_call_1140_raw.json")
    raw = dict(raw)
    raw.update(
        {
            "symbol": "4.1!35000",
            "stock_name": "NIFTY BANK",
            "expiry_date": "29-Sep-2026",
            "strike_price": "72600",
        }
    )
    parsed = parse_icici_tick(raw)
    assert parsed is not None
    assert parsed.stock_code == "CNXBAN"


def test_fields_still_parse_when_symbol_is_unknown(monkeypatch, tmp_path):
    """The field path remains the fallback: a token missing from the index (a cold or
    mid-refresh map) must still yield a usable contract rather than dropping the tick."""
    _seed_token_index(
        monkeypatch,
        tmp_path,
        [(820390, "BFO", "BSESEN", "2026-07-02", 77000, "CE")],
    )
    raw = dict(_load_fixture("nifty_call_25000_raw.json"))
    raw["symbol"] = "4.1!999999999"  # well-formed, absent from the seeded index
    parsed = parse_icici_tick(raw)
    assert parsed is not None
    assert parsed.stock_code == "NIFTY"
    assert parsed.expiry_display == "30-Jun-2026"
    assert parsed.strike == 25000.0


def test_bfo_symbol_only_tick_normalization(monkeypatch, tmp_path):
    _seed_bfo_token_index(monkeypatch, tmp_path)
    raw = _load_fixture("bfo_call_77000_raw.json")
    result = normalize_icici_tick(raw, updated_at=10.0)
    assert result is not None
    parsed, cell = result
    assert parsed.exchange_code == cfg.BFO
    assert parsed.stock_code == "BSESEN"
    assert parsed.expiry_display == "02-Jul-2026"
    assert parsed.strike == 77000.0
    assert parsed.right == "call"
    assert cell["ltp"] == 227.35
    assert cell["open_interest"] == 1547880
    assert cell["right"] == cfg.CALL


def test_symbol_prefix_exchange_mapping(monkeypatch, tmp_path):
    from icici_breeze_backend.app.services.reference_data.ws_token_index import (
        exchange_from_ws_prefix,
    )

    assert exchange_from_ws_prefix("8.1") == cfg.BFO
    assert exchange_from_ws_prefix("4.1") == cfg.NFO
