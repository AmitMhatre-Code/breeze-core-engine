"""Unknown depth (None) must never be read as "no market interest".

Bhavcopy carries no order-book columns and BSE wipes its book at close, so depth
is frequently genuinely unknown. Every gate that used to treat a zero as proof of
illiquidity has to distinguish that from a confirmed empty book.
"""
from __future__ import annotations

from unittest.mock import patch

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.services.chain_readiness import _cell_has_quote
from icici_breeze_backend.app.services.options_strategy_engine.helpers import quote_from_api
from icici_breeze_backend.app.services.processor import (
    _uncovered_scan_row_has_bid_side,
    processor,
)
from icici_breeze_backend.app.services.quote_source_router import (
    _apply_buy_sell_ratio,
    _cell_to_icici_row,
)
from icici_breeze_backend.app.services.reference_data.bhavcopy_store import _row_to_chain_cell


def test_bhavcopy_cell_preserves_unknown_depth():
    """Absent columns become None, not 0 -- 0 would mean "confirmed no bids"."""
    cell = _row_to_chain_cell(
        {"ltp": "95.25", "open_interest": "1200", "strike_price": "82000"},
        "SENSEX",
        "30-Jul-2026",
        cfg.BFO,
        cfg.PUT,
        20,
    )
    assert cell["total_buy_qty"] is None
    assert cell["total_sell_qty"] is None
    assert cell["best_bid_price"] is None
    assert cell["buy_sell_ratio"] is None


def test_bhavcopy_cell_keeps_real_zero_distinct_from_unknown():
    cell = _row_to_chain_cell(
        {
            "ltp": "95.25",
            "strike_price": "82000",
            "total_buy_qty": "0",
            "total_sell_qty": "0",
        },
        "SENSEX",
        "30-Jul-2026",
        cfg.BFO,
        cfg.PUT,
        20,
    )
    assert cell["total_buy_qty"] == 0
    assert cell["buy_sell_ratio"] == 0.0


def test_strategy_quote_stays_liquid_when_depth_unknown():
    """Otherwise every EOD contract would be pruned as illiquid post-close."""
    q = quote_from_api(82000.0, "Put", {"ltp": 95.25, "best_bid_price": 94.5})
    assert q.depth_known is False
    assert q.liquid is True

    dead = quote_from_api(82000.0, "Put", {"ltp": 0, "best_bid_price": 0})
    assert dead.liquid is False


def test_strategy_quote_respects_confirmed_empty_book():
    q = quote_from_api(82000.0, "Put", {"ltp": 95.25, "total_buy_qty": 0, "total_sell_qty": 0})
    assert q.depth_known is True
    assert q.liquid is False


def test_chain_readiness_accepts_quoted_price_without_depth():
    cell = {"ltp": 0, "best_bid_price": 94.5, "best_offer_price": 95.5}
    assert _cell_has_quote(cell, exchange_code=cfg.NFO) is True
    assert _cell_has_quote({"ltp": 0}, exchange_code=cfg.NFO) is False


def test_uncovered_scan_falls_through_on_unknown_depth_for_nfo():
    """Previously only BFO got price-evidence fallback; unknown depth now does
    too, on any exchange."""
    row = {"total_buy_qty": None, "best_bid_price": 94.5, "ltp": 95.0}
    assert _uncovered_scan_row_has_bid_side(row, cfg.NFO) is True
    # A confirmed zero book on NFO still disqualifies.
    assert _uncovered_scan_row_has_bid_side({"total_buy_qty": 0}, cfg.NFO) is False


def test_icici_row_and_ratio_preserve_none():
    row = _cell_to_icici_row({"strike_price": 82000.0, "ltp": 95.25})
    assert row["total_buy_qty"] is None
    assert row["buy_sell_ratio"] is None

    r = {"total_buy_qty": None, "total_sell_qty": None}
    _apply_buy_sell_ratio(r)
    assert r["buy_sell_ratio"] is None


def test_bse_post_close_rest_zeros_become_unknown():
    """The original bug: BSE reports an all-zero book after close, which read as
    "nothing trades here" for every SENSEX strike."""
    proc = processor.__new__(processor)
    raw = [
        {
            "strike_price": "82000",
            "ltp": "95.25",
            "total_buy_qty": "0",
            "total_sell_qty": "0",
            "best_bid_price": "0",
            "best_offer_price": "0",
            "open_interest": "1200",
        }
    ]
    with (
        patch(
            "icici_breeze_backend.app.services.processor.is_market_open",
            return_value=False,
        ),
        patch.object(processor, "fetch_lot_size", return_value=20),
    ):
        rows = proc._transform_icici_chain_rows(
            "SENSEX", "30-Jul-2026", cfg.BFO, cfg.PUT, raw
        )
    assert len(rows) == 1
    assert rows[0]["total_buy_qty"] is None
    assert rows[0]["best_bid_price"] is None
    assert rows[0]["buy_sell_ratio"] is None
    assert rows[0]["ltp"] == "95.25"  # the price itself is still good


def test_nfo_post_close_rest_zeros_are_left_alone():
    """NSE keeps its depth after close, so a zero there is real."""
    proc = processor.__new__(processor)
    raw = [
        {
            "strike_price": "25000",
            "ltp": "120.5",
            "total_buy_qty": "0",
            "total_sell_qty": "0",
            "open_interest": "500",
        }
    ]
    with (
        patch(
            "icici_breeze_backend.app.services.processor.is_market_open",
            return_value=False,
        ),
        patch.object(processor, "fetch_lot_size", return_value=65),
    ):
        rows = proc._transform_icici_chain_rows(
            "NIFTY", "30-Jul-2026", cfg.NFO, cfg.CALL, raw
        )
    assert rows[0]["total_buy_qty"] == 0
