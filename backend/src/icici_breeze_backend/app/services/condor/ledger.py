"""Campaign money: what closing now would leave, and where expiry breaks even.

The ledger's cash (every fill's proceeds minus every fill's cost, net of charges) is the
plan's "total net credit". Two numbers are built on it:

* **Campaign P&L now** = ledger cash + what the open legs would fetch if closed now (longs at
  the bid, shorts bought back at the ask) - the charges that closing would cost. This is what
  the max-loss stop reads.
* **Break-evens** = where ledger cash + the open legs' expiry payoff crosses zero. They include
  every past roll and hold for any shape. Settlement charges at expiry are not modelled.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Optional

from icici_breeze_backend.app.services.bots.charges import ChargesModel
from icici_breeze_backend.app.services.condor.model import Leg, MarketSnapshot
from icici_breeze_backend.app.services.condor.pricing import Right


def _positive(v: Optional[float]) -> bool:
    return v is not None and math.isfinite(v) and v > 0


@dataclass(frozen=True)
class CloseValue:
    """What closing every open leg now would bring in (negative when it costs)."""

    value_inr: Optional[float]
    charges_inr: float
    # Shorts with no ask: they cannot be bought back at a known price.
    unpriced: tuple[Leg, ...]
    # Longs with no bid: valued at zero, which is what selling them now would fetch.
    unbid: tuple[Leg, ...]


def close_value(legs: Iterable[Leg], market: MarketSnapshot, charges: ChargesModel) -> CloseValue:
    value = 0.0
    fees = 0.0
    unpriced: list[Leg] = []
    unbid: list[Leg] = []
    for leg in legs:
        quote = market.quote(leg.strike, leg.right)
        if leg.is_short:
            ask = quote.ask if quote else None
            if not _positive(ask):
                unpriced.append(leg)
                continue
            value -= ask * leg.quantity
            fees += charges.leg_charges(ask, leg.quantity, is_buy=True)
        else:
            bid = quote.bid if quote else None
            if not _positive(bid):
                unbid.append(leg)
                continue
            value += bid * leg.quantity
            fees += charges.leg_charges(bid, leg.quantity, is_buy=False)
    return CloseValue(
        value_inr=None if unpriced else value,
        charges_inr=fees,
        unpriced=tuple(unpriced),
        unbid=tuple(unbid),
    )


def campaign_pnl(ledger_cash_inr: float, close: CloseValue) -> Optional[float]:
    if close.value_inr is None:
        return None
    return ledger_cash_inr + close.value_inr - close.charges_inr


def _intrinsic(right: Right, strike: float, s: float) -> float:
    return max(0.0, s - strike) if right == "Call" else max(0.0, strike - s)


def expiry_pnl(legs: Iterable[Leg], ledger_cash_inr: float, s: float) -> float:
    """Campaign P&L if expiry settled at `s`, the open legs taken at intrinsic."""
    return ledger_cash_inr + sum(leg.signed_qty * _intrinsic(leg.right, leg.strike, s) for leg in legs)


def _slopes(legs: list[Leg]) -> tuple[float, float]:
    """d(P&L)/dS far below every strike and far above every strike."""
    below = sum(-leg.signed_qty for leg in legs if leg.right == "Put")
    above = sum(leg.signed_qty for leg in legs if leg.right == "Call")
    return below, above


def centre(legs: Iterable[Leg], fallback: float) -> float:
    """The position's own middle: the quantity-weighted mean of its short strikes.

    Break-evens are found either side of this, not of spot. Anchoring on spot misnames them
    the moment spot crosses one: the upper break-even becomes the "lower" one and the
    position looks inside its range when it is outside it.
    """
    shorts = [leg for leg in legs if leg.is_short]
    units = sum(leg.quantity for leg in shorts)
    return sum(leg.strike * leg.quantity for leg in shorts) / units if units else fallback


def breakevens(legs: Iterable[Leg], ledger_cash_inr: float, anchor: float) -> tuple[Optional[float], Optional[float]]:
    """The expiry break-even nearest below `anchor` and nearest above it, else None per side.
    Pass `centre(legs, spot)` as the anchor."""
    legs = list(legs)
    spot = anchor
    kinks = sorted({float(leg.strike) for leg in legs} | {spot})
    if not kinks:
        return None, None
    slope_below, slope_above = _slopes(legs)
    # Bracket the curve with two far points so the outer segments are searched too.
    span = max(kinks[-1] - kinks[0], spot) + 1.0
    points = [kinks[0] - span] + kinks + [kinks[-1] + span]
    roots: list[float] = []
    for a, b in zip(points, points[1:]):
        fa, fb = expiry_pnl(legs, ledger_cash_inr, a), expiry_pnl(legs, ledger_cash_inr, b)
        if fa == 0:
            roots.append(a)
        if (fa < 0) != (fb < 0) and fb != 0:
            roots.append(a + (b - a) * fa / (fa - fb))
    # Beyond the brackets each outer segment continues at its slope: f(x) = f0 + s(x - x0).
    # Below, the root x0 - f0/s lies further down only when f0 and s share a sign; above, the
    # root lies further up only when they differ.
    x0, f_lo = points[0], expiry_pnl(legs, ledger_cash_inr, points[0])
    if slope_below and f_lo * slope_below > 0:
        roots.append(x0 - f_lo / slope_below)
    x1, f_hi = points[-1], expiry_pnl(legs, ledger_cash_inr, points[-1])
    if slope_above and f_hi * slope_above < 0:
        roots.append(x1 - f_hi / slope_above)
    roots = [r for r in roots if r > 0]
    lower = max((r for r in roots if r <= spot), default=None)
    upper = min((r for r in roots if r >= spot), default=None)
    return lower, upper


def worst_loss_at_expiry(legs: Iterable[Leg], ledger_cash_inr: float) -> Optional[float]:
    """The lowest expiry P&L over every price, or None when it is unbounded (a short that no
    long caps). Piecewise linear, so the minimum sits at a strike or at an end."""
    legs = list(legs)
    slope_below, slope_above = _slopes(legs)
    if slope_below > 0 or slope_above < 0:
        return None
    strikes = sorted({float(leg.strike) for leg in legs})
    if not strikes:
        return ledger_cash_inr
    return min(expiry_pnl(legs, ledger_cash_inr, s) for s in strikes)
