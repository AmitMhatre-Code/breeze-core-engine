"""execute_leg_modification_step must always resend price on a modify call — ICICI
rejects a quantity-only modify_order with a generic error if price is omitted
from the request body entirely, rather than treating it as "leave unchanged"."""
from __future__ import annotations

from unittest.mock import patch

from icici_breeze_backend.app.services.leg_order_redistribution import LegModStep
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
    step = LegModStep(kind="modify", order_id="1", exchange_code="NFO", quantity=585)
    p = processor()
    with patch.object(
        p,
        "modify_order_single",
        return_value={"success": True, "rate_limited": False, "daily_limit_exhausted": False, "error": None},
    ) as mock_modify:
        p.execute_leg_modification_step(
            "user1", step, contract=_CONTRACT, new_price=None, current_price="1"
        )
    mock_modify.assert_called_once_with(
        "user1", "1|NFO", quantity="585", price="1"
    )


def test_price_change_sends_new_price():
    step = LegModStep(kind="modify", order_id="1", exchange_code="NFO", quantity=650)
    p = processor()
    with patch.object(
        p,
        "modify_order_single",
        return_value={"success": True, "rate_limited": False, "daily_limit_exhausted": False, "error": None},
    ) as mock_modify:
        p.execute_leg_modification_step(
            "user1", step, contract=_CONTRACT, new_price="1.2", current_price="1"
        )
    mock_modify.assert_called_once_with(
        "user1", "1|NFO", quantity="650", price="1.2"
    )


def test_cancel_step_success():
    step = LegModStep(kind="cancel", order_id="1", exchange_code="NFO", quantity=0)
    p = processor()
    with patch.object(
        p, "cancel_order_single", return_value={"success": True, "error": None},
    ) as mock_cancel:
        out = p.execute_leg_modification_step(
            "user1", step, contract=_CONTRACT, new_price=None, current_price="1"
        )
    mock_cancel.assert_called_once_with("user1", "1")
    assert out == {
        "success": True, "order_id": "1", "quantity": None, "price": None,
        "error": None, "rate_limited": False,
    }


def test_cancel_step_rate_limited_surfaces_flag():
    step = LegModStep(kind="cancel", order_id="1", exchange_code="NFO", quantity=0)
    p = processor()
    with patch.object(
        p, "cancel_order_single",
        return_value={"success": False, "error": "throttled", "rate_limited": True},
    ):
        out = p.execute_leg_modification_step(
            "user1", step, contract=_CONTRACT, new_price=None, current_price="1"
        )
    assert out["success"] is False
    assert out["rate_limited"] is True
    assert out["error"] == "throttled"


def test_place_step_reports_new_order_id():
    step = LegModStep(kind="place", order_id=None, exchange_code=None, quantity=250)
    p = processor()
    with patch.object(
        p, "place_order",
        return_value={"Status": 200, "Success": {"order_id": "999"}},
    ) as mock_place:
        out = p.execute_leg_modification_step(
            "user1", step, contract=_CONTRACT, new_price=None, current_price="1"
        )
    assert mock_place.call_args.kwargs["quantity"] == 250
    assert out["success"] is True
    assert out["order_id"] == "999"
    assert out["quantity"] == 250
