"""Analytic PoP proxies per Gemini §3.2 (no Monte Carlo in engine path)."""
from __future__ import annotations

from icici_breeze_backend.app.services.options_strategy_engine.greeks import (
    bs_delta,
    prob_above_strike,
    prob_below_strike,
)
from icici_breeze_backend.app.services.options_strategy_engine.helpers import sigma_for_pop
from icici_breeze_backend.app.services.options_strategy_engine.types import EngineContext, Right, TradeLeg


def pop_short_otm(delta: float | None) -> float:
    if delta is None:
        return 0.0
    return max(0.0, min(100.0, (1.0 - abs(delta)) * 100.0))


def pop_long_otm(
    spot: float,
    strike: float,
    premium: float,
    right: Right,
    t: float,
    sigma: float,
) -> float:
    if right == "Call":
        be = strike + premium
        p = prob_above_strike(spot, be, t, sigma)
    else:
        be = strike - premium
        p = prob_below_strike(spot, be, t, sigma)
    return max(0.0, min(100.0, p * 100.0))


def pop_between_breakevens(
    spot: float,
    lower: float,
    upper: float,
    t: float,
    sigma: float,
) -> float:
    if lower >= upper:
        return 0.0
    p_above_lower = prob_above_strike(spot, lower, t, sigma)
    p_above_upper = prob_above_strike(spot, upper, t, sigma)
    p = max(0.0, p_above_lower - p_above_upper)
    return max(0.0, min(100.0, p * 100.0))


def pop_iron_condor_short_pair(
    put_delta: float | None,
    call_delta: float | None,
) -> float:
    if put_delta is None or call_delta is None:
        return 0.0
    est = 1.0 - (abs(put_delta) + call_delta)
    return max(0.0, min(100.0, est * 100.0))


def breakevens_from_legs(legs: list[TradeLeg], lot_size: int) -> tuple[float | None, float | None]:
    """Approximate lower/upper breakevens for banded short-premium structures."""
    if not legs:
        return None, None
    net_credit = 0.0
    for leg in legs:
        units = leg.quantity / lot_size if lot_size > 0 else leg.quantity
        prem = leg.premium_per_unit
        if leg.side == "Sell":
            net_credit += prem * units
        else:
            net_credit -= prem * units
    strikes_call = [leg.strike for leg in legs if leg.right == "Call"]
    strikes_put = [leg.strike for leg in legs if leg.right == "Put"]
    if not strikes_call or not strikes_put:
        return None, None
    lower = min(strikes_put) - net_credit
    upper = max(strikes_call) + net_credit
    return lower, upper


def pop_for_legs(ctx: EngineContext, legs: list[TradeLeg]) -> float:
    if not legs or ctx.spot <= 0:
        return 0.0
    t = ctx.t_years
    sigma = sigma_for_pop(ctx)

    if len(legs) == 1:
        leg = legs[0]
        q = ctx.cache.get((leg.strike, leg.right))
        delta = q.delta if q and q.delta is not None else bs_delta(
            ctx.spot, float(leg.strike), t, sigma, leg.right
        )
        if leg.side == "Sell":
            return pop_short_otm(delta)
        return pop_long_otm(ctx.spot, float(leg.strike), leg.premium_per_unit, leg.right, t, sigma)

    if len(legs) == 2:
        sells = [l for l in legs if l.side == "Sell"]
        buys = [l for l in legs if l.side == "Buy"]
        if len(sells) == 2 and len(buys) == 0:
            net_credit = sum(l.premium_per_unit for l in sells)
            strikes = {l.right: l.strike for l in sells}
            if "Call" in strikes and "Put" in strikes:
                if strikes["Put"] == strikes["Call"]:
                    lower = strikes["Put"] - net_credit
                    upper = strikes["Call"] + net_credit
                else:
                    lower = strikes["Put"] - net_credit
                    upper = strikes["Call"] + net_credit
                return pop_between_breakevens(ctx.spot, lower, upper, t, sigma)
        if len(sells) == 1 and len(buys) == 1 and sells[0].right == buys[0].right:
            short_leg = sells[0]
            q = ctx.cache.get((short_leg.strike, short_leg.right))
            delta = q.delta if q and q.delta is not None else bs_delta(
                ctx.spot, float(short_leg.strike), t, sigma, short_leg.right
            )
            if short_leg.side == "Sell":
                return pop_short_otm(delta)

    lower, upper = breakevens_from_legs(legs, ctx.lot_size)
    if lower is not None and upper is not None and lower < upper:
        return pop_between_breakevens(ctx.spot, lower, upper, t, sigma)

    return 50.0


def expected_value_heuristic(pop_pct: float, max_profit: float, max_loss: float) -> float:
    pop = max(0.0, min(100.0, pop_pct)) / 100.0
    pol = 1.0 - pop
    profit = max(0.0, max_profit)
    loss = max(0.0, max_loss)
    return pop * profit - pol * loss
