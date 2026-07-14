"""execute_leg_modification must always resend price on a modify call — ICICI
rejects a quantity-only modify_order with a generic error if price is omitted
from the request body entirely, rather than treating it as "leave unchanged"."""
from __future__ import annotations

from unittest.mock import patch

from icici_breeze_backend.app.services.leg_order_redistribution import LegRedistributionPlan
from icici_breeze_backend.app.services.processor import processor

_CONTRACT = {
    "product_type": "options",
    "stock_code": "NIFTY",
    "action": "sell",
    "strike_price": "26000",
    "right": "call",
    "expiry_date": "2026-07-14",
    "exchange_code": "NFO",
}


def test_quantity_only_change_still_resends_current_price():
    plan = LegRedistributionPlan(
        cancel_order_ids=[],
        modify=[{"order_id": "1", "exchange_code": "NFO", "quantity": 585}],
        place_new_quantities=[],
    )
    p = processor()
    with patch.object(
        p,
        "modify_order_single",
        return_value={"success": True, "rate_limited": False, "daily_limit_exhausted": False, "error": None},
    ) as mock_modify:
        p.execute_leg_modification(
            "user1", plan, contract=_CONTRACT, new_price=None, current_price="1"
        )
    mock_modify.assert_called_once_with(
        "user1", "1|NFO", quantity="585", price="1"
    )


def test_price_change_sends_new_price():
    plan = LegRedistributionPlan(
        cancel_order_ids=[],
        modify=[{"order_id": "1", "exchange_code": "NFO", "quantity": 650}],
        place_new_quantities=[],
    )
    p = processor()
    with patch.object(
        p,
        "modify_order_single",
        return_value={"success": True, "rate_limited": False, "daily_limit_exhausted": False, "error": None},
    ) as mock_modify:
        p.execute_leg_modification(
            "user1", plan, contract=_CONTRACT, new_price="1.2", current_price="1"
        )
    mock_modify.assert_called_once_with(
        "user1", "1|NFO", quantity="650", price="1.2"
    )
