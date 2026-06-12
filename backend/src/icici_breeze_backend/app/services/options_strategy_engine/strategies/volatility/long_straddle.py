"""Long straddle strategy calculator."""
from __future__ import annotations

from icici_breeze_backend.app.services.options_strategy_engine.helpers import skip
from icici_breeze_backend.app.services.options_strategy_engine.pop import pop_for_legs
from icici_breeze_backend.app.services.options_strategy_engine.ranking import score_debit_trade
from icici_breeze_backend.app.services.options_strategy_engine.sizing import size_quantity_loss_only
from icici_breeze_backend.app.services.options_strategy_engine.strategies.common import make_result, windowed_liquid
from icici_breeze_backend.app.services.options_strategy_engine.types import (
    EngineContext,
    Right,
    StrategyResult,
    TradeLeg,
)


def calc_long_straddle(ctx: EngineContext) -> StrategyResult:
    sid, name = "long_straddle", "Long Straddle"
    if ctx.halted:
        return skip(sid, name, ctx.halt_reason or "Market halted")
    L = ctx.lot_size
    candidates = [
        s
        for s in windowed_liquid(ctx, sid, "Call")
        if ctx.range_lower <= s <= ctx.range_upper
        and (s, "Call") in ctx.cache
        and (s, "Put") in ctx.cache
        and ctx.cache[(s, "Call")].liquid
        and ctx.cache[(s, "Put")].liquid
    ]
    best: tuple[float, list[TradeLeg], float, float] | None = None
    for stp in sorted(candidates, key=lambda s: abs(s - ctx.atm_strike))[:5]:
        ce, pe = ctx.cache[(stp, "Call")], ctx.cache[(stp, "Put")]
        debit_lot = ((ce.best_offer_price or ce.ltp) + (pe.best_offer_price or pe.ltp)) * L
        qty = size_quantity_loss_only(min(ctx.margin_rupees, ctx.max_loss_rupees), debit_lot, L)
        if qty < L:
            continue
        legs = [
            TradeLeg("Call", "Buy", stp, qty, ce.best_offer_price or ce.ltp),
            TradeLeg("Put", "Buy", stp, qty, pe.best_offer_price or pe.ltp),
        ]
        max_loss = debit_lot * (qty // L)
        if max_loss > ctx.max_loss_rupees:
            continue
        pop = pop_for_legs(ctx, legs)
        ev = score_debit_trade(pop, float("inf"), max_loss)
        if best is None or ev > best[0]:
            best = (ev, legs, max_loss, pop)
    if not best:
        return skip(sid, name, "No long straddle meets risk limits within the outlook range.")
    _, legs, max_loss, pop = best
    return make_result(
        ctx, sid, name, legs,
        max_loss=max_loss,
        rr=f"{max_loss:.0f} : Unlimited",
        pop=pop,
        net_premium_val=-max_loss,
    )


def prefetch_long_straddle(ctx: EngineContext) -> set[tuple[int, Right]]:
    pairs: set[tuple[int, Right]] = set()
    for strike in ctx.strikes:
        if ctx.range_lower <= strike <= ctx.range_upper:
            pairs.add((strike, "Call"))
            pairs.add((strike, "Put"))
    return pairs
