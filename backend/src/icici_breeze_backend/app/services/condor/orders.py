"""From a current and a target position to orders, and the order they go out in.

Every condor action -- entry, roll, close, a manual ticket -- is expressed as "what the
position should be" and turned into orders here, so the safe sequence is decided in exactly
one place (plan section 4):

    1. BUY  opening   new longs (wings) first
    2. BUY  closing   buy back shorts
    3. SELL opening   new shorts, once their wings exist
    4. SELL closing   old longs last

At no step does the account hold a short whose wing has not been bought yet. Inside a group
the contract nearest the money goes first: it is the one whose price moves fastest while the
sequence is running.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Optional

from icici_breeze_backend.app.services.condor.model import Leg, OrderLeg
from icici_breeze_backend.app.services.condor.pricing import Right

_GROUP = {
    ("Buy", True): 0,
    ("Buy", False): 1,
    ("Sell", True): 2,
    ("Sell", False): 3,
}


def net_position(legs: Iterable[Leg]) -> dict[tuple[float, Right], int]:
    """Signed units per contract (long positive). Contracts that net to zero are dropped."""
    out: dict[tuple[float, Right], int] = defaultdict(int)
    for leg in legs:
        out[(float(leg.strike), leg.right)] += leg.signed_qty
    return {k: v for k, v in out.items() if v != 0}


def diff_orders(
    current: dict[tuple[float, Right], int], target: dict[tuple[float, Right], int]
) -> list[OrderLeg]:
    """Orders that turn `current` into `target`, unsequenced.

    A contract crossing from long to short (or back) is split into a closing order and an
    opening order, because the two belong in different steps of the sequence.
    """
    orders: list[OrderLeg] = []
    for key in sorted(set(current) | set(target)):
        strike, right = key
        have, want = current.get(key, 0), target.get(key, 0)
        if have == want:
            continue
        delta = want - have
        action = "Buy" if delta > 0 else "Sell"
        # The part of the move that reduces what is held, then the part that opens new.
        closing = min(abs(delta), abs(have)) if have != 0 and (have > 0) != (delta > 0) else 0
        opening = abs(delta) - closing
        if closing:
            orders.append(OrderLeg(strike, right, action, closing, opening=False))
        if opening:
            orders.append(OrderLeg(strike, right, action, opening, opening=True))
    return orders


def safe_sequence(orders: Iterable[OrderLeg], spot: Optional[float]) -> list[OrderLeg]:
    """The plan's four groups, nearest the money first within each."""

    def key(o: OrderLeg) -> tuple[int, float, str]:
        distance = abs(o.strike - spot) if spot else 0.0
        return (_GROUP[(o.action, o.opening)], distance, o.right)

    return sorted(orders, key=key)


def unhedged_rights(position: dict[tuple[float, Right], int]) -> list[Right]:
    """Rights on which short units exceed long units -- a short no wing caps.

    Any long of the same right caps a short's loss one-for-one, wherever its strike, so the
    test is a count per right. This is the only hard block on a manual ticket (plan 6a).
    """
    out: list[Right] = []
    for right in ("Call", "Put"):
        longs = sum(q for (_, r), q in position.items() if r == right and q > 0)
        shorts = -sum(q for (_, r), q in position.items() if r == right and q < 0)
        if shorts > longs:
            out.append(right)
    return out
