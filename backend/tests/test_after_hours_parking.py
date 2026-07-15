"""After-hours order placement auto-parks instead of calling ICICI place_order."""
from __future__ import annotations

import ssl

# breeze_connect downloads SecurityMaster at import time; allow tests on MITM networks.
ssl._create_default_https_context = ssl._create_unverified_context

from unittest.mock import MagicMock

import pytest

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.domain.order import ParkedOrderListItem
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
    monkeypatch.setattr(p, "fetch_qty_limits", lambda *_a, **_k: 1800)
    monkeypatch.setattr(p, "fetch_lot_size", lambda *_a, **_k: 75)
    return p, mock_breeze


def test_break_order_place_chunk_parks_when_market_closed(proc, monkeypatch):
    p, mock_breeze = proc
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.processor.is_market_open",
        lambda *_a, **_k: False,
    )
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.processor.market_closed_reason",
        lambda *_a, **_k: "after market close (3:30 PM IST)",
    )
    parked = [
        ParkedOrderListItem(
            id="park-1",
            product_type="Options",
            stock_code="NIFTY",
            exchange_code="NFO",
            expiry_date="30-Jun-2026",
            right="Call",
            strike_price="24000",
            quantity="75",
            price="100",
            action="Buy",
        )
    ]
    monkeypatch.setattr(p, "create_parked_orders", lambda *_a, **_k: parked)

    out = p.break_order_place_chunk(
        "user1",
        "NIFTY",
        "30-Jun-2026",
        "Options",
        "Call",
        "24000",
        "75",
        "100",
        "Buy",
        "NFO",
        0,
    )

    assert out["parked_for_execution"] is True
    assert out["parked_order_ids"] == ["park-1"]
    assert out["placed_quantity"] == 0
    mock_breeze.place_order.assert_not_called()


def test_break_order_place_chunk_from_parked_no_duplicate(proc, monkeypatch):
    p, mock_breeze = proc
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.processor.is_market_open",
        lambda *_a, **_k: False,
    )
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.processor.market_closed_reason",
        lambda *_a, **_k: "after market close (3:30 PM IST)",
    )
    create = MagicMock()
    monkeypatch.setattr(p, "create_parked_orders", create)

    out = p.break_order_place_chunk(
        "user1",
        "NIFTY",
        "30-Jun-2026",
        "Options",
        "Call",
        "24000",
        "75",
        "100",
        "Buy",
        "NFO",
        0,
        from_parked_execution=True,
    )

    assert out["terminal_messages"]
    assert "remains parked" in out["terminal_messages"][0]["message"]
    create.assert_not_called()
    mock_breeze.place_order.assert_not_called()


def test_break_order_parks_when_market_closed(proc, monkeypatch):
    p, mock_breeze = proc
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.processor.is_market_open",
        lambda *_a, **_k: False,
    )
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.processor.market_closed_reason",
        lambda *_a, **_k: "weekend",
    )
    monkeypatch.setattr(p, "_park_placement_for_execution", lambda *_a, **_k: ["park-2"])

    msgs = p.break_order(
        "user1",
        "NIFTY",
        "30-Jun-2026",
        "Options",
        "Call",
        "24000",
        "75",
        "100",
        "Buy",
    )

    assert len(msgs) == 1
    assert msgs[0]["type"] == cfg.INFO
    assert "parked for execution" in msgs[0]["message"].lower()
    mock_breeze.place_order.assert_not_called()


def test_break_order_place_chunk_calls_broker_when_open(proc, monkeypatch):
    p, mock_breeze = proc
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.processor.is_market_open",
        lambda *_a, **_k: True,
    )

    out = p.break_order_place_chunk(
        "user1",
        "NIFTY",
        "30-Jun-2026",
        "Options",
        "Call",
        "24000",
        "75",
        "100",
        "Buy",
        "NFO",
        0,
    )

    assert out.get("parked_for_execution") is False
    assert out["success"] is True
    mock_breeze.place_order.assert_called_once()
