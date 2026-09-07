"""Paper-mode fill simulation (docs/bots-scalping-plan.md section 7).

Fills at the touch -- a buy at the ask, a sell at the bid -- plus a configurable fraction of
the bid-ask spread as adverse slippage on **each** leg, then the full charges stack on top.

Filling at the touch is optimistic: it cannot tell you a limit would have gone unfilled in a
fast move. The slippage term is the honest correction for the part that *is* knowable --
that a real order rarely gets the price on the screen -- without pretending to model a book
we cannot see. Both halves are stated so a paper session's numbers are read for what they
are (see the two caveats in section 7).

Every simulated fill is priced off a REAL live quote. Nothing here invents a price.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from icici_breeze_backend.app.services.aggressive_limit import round_to_tick
from icici_breeze_backend.app.services.bots.charges import ChargesModel


@dataclass(frozen=True)
class SimulatedFill:
    price: float
    quantity: int
    charges: float
    slippage_per_unit: float
    touch_price: float

    @property
    def value(self) -> float:
        return self.price * self.quantity


def _spread(bid: Optional[float], ask: Optional[float]) -> float:
    if bid is None or ask is None:
        return 0.0
    return max(0.0, float(ask) - float(bid))


def simulate_buy(
    bid: Optional[float], ask: Optional[float], quantity: int, charges: ChargesModel
) -> Optional[SimulatedFill]:
    """Buy at the ask, worsened by a share of the spread. None when unpriceable."""
    if ask is None or float(ask) <= 0 or quantity <= 0:
        return None
    slip = _spread(bid, ask) * float(charges.slippage_spread_fraction)
    price = round_to_tick(float(ask) + slip)
    return SimulatedFill(
        price=price,
        quantity=int(quantity),
        charges=charges.leg_charges(price, quantity, is_buy=True),
        slippage_per_unit=round(slip, 4),
        touch_price=float(ask),
    )


def simulate_sell(
    bid: Optional[float], ask: Optional[float], quantity: int, charges: ChargesModel
) -> Optional[SimulatedFill]:
    """Sell at the bid, worsened by a share of the spread.

    Floored at one tick rather than allowed to go to or below zero: a near-worthless option
    still sells for something, and a negative fill price would silently invent profit on the
    exit of a losing trade.
    """
    if bid is None or float(bid) <= 0 or quantity <= 0:
        return None
    slip = _spread(bid, ask) * float(charges.slippage_spread_fraction)
    price = max(round_to_tick(float(bid) - slip), round_to_tick(0.05))
    return SimulatedFill(
        price=price,
        quantity=int(quantity),
        charges=charges.leg_charges(price, quantity, is_buy=False),
        slippage_per_unit=round(slip, 4),
        touch_price=float(bid),
    )


def round_trip_pnl(entry: SimulatedFill, exit_: SimulatedFill) -> tuple[float, float, float]:
    """(gross, friction, net) for a long round trip.

    `friction` is charges only. The slippage is already inside the fill prices and so shows
    up in `gross` -- double-counting it here would make every cycle look worse than the
    prices it actually recorded.
    """
    gross = (exit_.price - entry.price) * entry.quantity
    friction = entry.charges + exit_.charges
    return round(gross, 2), round(friction, 2), round(gross - friction, 2)
