"""Naked PE short strategy calculator."""
from __future__ import annotations

from icici_breeze_backend.app.services.options_strategy_engine.delta_anchor import (
    pop_to_short_delta,
    strikes_ranked_by_delta,
)
from icici_breeze_backend.app.services.options_strategy_engine.helpers import skip
from icici_breeze_backend.app.services.options_strategy_engine.pop import pop_for_legs
from icici_breeze_backend.app.services.options_strategy_engine.ranking import score_credit_trade
from icici_breeze_backend.app.services.options_strategy_engine.sizing import min_qty_for_one_lot
from icici_breeze_backend.app.services.options_strategy_engine.strategies.common import all_liquid, ok_with_pop
from icici_breeze_backend.app.services.options_strategy_engine.types import (
    EngineContext,
    Right,
    StrategyResult,
    TradeLeg,
)


def calc_naked_pe_short(ctx: EngineContext) -> StrategyResult:
    sid, name = "naked_pe_short", "Naked PE Short"
    if ctx.halted:
        return skip(sid, name, ctx.halt_reason or "Market halted")
    target = pop_to_short_delta(ctx.min_pop_pct, 1)
    L = ctx.lot_size
    candidates = strikes_ranked_by_delta(
        all_liquid(ctx, "Put"),
        ctx.cache,
        "Put",
        target,
        strike_filter=lambda s: s < ctx.atm_strike,
    )
    best: tuple[float, list[TradeLeg], float] | None = None

    for stp in candidates:
        q = ctx.cache.get((stp, "Put"))
        if not q or not q.liquid:
            continue
        prem = q.best_bid_price or q.ltp
        qty = min_qty_for_one_lot(L)
        if qty < L:
            continue
        legs = [TradeLeg("Put", "Sell", stp, qty, prem)]
        pop = pop_for_legs(ctx, legs)
        if pop < ctx.min_pop_pct:
            continue
        max_profit = prem * qty
        score = score_credit_trade(pop, max_profit, float("inf"))
        if best is None or score > best[0]:
            best = (score, legs, max_profit)

    if not best:
        return skip(sid, name, "No naked PE short meets minimum PoP on the liquid chain.")
    _, legs, max_profit = best
    return ok_with_pop(
        ctx,
        sid,
        name,
        legs,
        max_loss=None,
        rr=f"Unlimited : {max_profit:.0f}",
        modified=ctx.structure_modified,
        net_premium_val=max_profit,
    )


def prefetch_naked_pe_short(ctx: EngineContext) -> set[tuple[int, Right]]:
    from icici_breeze_backend.app.services.options_strategy_engine.strategies.common import (
        estimate_strike_for_abs_delta,
    )

    strike = estimate_strike_for_abs_delta(ctx, "Put", pop_to_short_delta(ctx.min_pop_pct, 1))
    return {(strike, "Put")} if strike is not None else set()
