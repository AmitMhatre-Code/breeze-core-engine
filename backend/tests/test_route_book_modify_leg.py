"""post_modify_leg_step/post_modify_leg_finalize treat new_quantity as the leg's OPEN quantity,
not its total: already-filled quantity is added back server-side (via filled_floor) before the
pure planner runs, and old_quantity for the success message must be open-only too. The plan is
recomputed fresh on every step call and flattened (via plan_steps) into single broker-call-sized
steps that the client drives one at a time.
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from icici_breeze_backend.app.api.v1 import route_book
from icici_breeze_backend.app.domain.order import (
    LegModifyFinalizeRequest,
    LegModifyOrderRef,
    LegModifyStepRequest,
)

_STEP_OK = {"success": True, "order_id": "1", "quantity": None, "price": None, "error": None, "rate_limited": False}


def _ctx():
    ctx = MagicMock()
    ctx.user_id = "U1"
    ctx.broker_token = "tok"
    return ctx


def _step_body(orders: list[LegModifyOrderRef], new_quantity: str, step_index: int = 0) -> LegModifyStepRequest:
    return LegModifyStepRequest(
        stock_code="NIFTY",
        expiry_date="16-Jun-2026",
        strike_price="24000",
        right="Call",
        action="Buy",
        orders=orders,
        new_quantity=new_quantity,
        step_index=step_index,
    )


def _finalize_body(orders: list[LegModifyOrderRef], new_quantity: int, **overrides) -> LegModifyFinalizeRequest:
    fields = dict(
        stock_code="NIFTY",
        expiry_date="16-Jun-2026",
        strike_price="24000",
        right="Call",
        action="Buy",
        orders=orders,
        new_quantity=new_quantity,
    )
    fields.update(overrides)
    return LegModifyFinalizeRequest(**fields)


def _run_step(body: LegModifyStepRequest):
    return asyncio.run(route_book.post_modify_leg_step(body=body, context=_ctx(), _trading_ok=None))


def _run_finalize(body: LegModifyFinalizeRequest):
    return asyncio.run(route_book.post_modify_leg_finalize(body=body, context=_ctx(), _trading_ok=None))


def _patched_step(**overrides):
    mocks = {
        "fetch_qty_limits": MagicMock(return_value=None),
        "fetch_lot_size": MagicMock(return_value=None),
        "execute_leg_modification_step": MagicMock(return_value=_STEP_OK),
    }
    mocks.update(overrides)
    ctxs = [patch.object(route_book.breeze, name, mock) for name, mock in mocks.items()]
    return ctxs, mocks


def _patched_finalize(**overrides):
    mocks = {
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
    body = _step_body(orders, new_quantity="300")  # 300 open requested

    ctxs, _mocks = _patched_step()
    with ctxs[0], ctxs[1], ctxs[2], patch(
        "icici_breeze_backend.app.api.v1.route_book.plan_leg_redistribution",
        wraps=route_book.plan_leg_redistribution,
    ) as spy_plan:
        _run_step(body)

    # floor(600) + requested open(300) = 900 must be what the planner receives as new_total_qty
    args, _kwargs = spy_plan.call_args
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
    body = _step_body(orders, new_quantity="0")

    ctxs, _mocks = _patched_step()
    with ctxs[0], ctxs[1], ctxs[2], patch(
        "icici_breeze_backend.app.api.v1.route_book.plan_leg_redistribution",
        wraps=route_book.plan_leg_redistribution,
    ) as spy_plan:
        result = _run_step(body)

    args, kwargs = spy_plan.call_args
    plan = route_book.plan_leg_redistribution(*args, **kwargs)
    # all open pending quantity (400 + 200 = 600) must be cancelled, nothing left open
    assert set(plan.cancel_order_ids) == {"1", "2"}
    assert plan.modify == []
    assert plan.place_new_quantities == []
    # two cancel steps total; step_index=0 executes the first one
    assert result.total_steps == 2
    assert result.op == "cancel"
    assert result.done is False


def test_step_index_past_total_reports_done():
    orders = [
        LegModifyOrderRef(
            order_id="1", exchange_code="NFO", quantity=300, pending_quantity=300,
            status="Ordered",
        ),
    ]
    body = _step_body(orders, new_quantity="300", step_index=5)  # unchanged qty -> 0 steps

    ctxs, mocks = _patched_step()
    with ctxs[0], ctxs[1], ctxs[2]:
        result = _run_step(body)

    assert result.done is True
    assert result.total_steps == 0
    mocks["execute_leg_modification_step"].assert_not_called()


def test_rate_limited_step_does_not_evict_snapshot():
    orders = [
        LegModifyOrderRef(
            order_id="1", exchange_code="NFO", quantity=300, pending_quantity=300,
            status="Ordered",
        ),
        LegModifyOrderRef(
            order_id="2", exchange_code="NFO", quantity=300, pending_quantity=300,
            status="Ordered",
        ),
    ]
    body = _step_body(orders, new_quantity="0", step_index=0)

    rate_limited_outcome = {
        "success": False, "order_id": "1", "quantity": None, "price": None,
        "error": "throttled", "rate_limited": True,
    }
    ctxs, mocks = _patched_step(execute_leg_modification_step=MagicMock(return_value=rate_limited_outcome))
    with ctxs[0], ctxs[1], ctxs[2], patch(
        "icici_breeze_backend.app.api.v1.route_book.evict_broker_snapshot"
    ) as mock_evict:
        result = _run_step(body)

    assert result.success is False
    assert result.rate_limited is True
    mock_evict.assert_not_called()


def test_finalize_old_quantity_is_open_only():
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
    body = _finalize_body(orders, new_quantity=300)  # unchanged open quantity

    ctxs, mocks = _patched_finalize()
    with ctxs[0], ctxs[1]:
        result = _run_finalize(body)

    messages_args = mocks["build_modify_leg_messages"].call_args[0]
    # contract_label, old_quantity, new_quantity, ...
    old_quantity = messages_args[1]
    new_quantity = messages_args[2]
    assert old_quantity == 300  # open-only: excludes the cancelled order's 500
    assert new_quantity == 300
    assert result.success is True


def test_finalize_updates_squareoff_leg_order_ids():
    orders = [
        LegModifyOrderRef(order_id="1", exchange_code="NFO", quantity=300, pending_quantity=0, status="Executed"),
        LegModifyOrderRef(order_id="2", exchange_code="NFO", quantity=300, pending_quantity=300, status="Ordered"),
    ]
    body = _finalize_body(
        orders, new_quantity=300,
        cancelled_order_ids=["2"],
        placed=[{"order_id": "3", "quantity": 300, "price": "1"}],
        rule_id="rule1", scrip_key="scrip1",
    )

    ctxs, _mocks = _patched_finalize()
    with ctxs[0], ctxs[1], patch.object(route_book.squareoff_repo, "update_leg_order_ids") as mock_update:
        _run_finalize(body)

    mock_update.assert_called_once()
    args, _kwargs = mock_update.call_args
    assert args[0] == "rule1"
    assert args[1] == "scrip1"
    # order "1" untouched, order "2" cancelled (dropped), order "3" newly placed
    assert set(args[2]) == {"1", "3"}


class TestLegModifyStepRequestValidation:
    def _orders(self):
        return [
            LegModifyOrderRef(
                order_id="1", exchange_code="NFO", quantity=100, pending_quantity=100,
                status="Ordered",
            )
        ]

    def test_zero_is_accepted(self):
        req = _step_body(self._orders(), new_quantity="0")
        assert req.new_quantity == "0"

    def test_negative_is_rejected(self):
        with pytest.raises(ValidationError):
            _step_body(self._orders(), new_quantity="-1")
