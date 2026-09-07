"""Net-of-charges premium for the writer bots (Bots 1 and 2).

The scalpers have always priced their round trips net; the writers quoted gross. That was a
real gap rather than a cosmetic one: a proposal's "premium" is the number a user approves a
trade on, and on a small leg the difference is material -- Rs 20 brokerage plus GST against a
few hundred rupees of premium is not a rounding error.

Estimated, and labelled as such wherever it is shown. Two honest limits:

* **Entry only.** A written option is held to expiry or bought back, and which of those
  happens is not knowable at proposal time. Charging a round trip would overstate the cost of
  a leg that expires worthless; charging one leg understates one that is bought back. The
  entry leg is the part that is certain.
* **Freeze-quantity chunking is not modelled.** A large leg goes out as several orders
  (`placement.qty_per_order`), each carrying its own brokerage, so the real cost can exceed
  this by a multiple of the per-order fee. `estimate_leg_charges` takes the order count when
  the caller knows it.
"""
from __future__ import annotations

from typing import Any, Optional

from icici_breeze_backend.app.services.bots.charges import ChargesModel, load_charges


def estimate_leg_charges(
    premium_per_share: float,
    quantity: int,
    *,
    exchange_code: str = "NFO",
    orders: int = 1,
    charges: Optional[ChargesModel] = None,
) -> float:
    """Charges on SELLING one leg once, across `orders` chunks.

    Brokerage is per order, so chunking multiplies it; the percentage components are on
    turnover and do not care how the quantity was split.
    """
    model = charges or load_charges()
    total = model.leg_charges(
        premium_per_share, quantity, is_buy=False, exchange_code=exchange_code
    )
    if orders > 1:
        # Each extra order carries another flat brokerage, and GST rides on that.
        extra = model.brokerage_for(0.0) * (orders - 1)
        total += extra * (1 + model.gst_pct / 100.0)
    return round(total, 2)


def annotate_leg(leg: Any, *, charges: Optional[ChargesModel] = None, orders: int = 1) -> Any:
    """Fill `estimated_charges` and `net_premium_total` on a proposal leg, in place.

    Tolerant by design: a leg missing a premium or a quantity is left alone rather than
    raising. This runs inside a scan that is already priced and allocated, and a cost
    estimate failing must never cost the user the proposal itself.
    """
    try:
        premium = float(getattr(leg, "premium_per_share", 0) or 0)
        quantity = int(getattr(leg, "quantity", 0) or 0)
        if premium <= 0 or quantity <= 0:
            return leg
        estimate = estimate_leg_charges(
            premium,
            quantity,
            exchange_code=str(getattr(leg, "exchange_code", "NFO") or "NFO"),
            orders=orders,
            charges=charges,
        )
        leg.estimated_charges = estimate
        leg.net_premium_total = round(float(getattr(leg, "premium_total", 0) or 0) - estimate, 2)
    except Exception:  # noqa: BLE001 -- see docstring
        return leg
    return leg


def annotate_legs(legs: list[Any], *, charges: Optional[ChargesModel] = None) -> list[Any]:
    """Annotate a whole proposal, loading the cost model once for the batch."""
    model = charges or load_charges()
    for leg in legs:
        annotate_leg(leg, charges=model)
    return legs
