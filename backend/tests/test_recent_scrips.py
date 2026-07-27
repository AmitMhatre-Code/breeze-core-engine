"""Tests for the recently-traded-scrips quick-select aggregation."""
from __future__ import annotations

from unittest.mock import MagicMock

from icici_breeze_backend.app.services.recent_scrips import get_recently_traded_scrips


def _trade(stock_code: str) -> dict:
    return {"stock_code": stock_code, "action": "Buy", "quantity": "75"}


def test_ranks_by_trade_count_desc_then_alphabetical():
    rows = (
        [_trade("NIFTY")] * 3
        + [_trade("BANKNIFTY")] * 3
        + [_trade("RELIANCE")] * 5
        + [_trade("TCS")] * 1
        + [_trade("infy")]  # lowercase from broker: must normalize to INFY
    )
    processor = MagicMock()
    processor.get_trades.return_value = {"Status": 200, "Success": rows, "Error": None}

    result = get_recently_traded_scrips("U1", processor)

    assert [r["stock_code"] for r in result] == [
        "RELIANCE",
        "BANKNIFTY",
        "NIFTY",
        "INFY",
        "TCS",
    ]
    assert result[0]["trade_count"] == 5
    processor.get_trades.assert_called_once()


def test_caps_at_top_five():
    rows = []
    for i in range(8):
        rows.extend([_trade(f"SCRIP{i}")] * (8 - i))
    processor = MagicMock()
    processor.get_trades.return_value = {"Status": 200, "Success": rows, "Error": None}

    result = get_recently_traded_scrips("U1", processor)

    assert len(result) == 5
    assert [r["stock_code"] for r in result] == [
        "SCRIP0",
        "SCRIP1",
        "SCRIP2",
        "SCRIP3",
        "SCRIP4",
    ]


def test_fails_soft_on_broker_error_status():
    processor = MagicMock()
    processor.get_trades.return_value = {
        "Status": 500,
        "Success": None,
        "Error": "broken",
    }

    assert get_recently_traded_scrips("U1", processor) == []


def test_fails_soft_on_exception():
    processor = MagicMock()
    processor.get_trades.side_effect = RuntimeError("boom")

    assert get_recently_traded_scrips("U1", processor) == []


def test_empty_trade_list_returns_empty():
    processor = MagicMock()
    processor.get_trades.return_value = {"Status": 200, "Success": [], "Error": None}

    assert get_recently_traded_scrips("U1", processor) == []
