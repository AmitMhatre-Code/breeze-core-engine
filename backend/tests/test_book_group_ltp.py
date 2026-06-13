"""Tests for fast order-book load and batched group LTP."""
from __future__ import annotations

import importlib
from unittest.mock import MagicMock, patch

import pytest

from icici_breeze_backend.app.services.broker_snapshot_cache import (
    get_snapshot,
    set_snapshot,
)


@pytest.fixture
def mock_broker_env(monkeypatch):
    monkeypatch.setenv("ICICI_BROKER_MODE", "mock")
    import icici_breeze_backend.core.config as core_config
    import icici_breeze_backend.app.core.config as app_config

    importlib.reload(core_config)
    importlib.reload(app_config)


@pytest.fixture(autouse=True)
def _clear_snapshot_cache():
    from icici_breeze_backend.app.services import broker_snapshot_cache as mod

    with mod._lock:
        mod._cache.clear()
    yield
    with mod._lock:
        mod._cache.clear()


def _sample_orders_success():
    return {
        "Status": 200,
        "Success": [
            {
                "stock_code": "NIFTY",
                "expiry_date": "16-Jun-2026",
                "strike_price": 24000,
                "right": "Call",
                "action": "Buy",
                "quantity": 50,
                "pending_quantity": 50,
                "status": "Ordered",
                "product_type": "Options",
                "exchange_code": "NFO",
            },
            {
                "stock_code": "NIFTY",
                "expiry_date": "16-Jun-2026",
                "strike_price": 24000,
                "right": "Call",
                "action": "Sell",
                "quantity": 25,
                "pending_quantity": 25,
                "status": "Ordered",
                "product_type": "Options",
                "exchange_code": "NFO",
            },
        ],
    }


def test_group_orders_fetch_ltp_false_skips_get_quote(mock_broker_env):
    from icici_breeze_backend.app.services.processor import processor

    proc = processor()
    orders = _sample_orders_success()
    with patch.object(proc, "get_quote", MagicMock()) as mock_quote:
        grouped = proc.group_orders("U1", orders, fetch_ltp=False)
    mock_quote.assert_not_called()
    assert len(grouped) == 2
    assert grouped[0]["group_ltp"] is None
    assert grouped[1]["group_ltp"] is None


def test_group_orders_fetch_ltp_true_calls_get_quote(mock_broker_env):
    from icici_breeze_backend.app.services.processor import processor

    proc = processor()
    orders = _sample_orders_success()
    with patch.object(
        proc,
        "get_quote",
        MagicMock(return_value={"Status": 200, "Success": [{"ltp": 12.5}]}),
    ) as mock_quote:
        grouped = proc.group_orders("U1", orders, fetch_ltp=True)
    assert mock_quote.call_count == 2
    assert grouped[0]["group_ltp"] == 12.5


def test_fetch_group_ltps_batch_dedupes_contract_and_buckets_chains(mock_broker_env):
    from icici_breeze_backend.app.services.processor import processor

    proc = processor()
    mock_breeze = MagicMock()
    mock_breeze.get_option_chain_quotes.return_value = {
        "Status": 200,
        "Success": [
            {"strike_price": 24000, "ltp": 101.25},
            {"strike_price": 24100, "ltp": 88.0},
        ],
    }

    groups = [
        {
            "group": "buy-g0",
            "stock_code": "NIFTY",
            "expiry_date": "16-Jun-2026",
            "strike_price": 24000,
            "right": "Call",
            "exchange_code": "NFO",
        },
        {
            "group": "sell-g1",
            "stock_code": "NIFTY",
            "expiry_date": "16-Jun-2026",
            "strike_price": 24000,
            "right": "Call",
            "exchange_code": "NFO",
        },
        {
            "group": "put-g2",
            "stock_code": "NIFTY",
            "expiry_date": "16-Jun-2026",
            "strike_price": 24100,
            "right": "Put",
            "exchange_code": "NFO",
        },
    ]

    with patch.object(proc, "get_session_breeze", return_value=mock_breeze):
        ltps = proc.fetch_group_ltps_batch("U1", groups)

    assert mock_breeze.get_option_chain_quotes.call_count == 2
    assert ltps["buy-g0"] == 101.25
    assert ltps["sell-g1"] == 101.25
    assert ltps["put-g2"] == 88.0


def test_get_book_data_skips_customer_and_margin_prechecks(mock_broker_env):
    import asyncio

    from icici_breeze_backend.app.api.v1 import route_book

    mock_proc = MagicMock()
    mock_proc.retrieve_messages.return_value = []
    mock_proc.get_orders.return_value = {"Status": 200, "Success": None}
    mock_proc.get_customer_details = MagicMock(
        side_effect=AssertionError("should not call get_customer_details")
    )
    mock_proc.get_margin_situation = MagicMock(
        side_effect=AssertionError("should not call get_margin_situation")
    )

    ctx = MagicMock()
    ctx.user_id = "U1"
    ctx.broker_token = "tok"

    with patch.object(route_book, "breeze", mock_proc):
        out = asyncio.run(route_book.get_book_data(context=ctx))

    mock_proc.get_orders.assert_called_once()
    assert out.orders_failed is False
    assert out.grouped_orders is None


def test_evict_broker_snapshot_clears_cached_margin():
    from icici_breeze_backend.app.services.processor import build_margin_situation_from_raw

    user = "U1"
    token = "tok"
    margin = build_margin_situation_from_raw(
        {"Status": 200, "Success": {"cash_limit": 100.0, "limit_list": []}}
    )
    set_snapshot(
        user,
        token,
        customer_details={"Status": 200, "Success": {}},
        margin_situation=margin,
    )
    assert get_snapshot(user, token) is not None

    from icici_breeze_backend.app.services.broker_snapshot_cache import evict_broker_snapshot

    evict_broker_snapshot(user, token)
    assert get_snapshot(user, token) is None


def test_break_chunk_evicts_snapshot_on_success(mock_broker_env):
    import asyncio

    from icici_breeze_backend.app.api.v1 import route_order

    ctx = MagicMock()
    ctx.user_id = "U1"
    ctx.broker_token = "tok"

    body = MagicMock()
    body.chunk_index = 1

    set_snapshot(
        "U1",
        "tok",
        customer_details={"Status": 200, "Success": {}},
        margin_situation={"Status": 200, "Success": {}},
    )

    with patch.object(
        route_order.breeze,
        "break_order_place_chunk",
        return_value={"success": True, "placed_quantity": 50},
    ), patch(
        "icici_breeze_backend.app.api.v1.route_order.get_icici_rate_limit_pause_seconds",
        return_value=1.0,
    ):
        asyncio.run(route_order.post_break_chunk(body=body, context=ctx, _trading_ok=None))

    assert get_snapshot("U1", "tok") is None


def test_cancel_commit_evicts_snapshot_on_success(mock_broker_env):
    import asyncio

    from icici_breeze_backend.app.api.v1 import route_book
    from icici_breeze_backend.app.domain.order import BookCancelCommitRequest, BookCancelResultItem

    ctx = MagicMock()
    ctx.user_id = "U1"
    ctx.broker_token = "tok"

    set_snapshot(
        "U1",
        "tok",
        customer_details={"Status": 200, "Success": {}},
        margin_situation={"Status": 200, "Success": {}},
    )

    body = BookCancelCommitRequest(
        results=[BookCancelResultItem(order_ref="123", success=True)],
    )

    with patch.object(route_book.breeze, "build_cancel_order_messages", return_value=[]):
        asyncio.run(route_book.post_cancel_commit(body=body, context=ctx, _trading_ok=None))

    assert get_snapshot("U1", "tok") is None
