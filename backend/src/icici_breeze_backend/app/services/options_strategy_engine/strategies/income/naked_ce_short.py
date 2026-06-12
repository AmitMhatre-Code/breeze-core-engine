"""Naked CE short strategy calculator."""
from __future__ import annotations

from icici_breeze_backend.app.services.options_strategy_engine.delta_anchor import best_strike_near_delta
from icici_breeze_backend.app.services.options_strategy_engine.helpers import skip
from icici_breeze_backend.app.services.options_strategy_engine.sizing import size_quantity_margin_only
from icici_breeze_backend.app.services.options_strategy_engine.strategies.base import all_liquid, ok_with_pop
from icici_breeze_backend.app.services.options_strategy_engine.strategies.income._common import short_delta
from icici_breeze_backend.app.services.options_strategy_engine.types import EngineContext, StrategyResult, TradeLeg


def calc_naked_ce_short(ctx: EngineContext) -> StrategyResult:
    sid, name = "naked_ce_short", "Naked CE Short"
    if ctx.halted:
        return skip(sid, name, ctx.halt_reason or "Market halted")
    target = short_delta(ctx, 1)
    stp = best_strike_near_delta(
        all_liquid(ctx, "Call"),
        ctx.cache,
        "Call",
        target,
        strike_filter=lambda s: s > ctx.atm_strike,
    )
    if stp is None:
        return skip(sid, name, "No liquid OTM CE near target delta.")
    q = ctx.cache.get((stp, "Call"))
    if not q:
        return skip(sid, name, "Quote missing for selected strike.")
    prem = q.best_bid_price or q.ltp
    L = ctx.lot_size
    qty = size_quantity_margin_only(ctx.margin_rupees, prem * L * 2, L)
    if qty < L:
        return skip(sid, name, "Insufficient margin for one lot.")
    legs = [TradeLeg("Call", "Sell", stp, qty, prem)]
    max_profit = prem * qty
    return ok_with_pop(
        ctx,
        sid,
        name,
        legs,
        max_loss=None,
        rr=f"Unlimited : {max_profit:.0f}",
        modified=ctx.structure_modified,
        net_premium_val=max_profit,
        require_pop=False,
    )
