"""Bear call spread strategy calculator."""
from __future__ import annotations

from icici_breeze_backend.app.services.options_strategy_engine.delta_anchor import strikes_ranked_by_delta
from icici_breeze_backend.app.services.options_strategy_engine.helpers import skip
from icici_breeze_backend.app.services.options_strategy_engine.ranking import score_credit_trade
from icici_breeze_backend.app.services.options_strategy_engine.strategies.base import (
    all_liquid,
    credit_spread_wing_full,
    make_result,
)
from icici_breeze_backend.app.services.options_strategy_engine.strategies.income._common import short_delta
from icici_breeze_backend.app.services.options_strategy_engine.types import EngineContext, StrategyResult, TradeLeg


def calc_bear_call_spread(ctx: EngineContext) -> StrategyResult:
    sid, name = "bear_call_spread", "Bear Call Spread"
    if ctx.halted:
        return skip(sid, name, ctx.halt_reason or "Market halted")
    target = short_delta(ctx, 1)
    short_candidates = strikes_ranked_by_delta(
        all_liquid(ctx, "Call"),
        ctx.cache,
        "Call",
        target,
        strike_filter=lambda s: s >= ctx.atm_strike,
    )
    best_wing: tuple[float, int, int, float, float, int, float] | None = None
    for stp_s in short_candidates:
        wing = credit_spread_wing_full(ctx, stp_s, "Call", ctx.liquid_ce_strikes, True)
        if not wing:
            continue
        stp_l, credit, max_loss_u, qty, pop = wing
        score = score_credit_trade(pop, credit * qty, max_loss_u * qty)
        if best_wing is None or score > best_wing[0]:
            best_wing = (score, stp_s, stp_l, credit, max_loss_u, qty, pop)
    if not best_wing:
        return skip(sid, name, "No bear call spread meets minimum PoP within risk limits.")
    _, stp_s, stp_l, credit, max_loss_u, qty, pop = best_wing
    qs, ql = ctx.cache[(stp_s, "Call")], ctx.cache[(stp_l, "Call")]
    legs = [
        TradeLeg("Call", "Sell", stp_s, qty, qs.best_bid_price or qs.ltp),
        TradeLeg("Call", "Buy", stp_l, qty, ql.best_offer_price or ql.ltp),
    ]
    max_loss = max_loss_u * qty
    return make_result(
        ctx, sid, name, legs,
        max_loss=max_loss,
        rr=f"{max_loss:.0f} : {credit * qty:.0f}",
        pop=pop,
        net_premium_val=credit * qty,
    )
