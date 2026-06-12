"""Short straddle strategy calculator."""
from __future__ import annotations

from icici_breeze_backend.app.services.options_strategy_engine.helpers import skip
from icici_breeze_backend.app.services.options_strategy_engine.sizing import min_qty_for_one_lot
from icici_breeze_backend.app.services.options_strategy_engine.strategies.common import atm_with_liquidity, ok_with_pop
from icici_breeze_backend.app.services.options_strategy_engine.types import (
    EngineContext,
    Right,
    StrategyResult,
    TradeLeg,
)


def calc_short_straddle(ctx: EngineContext) -> StrategyResult:
    sid, name = "short_straddle", "Short Straddle"
    if ctx.halted:
        return skip(sid, name, ctx.halt_reason or "Market halted")
    stp = atm_with_liquidity(ctx)
    if stp is None:
        return skip(sid, name, "No liquid ATM straddle strike.")
    ce, pe = ctx.cache[(stp, "Call")], ctx.cache[(stp, "Put")]
    prem_c, prem_p = ce.best_bid_price or ce.ltp, pe.best_bid_price or pe.ltp
    L = ctx.lot_size
    qty = min_qty_for_one_lot(L)
    if qty < L:
        return skip(sid, name, "Insufficient margin for one lot.")
    legs = [
        TradeLeg("Call", "Sell", stp, qty, prem_c),
        TradeLeg("Put", "Sell", stp, qty, prem_p),
    ]
    max_profit = (prem_c + prem_p) * qty
    return ok_with_pop(
        ctx, sid, name, legs,
        max_loss=None,
        rr=f"Unlimited : {max_profit:.0f}",
        modified=ctx.structure_modified,
        net_premium_val=max_profit,
    )


def prefetch_short_straddle(ctx: EngineContext) -> set[tuple[int, Right]]:
    from icici_breeze_backend.app.services.options_strategy_engine.strategies.common import prefetch_atm_pairs

    return prefetch_atm_pairs(ctx)
