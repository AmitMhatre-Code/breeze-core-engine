"""Analytic PoP proxies per Gemini §3.2 (no Monte Carlo in engine path)."""
from __future__ import annotations

from dataclasses import dataclass

from icici_breeze_backend.app.services.options_strategy_engine.greeks import (
    prob_above_strike,
    prob_below_strike,
)
from icici_breeze_backend.app.services.options_strategy_engine.iv_smile import resolve_leg_sigma
from icici_breeze_backend.app.services.options_strategy_engine.types import EngineContext, Right, TradeLeg


def pop_short_breakeven(
    spot: float,
    strike: float,
    premium: float,
    right: Right,
    t: float,
    sigma: float,
) -> float:
    """Mirrors `pop_long_otm`'s breakeven, but a short position profits on the opposite side
    of it: 1-|delta| (the previous approximation) omits the premium cushion entirely and
    conflates N(d1) with N(d2), understating PoP most where the premium and the N(d1)/N(d2)
    gap are both largest — at the money."""
    if right == "Call":
        be = strike + premium
        p = prob_below_strike(spot, be, t, sigma)
    else:
        be = strike - premium
        p = prob_above_strike(spot, be, t, sigma)
    return max(0.0, min(100.0, p * 100.0))


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
    sigma_lower: float,
    sigma_upper: float,
) -> float:
    """`sigma_lower`/`sigma_upper` are independent: this is two closed-form N(d2) evaluations,
    not a shared-path simulation, so each breakeven can (and should) use its own side's
    smile-resolved sigma rather than one blended value."""
    if lower >= upper:
        return 0.0
    p_above_lower = prob_above_strike(spot, lower, t, sigma_lower)
    p_above_upper = prob_above_strike(spot, upper, t, sigma_upper)
    p = max(0.0, p_above_lower - p_above_upper)
    return max(0.0, min(100.0, p * 100.0))


@dataclass(frozen=True)
class PopDetail:
    """PoP result with explicit strike/breakeven methodology for audit trails."""

    pop_pct: float
    basis: str
    lower_breakeven: float | None = None
    upper_breakeven: float | None = None
    short_put: int | None = None
    short_call: int | None = None
    long_put: int | None = None
    long_call: int | None = None
    net_credit_per_unit: float | None = None

    def to_audit_dict(self) -> dict[str, object]:
        return {
            "pop_pct": round(self.pop_pct, 4),
            "pop_basis": self.basis,
            "lower_breakeven": (
                round(self.lower_breakeven, 4) if self.lower_breakeven is not None else None
            ),
            "upper_breakeven": (
                round(self.upper_breakeven, 4) if self.upper_breakeven is not None else None
            ),
            "short_put": self.short_put,
            "short_call": self.short_call,
            "long_put": self.long_put,
            "long_call": self.long_call,
            "net_credit_per_unit": (
                round(self.net_credit_per_unit, 4)
                if self.net_credit_per_unit is not None
                else None
            ),
        }


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


