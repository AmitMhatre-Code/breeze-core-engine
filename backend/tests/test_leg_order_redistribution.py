import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.services.leg_order_redistribution import (
    LegModifyValidationError,
    LegOrderState,
    filled_floor,
    plan_leg_redistribution,
)


def _order(order_id, quantity, pending_quantity, status=cfg.ORDERED, exchange_code="NFO"):
    return LegOrderState(
        order_id=order_id,
        exchange_code=exchange_code,
        quantity=quantity,
        pending_quantity=pending_quantity,
        status=status,
    )


class TestFilledFloor:
    def test_sums_filled_quantity_across_all_orders(self):
        orders = [
            _order("1", 1000, 400, status=cfg.PARTIAL_EXECUTED),
            _order("2", 500, 0, status=cfg.EXECUTED),
        ]
        assert filled_floor(orders) == (1000 - 400) + (500 - 0)


class TestPlanLegRedistribution:
    def test_rejects_new_total_below_filled_floor(self):
        orders = [_order("1", 1000, 400, status=cfg.PARTIAL_EXECUTED)]
        try:
            plan_leg_redistribution(orders, 1800, 500)
        except LegModifyValidationError:
            pass
        else:
            raise AssertionError("expected LegModifyValidationError")

    def test_no_op_when_target_equals_current(self):
        orders = [_order("1", 1000, 1000)]
        plan = plan_leg_redistribution(orders, 1800, 1000)
        assert plan.cancel_order_ids == []
        assert plan.modify == []
        assert plan.place_new_quantities == []

    def test_shrink_within_one_order(self):
        # Single order, unfilled, leg total shrinks from 1000 to 600.
        orders = [_order("1", 1000, 1000)]
        plan = plan_leg_redistribution(orders, 1800, 600)
        assert plan.cancel_order_ids == []
        assert plan.modify == [{"order_id": "1", "exchange_code": "NFO", "quantity": 600}]
        assert plan.place_new_quantities == []

    def test_shrink_cancels_least_filled_order_first(self):
        # Two open orders: "1" is half-filled (450/900), "2" is completely unfilled
        # (0/900). Shrinking so exactly "1"'s current pending remains should cancel
        # "2" (least filled) entirely and leave "1" untouched.
        orders = [
            _order("1", 900, 450, status=cfg.PARTIAL_EXECUTED),
            _order("2", 900, 900),
        ]
        floor = filled_floor(orders)  # 450
        # target_open_total = 450 (just "1"'s current pending) -> new_total = 900
        plan = plan_leg_redistribution(orders, 1800, floor + 450)
        assert plan.cancel_order_ids == ["2"]
        assert plan.modify == []

    def test_shrink_partially_reduces_remaining_order_after_cancelling_others(self):
        orders = [
            _order("1", 900, 450, status=cfg.PARTIAL_EXECUTED),
            _order("2", 900, 900),
        ]
        floor = filled_floor(orders)  # 450
        # target_open_total = 300 (less than order "2"'s 900 pending alone, so "2"
        # is cancelled and "1" absorbs the rest of the shed).
        plan = plan_leg_redistribution(orders, 1800, floor + 300)
        assert plan.cancel_order_ids == ["2"]
        # Quantity sent is order "1"'s new OPEN quantity (450 pending - 150 shed),
        # not its new placed total (750) — see module docstring.
        assert plan.modify == [{"order_id": "1", "exchange_code": "NFO", "quantity": 450 - 150}]

    def test_grow_within_existing_capacity(self):
        # One order at 900/1800 cap, growing leg total by 400 should just bump
        # that order's own quantity, no new order needed.
        orders = [_order("1", 900, 900)]
        plan = plan_leg_redistribution(orders, 1800, 1300)
        assert plan.cancel_order_ids == []
        assert plan.modify == [{"order_id": "1", "exchange_code": "NFO", "quantity": 1300}]
        assert plan.place_new_quantities == []

    def test_grow_beyond_capacity_places_new_chunk_orders(self):
        # Two orders already at the freeze cap (1800 each); growing further can
        # only be satisfied by placing new chunk order(s).
        orders = [
            _order("1", 1800, 1800),
            _order("2", 1800, 1800),
        ]
        # total currently 3600, growing to 3600 + 1800 + 400 = 5800
        plan = plan_leg_redistribution(orders, 1800, 5800)
        assert plan.modify == []
        assert plan.place_new_quantities == [1800, 400]

    def test_single_order_leg_degenerates_correctly(self):
        orders = [_order("1", 500, 500)]
        plan = plan_leg_redistribution(orders, 1800, 500)
        assert plan.cancel_order_ids == []
        assert plan.modify == []
        assert plan.place_new_quantities == []

    def test_price_only_change_still_resubmits_open_orders(self):
        # Quantity is unchanged (target == current), but a price-only change
        # must still surface every open order in `modify` so it actually
        # reaches the broker instead of silently no-op'ing.
        orders = [
            _order("1", 900, 450, status=cfg.PARTIAL_EXECUTED),
            _order("2", 900, 900),
        ]
        plan = plan_leg_redistribution(orders, 1800, 1800, price_changed=True)
        assert plan.cancel_order_ids == []
        assert plan.place_new_quantities == []
        assert {m["order_id"] for m in plan.modify} == {"1", "2"}
        # Each order carries the price at its own OPEN quantity: "1" is half
        # filled so it resubmits 450, not its placed total of 900.
        assert next(m for m in plan.modify if m["order_id"] == "1")["quantity"] == 450
        assert next(m for m in plan.modify if m["order_id"] == "2")["quantity"] == 900

    def test_price_only_change_with_no_open_orders_raises(self):
        # Every order already fully filled — nothing left on the broker side
        # to carry a price update, so this must be a clear error, not a
        # silent success.
        orders = [_order("1", 500, 0, status=cfg.EXECUTED)]
        try:
            plan_leg_redistribution(orders, 1800, 500, price_changed=True)
        except LegModifyValidationError:
            pass
        else:
            raise AssertionError("expected LegModifyValidationError")

    def test_price_unchanged_stays_a_true_no_op(self):
        orders = [_order("1", 1000, 1000)]
        plan = plan_leg_redistribution(orders, 1800, 1000, price_changed=False)
        assert plan.cancel_order_ids == []
        assert plan.modify == []
        assert plan.place_new_quantities == []


