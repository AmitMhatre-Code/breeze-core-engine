"""Short straddle strategy calculator."""
from __future__ import annotations

from icici_breeze_backend.app.services.options_strategy_engine.helpers import skip
from icici_breeze_backend.app.services.options_strategy_engine.pop import pop_for_legs
from icici_breeze_backend.app.services.options_strategy_engine.sizing import min_qty_for_one_lot
from icici_breeze_backend.app.services.options_strategy_engine.strategies.common import atm_with_liquidity, ok_with_pop
from icici_breeze_backend.app.services.options_strategy_engine.types import (
    EngineContext,
    Right,
    StrategyResult,
    TradeLeg,
)
from icici_breeze_backend.audit.strategy_evaluation_audit import (
    audit_collector_for,
    record_simple_attempt,
    record_simple_winner,
)

_INCOME_STAGES = ("passed_liquidity", "passed_credit", "passed_pop", "returned")


def calc_short_straddle(ctx: EngineContext) -> StrategyResult:
    sid, name = "short_straddle", "Short Straddle"
    if ctx.halted:
        return skip(sid, name, ctx.halt_reason or "Market halted")
    collector = audit_collector_for(ctx)
    if collector is not None:
        collector.min_pop_pct = ctx.min_pop_pct
    stp = atm_with_liquidity(ctx)
    if stp is None:
        record_simple_attempt(collector, reject_reason="illiquid")
        return skip(sid, name, "No liquid ATM straddle strike.")
    ce, pe = ctx.cache[(stp, "Call")], ctx.cache[(stp, "Put")]
    prem_c, prem_p = ce.best_bid_price or ce.ltp, pe.best_bid_price or pe.ltp
    L = ctx.lot_size
    qty = min_qty_for_one_lot(L)
    if qty < L:
        record_simple_attempt(collector, reject_reason="quantity", strike=stp)
        return skip(sid, name, "Insufficient margin for one lot.")
    legs = [
        TradeLeg("Call", "Sell", stp, qty, prem_c),
        TradeLeg("Put", "Sell", stp, qty, prem_p),
    ]
    pop = pop_for_legs(ctx, legs)
    if pop < ctx.min_pop_pct:
        record_simple_attempt(collector, reject_reason="pop_floor", pop_pct=pop, strike=stp)
        return skip(
            sid,
            name,
            f"PoP {pop:.1f}% below minimum {ctx.min_pop_pct:.1f}%.",
        )
    max_profit = (prem_c + prem_p) * qty
    record_simple_attempt(collector, pop_pct=pop, strike=stp, credit=prem_c + prem_p)
    if collector is not None:
        collector.record_stage("passed_credit")
        collector.record_stage("passed_pop")
    record_simple_winner(
        collector,
        legs,
        metrics={"pop_pct": pop, "net_credit": max_profit},
        stages_passed=list(_INCOME_STAGES),
    )
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