def pop_detail_for_legs(ctx: EngineContext, legs: list[TradeLeg]) -> PopDetail:
    if not legs or ctx.spot <= 0:
        return PopDetail(0.0, "unavailable")

    t = ctx.t_years

    if len(legs) == 1:
        leg = legs[0]
        prem = leg.premium_per_unit
        if leg.side == "Sell":
            be = leg.strike + prem if leg.right == "Call" else leg.strike - prem
            sigma_be = resolve_leg_sigma(ctx, be, leg.right)
            return PopDetail(
                pop_short_breakeven(ctx.spot, float(leg.strike), prem, leg.right, t, sigma_be),
                "short_strike_breakeven",
                lower_breakeven=be if leg.right == "Put" else None,
                upper_breakeven=be if leg.right == "Call" else None,
            )
        if leg.right == "Call":
            lower = leg.strike + prem
            upper = None
            sigma_be = resolve_leg_sigma(ctx, lower, leg.right)
        else:
            lower = None
            upper = leg.strike - prem
            sigma_be = resolve_leg_sigma(ctx, upper, leg.right)
        return PopDetail(
            pop_long_otm(ctx.spot, float(leg.strike), prem, leg.right, t, sigma_be),
            "long_strike_breakeven",
            lower_breakeven=lower,
            upper_breakeven=upper,
        )

    if len(legs) == 2:
        sells = [l for l in legs if l.side == "Sell"]
        buys = [l for l in legs if l.side == "Buy"]
        if len(sells) == 2 and len(buys) == 0:
            net_credit = sum(l.premium_per_unit for l in sells)
            strikes = {l.right: l.strike for l in sells}
            if "Call" in strikes and "Put" in strikes:
                lower = strikes["Put"] - net_credit
                upper = strikes["Call"] + net_credit
                sigma_lower = resolve_leg_sigma(ctx, lower, "Put")
                sigma_upper = resolve_leg_sigma(ctx, upper, "Call")
                return PopDetail(
                    pop_between_breakevens(ctx.spot, lower, upper, t, sigma_lower, sigma_upper),
                    "breakevens_from_short_strikes",
                    lower_breakeven=lower,
                    upper_breakeven=upper,
                    short_put=strikes["Put"],
                    short_call=strikes["Call"],
                    net_credit_per_unit=net_credit,
                )
        if len(sells) == 1 and len(buys) == 1 and sells[0].right == buys[0].right:
            short_leg = sells[0]
            buy_leg = buys[0]
            right = short_leg.right
            net_credit = short_leg.premium_per_unit - buy_leg.premium_per_unit
            be = short_leg.strike + net_credit if right == "Call" else short_leg.strike - net_credit
            sigma_be = resolve_leg_sigma(ctx, be, right)
            return PopDetail(
                pop_short_breakeven(ctx.spot, float(short_leg.strike), net_credit, right, t, sigma_be),
                "short_strike_breakeven",
                lower_breakeven=be if right == "Put" else None,
                upper_breakeven=be if right == "Call" else None,
                net_credit_per_unit=net_credit,
            )

    if len(legs) == 4:
        sells = [l for l in legs if l.side == "Sell"]
        buys = [l for l in legs if l.side == "Buy"]
        sell_put = next((l for l in sells if l.right == "Put"), None)
        sell_call = next((l for l in sells if l.right == "Call"), None)
        buy_put = next((l for l in buys if l.right == "Put"), None)
        buy_call = next((l for l in buys if l.right == "Call"), None)
        if len(sells) == 2 and len(buys) == 2 and sell_put and sell_call:
            net_credit = (
                sell_put.premium_per_unit
                + sell_call.premium_per_unit
                - sum(l.premium_per_unit for l in buys)
            )
            lower = sell_put.strike - net_credit
            upper = sell_call.strike + net_credit
            sigma_lower = resolve_leg_sigma(ctx, lower, "Put")
            sigma_upper = resolve_leg_sigma(ctx, upper, "Call")
            return PopDetail(
                pop_between_breakevens(ctx.spot, lower, upper, t, sigma_lower, sigma_upper),
                "breakevens_from_short_strikes",
                lower_breakeven=lower,
                upper_breakeven=upper,
                short_put=sell_put.strike,
                short_call=sell_call.strike,
                long_put=buy_put.strike if buy_put else None,
                long_call=buy_call.strike if buy_call else None,
                net_credit_per_unit=net_credit,
            )

    lower, upper = breakevens_from_legs(legs, ctx.lot_size)
    if lower is not None and upper is not None and lower < upper:
        puts = [l.strike for l in legs if l.right == "Put"]
        calls = [l.strike for l in legs if l.right == "Call"]
        net_credit = sum(
            l.premium_per_unit if l.side == "Sell" else -l.premium_per_unit for l in legs
        )
        sigma_lower = resolve_leg_sigma(ctx, lower, "Put")
        sigma_upper = resolve_leg_sigma(ctx, upper, "Call")
        return PopDetail(
            pop_between_breakevens(ctx.spot, lower, upper, t, sigma_lower, sigma_upper),
            "breakevens_from_long_strikes",
            lower_breakeven=lower,
            upper_breakeven=upper,
            long_put=min(puts) if puts else None,
            long_call=max(calls) if calls else None,
            net_credit_per_unit=net_credit,
        )

    return PopDetail(50.0, "fallback_default")


def pop_for_legs(ctx: EngineContext, legs: list[TradeLeg]) -> float:
    return pop_detail_for_legs(ctx, legs).pop_pct


def expected_value_heuristic(pop_pct: float, max_profit: float, max_loss: float) -> float:
    pop = max(0.0, min(100.0, pop_pct)) / 100.0
    pol = 1.0 - pop
    profit = max(0.0, max_profit)
    loss = max(0.0, max_loss)
    return pop * profit - pol * loss