class TestPartialFillFreezeLimit:
    """Regression cover for modify quantities on partially-filled orders.

    ICICI validates the exchange freeze limit on a modify against
    `already_filled + quantity_sent`, so every emitted quantity must be the
    order's new OPEN quantity. Sending the placed total instead double-counts
    the filled portion and the exchange rejects it with "Maximum qty per order
    for this stock allowed by the exchange is : :N". Fully-unfilled orders are
    unaffected (total == pending), so these cases are the only ones that catch it.
    """

    def test_price_change_on_partially_filled_leg_stays_within_freeze_limit(self):
        # Real BSESEN case: freeze limit 1000, leg of 3200 split 1000/1000/1000/200.
        # Order "B" filled 140 of 1000 with 860 still open; a price-only change
        # previously resubmitted its placed total (1000), so the exchange saw
        # 140 + 1000 = 1140 > 1000 and rejected only that one order.
        qty_per_order = 1000
        orders = [
            _order("A", 1000, 0, status=cfg.EXECUTED),
            _order("B", 1000, 860, status=cfg.PARTIAL_EXECUTED),
            _order("C", 1000, 1000),
            _order("D", 200, 200),
        ]
        floor = filled_floor(orders)
        assert floor == 1140
        # Quantity untouched (2060 open), price-only change.
        plan = plan_leg_redistribution(
            orders, qty_per_order, floor + 2060, price_changed=True
        )

        assert plan.cancel_order_ids == []
        assert plan.place_new_quantities == []
        by_id = {m["order_id"]: m["quantity"] for m in plan.modify}
        assert by_id == {"B": 860, "C": 1000, "D": 200}

        filled_by_id = {o.order_id: o.filled_quantity for o in orders}
        for order_id, sent in by_id.items():
            assert filled_by_id[order_id] + sent <= qty_per_order

    def test_shrink_of_partially_filled_order_sends_open_quantity(self):
        # 200 of 1000 filled, 800 open; shedding 300 leaves 500 open — the order's
        # new placed total would be 700, but 500 is what must reach the broker.
        orders = [_order("1", 1000, 800, status=cfg.PARTIAL_EXECUTED)]
        floor = filled_floor(orders)  # 200
        plan = plan_leg_redistribution(orders, 1000, floor + 500)
        assert plan.cancel_order_ids == []
        assert plan.modify == [{"order_id": "1", "exchange_code": "NFO", "quantity": 500}]

    def test_grow_of_partially_filled_order_fills_headroom_without_breaching_limit(self):
        # 200 filled / 400 open on a 600-qty order, freeze limit 1000: headroom is
        # measured on the placed total (1000 - 600 = 400) but the quantity sent is
        # the open portion (400 + 400 = 800), keeping filled + sent at exactly 1000.
        qty_per_order = 1000
        orders = [_order("1", 600, 400, status=cfg.PARTIAL_EXECUTED)]
        floor = filled_floor(orders)  # 200
        plan = plan_leg_redistribution(orders, qty_per_order, floor + 800)
        assert plan.modify == [{"order_id": "1", "exchange_code": "NFO", "quantity": 800}]
        assert plan.place_new_quantities == []
        assert orders[0].filled_quantity + plan.modify[0]["quantity"] == qty_per_order

    def test_grow_past_headroom_of_partially_filled_order_spills_to_new_orders(self):
        # Same order, but growing beyond its remaining headroom: the excess must
        # become new chunk orders rather than inflating the capped one.
        qty_per_order = 1000
        orders = [_order("1", 600, 400, status=cfg.PARTIAL_EXECUTED)]
        floor = filled_floor(orders)  # 200
        plan = plan_leg_redistribution(orders, qty_per_order, floor + 800 + 1300)
        assert plan.modify == [{"order_id": "1", "exchange_code": "NFO", "quantity": 800}]
        assert plan.place_new_quantities == [1000, 300]
        assert all(q <= qty_per_order for q in plan.place_new_quantities)
