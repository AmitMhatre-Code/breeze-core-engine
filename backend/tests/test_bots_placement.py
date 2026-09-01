"""Order placement shared by the bots (services.bots.placement)."""
from __future__ import annotations

import pytest

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.services.bots import placement


class FakeProc:
    def __init__(self, *, qty_limit=None, lot=None, responses=None, raises=False):
        self._qty_limit = qty_limit
        self._lot = lot
        self._responses = list(responses or [])
        self._raises = raises
        self.orders = []

    def fetch_qty_limits(self, stock_code, exchange_code=cfg.NFO):
        if self._qty_limit is None:
            raise RuntimeError("scrip master gap")
        return self._qty_limit

    def fetch_lot_size(self, stock_code, expiry_date, exchange_code=cfg.NFO):
        return self._lot

    def place_order(self, user_id, product_type, stock_code, action, strike_price, right,
                    price, expiry_date, quantity, exchange_code=cfg.NFO, aggressive_limit=False):
        if self._raises:
            raise RuntimeError("socket died")
        self.orders.append(
            {"stock_code": stock_code, "quantity": quantity, "price": price,
             "action": action, "right": right}
        )
        if self._responses:
            return self._responses.pop(0)
        return {"Status": 200, "Success": {"order_id": f"OID{len(self.orders)}"}, "Error": None}


def leg(quantity=4500, premium=4.25):
    return {
        "stock_code": "NTPC", "exchange_code": cfg.NFO, "right": "call",
        "expiry_display": "24-Sep-2026", "strike_price": 350.0,
        "quantity": quantity, "premium_per_share": premium,
    }


# --- limit price ---------------------------------------------------------------------


def test_sell_limit_undercuts_the_bid_and_lands_on_a_tick():
    price = placement.sell_limit_price(10.0, 5.0)
    assert price == pytest.approx(9.5)
    assert round(price / cfg.AGGRESSIVE_LIMIT_TICK_SIZE) == price / cfg.AGGRESSIVE_LIMIT_TICK_SIZE


def test_sell_limit_never_falls_below_one_tick():
    """A near-worthless option must not be offered at zero."""
    assert placement.sell_limit_price(0.05, 50.0) >= cfg.AGGRESSIVE_LIMIT_TICK_SIZE


# --- chunking ------------------------------------------------------------------------


def test_order_is_chunked_under_the_freeze_limit():
    # Freeze limit 1800, lot 1500 -> one lot per order; 4500 needs three orders.
    proc = FakeProc(qty_limit=1800, lot=1500)
    results = placement.place_short_legs(proc, "u1", [leg(4500)], tolerance_pct=5.0)
    assert [o["quantity"] for o in proc.orders] == [1500, 1500, 1500]
    assert len(results[0].order_ids) == 3
    assert results[0].ok


def test_chunks_stay_lot_aligned():
    """2600 freeze / 1000 lot must chunk at 2000, never at 2600."""
    proc = FakeProc(qty_limit=2600, lot=1000)
    placement.place_short_legs(proc, "u1", [leg(5000)], tolerance_pct=5.0)
    assert [o["quantity"] for o in proc.orders] == [2000, 2000, 1000]


def test_a_scrip_master_gap_places_unchunked_rather_than_blocking():
    proc = FakeProc(qty_limit=None, lot=1500)
    results = placement.place_short_legs(proc, "u1", [leg(4500)], tolerance_pct=5.0)
    assert [o["quantity"] for o in proc.orders] == [4500]
    assert results[0].ok


def test_quantity_below_the_freeze_limit_goes_out_in_one_order():
    proc = FakeProc(qty_limit=100000, lot=1500)
    placement.place_short_legs(proc, "u1", [leg(3000)], tolerance_pct=5.0)
    assert [o["quantity"] for o in proc.orders] == [3000]


# --- failures ------------------------------------------------------------------------


def test_rejection_is_reported_not_raised():
    proc = FakeProc(qty_limit=100000, lot=1500,
                    responses=[{"Status": 400, "Error": "Insufficient margin"}])
    results = placement.place_short_legs(proc, "u1", [leg(3000)], tolerance_pct=5.0)
    assert results[0].ok is False
    assert "Insufficient margin" in results[0].error


def test_partial_placement_is_reported_as_partial():
    """Two chunks in, third rejected — neither a clean success nor a clean failure."""
    proc = FakeProc(
        qty_limit=1800, lot=1500,
        responses=[
            {"Status": 200, "Success": {"order_id": "A"}},
            {"Status": 200, "Success": {"order_id": "B"}},
            {"Status": 400, "Error": "Freeze limit"},
        ],
    )
    results = placement.place_short_legs(proc, "u1", [leg(4500)], tolerance_pct=5.0)
    assert results[0].order_ids == ["A", "B"]
    assert results[0].error.startswith("Partially placed (2 order(s))")
    assert results[0].ok is False


def test_missing_order_id_is_an_error_not_a_silent_success():
    proc = FakeProc(qty_limit=100000, lot=1500,
                    responses=[{"Status": 200, "Success": {}}])
    results = placement.place_short_legs(proc, "u1", [leg(3000)], tolerance_pct=5.0)
    assert results[0].ok is False
    assert "order id" in results[0].error


def test_broker_exception_is_contained():
    proc = FakeProc(qty_limit=100000, lot=1500, raises=True)
    results = placement.place_short_legs(proc, "u1", [leg(3000)], tolerance_pct=5.0)
    assert results[0].ok is False


def test_one_failing_leg_does_not_stop_the_others():
    """Placement is best-effort per leg: a rejection on one scrip must not abandon another."""
    proc = FakeProc(
        qty_limit=100000, lot=1500,
        responses=[
            {"Status": 400, "Error": "Rejected"},
            {"Status": 200, "Success": {"order_id": "B"}},
        ],
    )
    second = {**leg(1500), "stock_code": "ITC"}
    results = placement.place_short_legs(proc, "u1", [leg(3000), second], tolerance_pct=5.0)
    assert results[0].ok is False
    assert results[1].ok is True


def test_puts_are_sold_with_the_put_right():
    proc = FakeProc(qty_limit=100000, lot=1500)
    placement.place_short_legs(
        proc, "u1", [{**leg(1500), "right": "put"}], tolerance_pct=5.0
    )
    assert proc.orders[0]["right"] == cfg.PUT
    assert proc.orders[0]["action"] == cfg.SELL
