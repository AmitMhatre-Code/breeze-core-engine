"""Aggressive limit order placement (Breeze order_type=market, price=0).

ICICI has no native market-order support yet, but the feature ships enabled via the
ICICI-independent "limit_tolerance" mode; AGGRESSIVE_LIMIT_ORDER_ENABLED (see core/config.py)
is the master gate. These tests cover both states: gate disabled and gate enabled.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from icici_breeze_backend.app.domain.order import BreakOrderChunkRequest
from icici_breeze_backend.app.services import processor as processor_module
from icici_breeze_backend.app.services.processor import processor


@pytest.fixture
def proc(monkeypatch):
    mock_breeze = MagicMock()
    mock_breeze.place_order.return_value = {
        "Status": 200,
        "Success": {"order_id": "123"},
        "Error": None,
    }
    p = processor()
    monkeypatch.setattr(p, "get_session_breeze", lambda _uid: mock_breeze)
    monkeypatch.setattr(p, "_maybe_evict_session", lambda *_a, **_k: None)
    return p, mock_breeze


def test_place_order_aggressive_limit_rejected_when_gate_disabled(proc, monkeypatch):
    """With the master gate off, aggressive_limit requests are rejected."""
    monkeypatch.setattr(processor_module.cfg, "AGGRESSIVE_LIMIT_ORDER_ENABLED", False)
    p, mock_breeze = proc
    resp = p.place_order(
        "user1",
        "options",
        "NIFTY",
        "buy",
        "24000",
        "call",
        "150.50",
        "27-Feb-2025",
        "75",
        exchange_code="NFO",
        aggressive_limit=True,
    )
    mock_breeze.place_order.assert_not_called()
    assert resp["Status"] != 200
    assert "disabled" in resp["Error"].lower()


def test_place_order_aggressive_limit_uses_market_and_zero_price(proc, monkeypatch):
    p, mock_breeze = proc
    monkeypatch.setattr(processor_module.cfg, "AGGRESSIVE_LIMIT_ORDER_ENABLED", True)
    p.place_order(
        "user1",
        "options",
        "NIFTY",
        "buy",
        "24000",
        "call",
        "150.50",
        "27-Feb-2025",
        "75",
        exchange_code="NFO",
        aggressive_limit=True,
    )
    mock_breeze.place_order.assert_called_once()
    kwargs = mock_breeze.place_order.call_args.kwargs
    assert kwargs["order_type"] == "market"
    assert kwargs["price"] == "0"


def test_place_order_limit_unchanged(proc):
    p, mock_breeze = proc
    p.place_order(
        "user1",
        "options",
        "NIFTY",
        "buy",
        "24000",
        "call",
        "150.50",
        "27-Feb-2025",
        "75",
        exchange_code="NFO",
        aggressive_limit=False,
    )
    kwargs = mock_breeze.place_order.call_args.kwargs
    assert kwargs["order_type"] == "limit"
    assert kwargs["price"] == "150.50"


def test_break_order_place_chunk_aggressive_limit_rejected_when_gate_disabled(proc, monkeypatch):
    monkeypatch.setattr(processor_module.cfg, "AGGRESSIVE_LIMIT_ORDER_ENABLED", False)
    p, mock_breeze = proc
    result = p.break_order_place_chunk(
        "user1",
        "NIFTY",
        "27-Feb-2025",
        "options",
        "call",
        "24000",
        "75",
        "150.50",
        "buy",
        exchange_code="NFO",
        chunk_index=0,
        aggressive_limit=True,
    )
    mock_breeze.place_order.assert_not_called()
    assert result["success"] is False
    assert "disabled" in result["terminal_messages"][0]["message"].lower()


def test_break_order_aggressive_limit_rejected_when_gate_disabled(proc, monkeypatch):
    monkeypatch.setattr(processor_module.cfg, "AGGRESSIVE_LIMIT_ORDER_ENABLED", False)
    p, mock_breeze = proc
    msgs = p.break_order(
        "user1",
        "NIFTY",
        "27-Feb-2025",
        "options",
        "call",
        "24000",
        "75",
        "150.50",
        "buy",
        exchange_code="NFO",
        aggressive_limit=True,
    )
    mock_breeze.place_order.assert_not_called()
    assert "disabled" in msgs[0]["message"].lower()


def test_break_order_finalize_messages_aggressive_limit_when_gate_disabled(proc, monkeypatch):
    """With the gate off, finalize messaging falls back to premium wording (flag ignored)."""
    monkeypatch.setattr(processor_module.cfg, "AGGRESSIVE_LIMIT_ORDER_ENABLED", False)
    p, _ = proc
    msgs = p.break_order_finalize_user_messages(
        "NIFTY-27-Feb-2025-24000-Call",
        10.0,
        [75],
        [],
        aggressive_limit=True,
    )
    assert "premium" in msgs[0]["message"].lower()
    assert "aggressive limit" not in msgs[0]["message"].lower()


def test_break_order_finalize_messages_aggressive_limit(proc, monkeypatch):
    p, _ = proc
    monkeypatch.setattr(processor_module.cfg, "AGGRESSIVE_LIMIT_ORDER_ENABLED", True)
    msgs = p.break_order_finalize_user_messages(
        "NIFTY-27-Feb-2025-24000-Call",
        0.0,
        [75],
        [],
        aggressive_limit=True,
    )
    assert len(msgs) == 1
    assert msgs[0]["type"] == "alert-success"
    assert "aggressive limit" in msgs[0]["message"].lower()
    assert "premium" not in msgs[0]["message"].lower()


def test_break_order_finalize_messages_limit_includes_premium(proc):
    p, _ = proc
    msgs = p.break_order_finalize_user_messages(
        "NIFTY-27-Feb-2025-24000-Call",
        10.0,
        [75],
        [],
        aggressive_limit=False,
    )
    assert "premium" in msgs[0]["message"].lower()


def test_break_order_chunk_request_accepts_aggressive_limit():
    body = BreakOrderChunkRequest(
        product_type="Options",
        stock_code="NIFTY",
        exchange_code="NFO",
        expiry_date="27-Feb-2025",
        right="Call",
        strike_price="24000",
        total_qty="75",
        price="0",
        action="Buy",
        chunk_index=0,
        aggressive_limit=True,
    )
    assert body.aggressive_limit is True
