"""Iron butterfly strategy calculator."""
from __future__ import annotations

from icici_breeze_backend.app.services.options_strategy_engine.helpers import skip
from icici_breeze_backend.app.services.options_strategy_engine.strategies.base import (
    atm_with_liquidity,
    iron_wings_symmetric,
    make_result,
)
from icici_breeze_backend.app.services.options_strategy_engine.types import EngineContext, StrategyResult, TradeLeg


def calc_iron_butterfly(ctx: EngineContext) -> StrategyResult:
    sid, name = "iron_butterfly", "Iron Butterfly"
    if ctx.halted:
        return skip(sid, name, ctx.halt_reason or "Market halted")
    stp = atm_with_liquidity(ctx)
    if stp is None:
        return skip(sid, name, "No liquid ATM for iron butterfly.")
    wings = iron_wings_symmetric(ctx, stp, stp, strategy_id=sid)
    if not wings:
        return skip(sid, name, "No symmetric wings meet minimum PoP within risk limits.")
    lp, lc, credit, max_loss_u, qty, pop = wings
    ce, pe, lpq, lcq = ctx.cache[(stp, "Call")], ctx.cache[(stp, "Put")], ctx.cache[(lp, "Put")], ctx.cache[(lc, "Call")]
    legs = [
        TradeLeg("Put", "Sell", stp, qty, pe.best_bid_price or pe.ltp),
        TradeLeg("Put", "Buy", lp, qty, lpq.best_offer_price or lpq.ltp),
        TradeLeg("Call", "Sell", stp, qty, ce.best_bid_price or ce.ltp),
        TradeLeg("Call", "Buy", lc, qty, lcq.best_offer_price or lcq.ltp),
    ]
    max_loss = max_loss_u * qty
    return make_result(
        ctx, sid, name, legs,
        max_loss=max_loss,
        rr=f"{max_loss:.0f} : {credit * qty:.0f}",
        pop=pop,
        net_premium_val=credit * qty,
    )
