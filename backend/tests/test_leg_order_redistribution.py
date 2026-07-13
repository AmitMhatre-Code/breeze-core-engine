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
        assert plan.modify == [{"order_id": "1", "exchange_code": "NFO", "quantity": 900 - 150}]

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
