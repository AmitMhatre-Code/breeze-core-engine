"""Model-based probability of profit for Strategy Builder (New). Mirrors frontend payoff.ts."""
from __future__ import annotations

import math
import random
from typing import Literal, Protocol

DEFAULT_R = 0.07
DEFAULT_Q = 0.0
DEFAULT_SAMPLES = 8000

Side = Literal["Buy", "Sell"]
Right = Literal["Call", "Put"]


class PopLeg(Protocol):
    right: Right
    side: Side
    strike: int
    quantity: int
    premium_per_unit: float


def _intrinsic(spot: float, strike: float, right: Right) -> float:
    if right == "Call":
        return max(0.0, spot - strike)
    return max(0.0, strike - spot)


def portfolio_payoff_at_expiry(spot: float, legs: list[PopLeg], lot_size: int) -> float:
    ls = lot_size if lot_size > 0 else 0
    total = 0.0
    for leg in legs:
        units = max(0, leg.quantity)
        if units <= 0 or ls <= 0:
            continue
        intr = _intrinsic(spot, float(leg.strike), leg.right)
        prem = leg.premium_per_unit if math.isfinite(leg.premium_per_unit) else 0.0
        if leg.side == "Buy":
            total += units * (intr - prem)
        else:
            total += units * (prem - intr)
    return total


def estimate_probability_of_profit(
    spot: float,
    t_years: float,
    sigma: float,
    legs: list[PopLeg],
    lot_size: int,
    *,
    r: float = DEFAULT_R,
    q: float = DEFAULT_Q,
    samples: int = DEFAULT_SAMPLES,
    rng: random.Random | None = None,
) -> float:
    if t_years <= 0 or sigma <= 0 or spot <= 0 or samples < 1 or not legs:
        return 0.0
    gen = rng or random
    drift = (r - q - 0.5 * sigma * sigma) * t_years
    vol = sigma * math.sqrt(t_years)
    wins = 0
    for _ in range(samples):
        z = gen.gauss(0.0, 1.0)
        st = spot * math.exp(drift + vol * z)
        if portfolio_payoff_at_expiry(st, legs, lot_size) > 0:
            wins += 1
    return (100.0 * wins) / samples
