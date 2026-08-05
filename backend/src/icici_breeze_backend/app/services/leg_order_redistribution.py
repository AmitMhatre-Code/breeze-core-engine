"""Pure planning logic for leg-level order modify (quantity/price).

A "leg" (one option contract + one action) can be split across multiple ICICI
orders because of exchange freeze-quantity limits. Modifying a leg means
picking a new total quantity and redistributing it across however many
orders currently exist for that leg — shrinking/cancelling some, growing
others, or adding new chunk orders — without ever touching the portion of an
order that's already filled. No broker calls happen here; this only computes
the plan that `processor.execute_leg_modification` then carries out.

Quantity semantics: every quantity emitted in `modify` is that order's new
**open** (not-yet-filled) quantity, never its placed total. ICICI's modify_order
treats the quantity on a partially-traded order as the remaining quantity and
validates the exchange freeze limit against `already_traded + quantity_sent`, so
sending the placed total for an order with fills double-counts the filled
portion and is rejected ("Maximum qty per order for this stock allowed by the
exchange is : :N") once that sum passes the limit. For an order with no fills
the two are identical, which is why this only ever misfires on partial fills.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

import icici_breeze_backend.app.core.config as cfg

_OPEN_STATUSES = {cfg.REQUESTED, cfg.ORDERED, cfg.PARTIAL_EXECUTED}


@dataclass
class LegOrderState:
    order_id: str
    exchange_code: str
    quantity: int
    pending_quantity: int
    status: str

    @property
    def filled_quantity(self) -> int:
        return self.quantity - self.pending_quantity

    @property
    def is_open(self) -> bool:
        return self.status in _OPEN_STATUSES


@dataclass
class LegRedistributionPlan:
    cancel_order_ids: list[str]
    modify: list[dict]  # [{order_id, exchange_code, quantity}]
    place_new_quantities: list[int]


class LegModifyValidationError(ValueError):
    """Raised when the requested new total quantity is below the leg's filled floor."""


@dataclass
class LegModStep:
    """One broker-call-sized unit of work from a `LegRedistributionPlan`."""

    kind: Literal["cancel", "modify", "place"]
    order_id: Optional[str]
    exchange_code: Optional[str]
    quantity: int


def plan_steps(plan: LegRedistributionPlan) -> list[LegModStep]:
    """Flatten a plan into an ordered list of single broker-call steps.

    Order matters and must match `processor.execute_leg_modification`'s historical
    behavior: all cancels first, then all modifies, then all new placements — so a
    retry that resumes at a given step index reproduces the same effective sequence.
    """
    steps: list[LegModStep] = []
    for order_id in plan.cancel_order_ids:
        steps.append(LegModStep(kind="cancel", order_id=order_id, exchange_code=None, quantity=0))
    for item in plan.modify:
        steps.append(
            LegModStep(
                kind="modify",
                order_id=item["order_id"],
                exchange_code=item["exchange_code"],
                quantity=item["quantity"],
            )
        )
    for qty in plan.place_new_quantities:
        steps.append(LegModStep(kind="place", order_id=None, exchange_code=None, quantity=qty))
    return steps


def filled_floor(orders: list[LegOrderState]) -> int:
    return sum(o.filled_quantity for o in orders)


def plan_leg_redistribution(
    orders: list[LegOrderState],
    qty_per_order: int,
    new_total_qty: int,
    *,
    price_changed: bool = False,
) -> LegRedistributionPlan:
    """Compute cancel/modify/place-new actions to reach `new_total_qty` for a leg.

    Raises LegModifyValidationError if new_total_qty is below the sum of
    already-filled quantity across every order (that portion can never move),
    or if `price_changed` is set but this leg has no open order left to carry
    the price update (fully filled — nothing left to modify on the broker side).
    """
    floor = filled_floor(orders)
    if new_total_qty < floor:
        raise LegModifyValidationError(
            f"New quantity ({new_total_qty}) cannot be less than the already-filled "
            f"quantity across this leg ({floor})."
        )

    open_orders = [o for o in orders if o.is_open]
    current_open_total = sum(o.pending_quantity for o in open_orders)
    target_open_total = new_total_qty - floor

    plan = LegRedistributionPlan(cancel_order_ids=[], modify=[], place_new_quantities=[])

    if target_open_total == current_open_total:
        if price_changed:
            # Quantity is unchanged, so the redistribution logic below would
            # otherwise return a totally empty plan — but a price-only change
            # still needs to reach the broker, so re-submit every open order
            # at its current open quantity purely to carry the new price.
            if not open_orders:
                raise LegModifyValidationError(
                    "Cannot change price: this leg has no open quantity left "
                    "to modify (already fully filled)."
                )
            for o in open_orders:
                plan.modify.append(
                    {
                        "order_id": o.order_id,
                        "exchange_code": o.exchange_code,
                        "quantity": o.pending_quantity,
                    }
                )
        return plan

    if target_open_total < current_open_total:
        to_shed = current_open_total - target_open_total
        # Prefer cancelling whichever open orders have the least already-filled
        # quantity, to minimize disrupting partial fills.
        for o in sorted(open_orders, key=lambda o: o.filled_quantity):
            if to_shed <= 0:
                break
            if to_shed >= o.pending_quantity:
                plan.cancel_order_ids.append(o.order_id)
                to_shed -= o.pending_quantity
            else:
                plan.modify.append(
                    {
                        "order_id": o.order_id,
                        "exchange_code": o.exchange_code,
                        "quantity": o.pending_quantity - to_shed,
                    }
                )
                to_shed = 0
        return plan

    # Growing: fill existing open orders up to the freeze-aligned cap first.
    to_grow = target_open_total - current_open_total
    for o in open_orders:
        if to_grow <= 0:
            break
        # Headroom is measured against the placed total, since the exchange caps
        # filled + open per order; the quantity *sent* is then the open portion.
        capacity = qty_per_order - o.quantity
        if capacity <= 0:
            continue
        add = min(capacity, to_grow)
        plan.modify.append(
            {
                "order_id": o.order_id,
                "exchange_code": o.exchange_code,
                "quantity": o.pending_quantity + add,
            }
        )
        to_grow -= add

    if to_grow > 0:
        iterations = to_grow // qty_per_order
        remainder = to_grow % qty_per_order
        plan.place_new_quantities = [qty_per_order] * iterations
        if remainder:
            plan.place_new_quantities.append(remainder)

    return plan
