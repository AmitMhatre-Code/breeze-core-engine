"""post_modify_leg treats new_quantity as the leg's OPEN quantity, not its total:
already-filled quantity is added back server-side (via filled_floor) before the pure
planner runs, and old_quantity for the success message must be open-only too."""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from icici_breeze_backend.app.api.v1 import route_book
from icici_breeze_backend.app.domain.order import LegModifyOrderRef, LegModifyRequest

_EXEC_RESULT_OK = {
    "cancelled": [],
    "modified": [],
    "placed": [],
    "failures": [],
    "all_ok": True,
    "rate_limited": False,
}


def _ctx():
    ctx = MagicMock()
    ctx.user_id = "U1"
    ctx.broker_token = "tok"
    return ctx


def _body(orders: list[LegModifyOrderRef], new_quantity: str) -> LegModifyRequest:
    return LegModifyRequest(
        stock_code="NIFTY",
        expiry_date="16-Jun-2026",
        strike_price="24000",
        right="Call",
        action="Buy",
        orders=orders,
        new_quantity=new_quantity,
    )


def _run(body: LegModifyRequest):
    return asyncio.run(route_book.post_modify_leg(body=body, context=_ctx(), _trading_ok=None))


def _patched(**overrides):
    mocks = {
        "fetch_qty_limits": MagicMock(return_value=None),
        "fetch_lot_size": MagicMock(return_value=None),
        "execute_leg_modification": MagicMock(return_value=_EXEC_RESULT_OK),
        "build_modify_leg_messages": MagicMock(return_value=[]),
        "store_messages": MagicMock(),
    }
    mocks.update(overrides)
    ctxs = [patch.object(route_book.breeze, name, mock) for name, mock in mocks.items()]
    return ctxs, mocks


def test_floor_addition_adapter_correctness():
    # PARTIAL_EXECUTED: quantity=1000, pending=400 -> filled floor = 600
    orders = [
        LegModifyOrderRef(
            order_id="1", exchange_code="NFO", quantity=1000, pending_quantity=400,
            status="Partially Executed",
        )
    ]
    body = _body(orders, new_quantity="300")  # 300 open requested

    ctxs, mocks = _patched()
    with ctxs[0], ctxs[1], ctxs[2], ctxs[3], ctxs[4], patch(
        "icici_breeze_backend.app.api.v1.route_book.plan_leg_redistribution",
        wraps=route_book.plan_leg_redistribution,
    ) as spy_plan:
        _run(body)

    # floor(600) + requested open(300) = 900 must be what the planner receives as new_total_qty
    args, kwargs = spy_plan.call_args
    assert args[2] == 900


def test_zero_quantity_cancels_all_open_keeps_filled():
    orders = [
        LegModifyOrderRef(
            order_id="1", exchange_code="NFO", quantity=1000, pending_quantity=400,
            status="Partially Executed",
        ),
        LegModifyOrderRef(
            order_id="2", exchange_code="NFO", quantity=200, pending_quantity=200,
            status="Ordered",
        ),
    ]
    body = _body(orders, new_quantity="0")

    ctxs, mocks = _patched()
    with ctxs[0], ctxs[1], ctxs[2], ctxs[3], ctxs[4]:
        result = _run(body)

    assert result.success is True
    mocks["execute_leg_modification"].assert_called_once()
    plan = mocks["execute_leg_modification"].call_args[0][1]
    # all open pending quantity (400 + 200 = 600) must be cancelled, nothing left open
    assert set(plan.cancel_order_ids) == {"1", "2"}
    assert plan.modify == []
    assert plan.place_new_quantities == []


def test_mixed_cancelled_and_open_leg_old_quantity_is_open_only():
    orders = [
        # Cancelled before any fill: pending_quantity == quantity (nothing executed),
        # so filled_floor correctly attributes 0 filled quantity to this order.
        LegModifyOrderRef(
            order_id="1", exchange_code="NFO", quantity=500, pending_quantity=500,
            status="Cancelled",
        ),
        LegModifyOrderRef(
            order_id="2", exchange_code="NFO", quantity=300, pending_quantity=300,
            status="Ordered",
        ),
    ]
    body = _body(orders, new_quantity="300")  # unchanged open quantity

    ctxs, mocks = _patched()
    with ctxs[0], ctxs[1], ctxs[2], ctxs[3], ctxs[4]:
        _run(body)

    messages_args = mocks["build_modify_leg_messages"].call_args[0]
    # contract_label, old_quantity, new_quantity, ...
    old_quantity = messages_args[1]
    new_quantity = messages_args[2]
    assert old_quantity == 300  # open-only: excludes the cancelled order's 500
    assert new_quantity == 300


class TestLegModifyRequestValidation:
    def _orders(self):
        return [
            LegModifyOrderRef(
                order_id="1", exchange_code="NFO", quantity=100, pending_quantity=100,
                status="Ordered",
            )
        ]

    def test_zero_is_accepted(self):
        req = _body(self._orders(), new_quantity="0")
        assert req.new_quantity == "0"

    def test_negative_is_rejected(self):
        with pytest.raises(ValidationError):
            _body(self._orders(), new_quantity="-1")
