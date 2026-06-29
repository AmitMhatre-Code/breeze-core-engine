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
    raw = _load_fixture("nifty_call_25000_raw.json")
    raw = dict(raw)
    raw["exchange"] = "BSE Futures & Options"
    parsed = parse_icici_tick(raw)
    assert parsed is not None
    assert parsed.exchange_code == cfg.BFO
