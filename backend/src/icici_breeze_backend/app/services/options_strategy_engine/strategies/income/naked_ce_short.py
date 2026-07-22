"""Naked CE short strategy calculator."""
from __future__ import annotations

from icici_breeze_backend.app.services.options_strategy_engine.delta_anchor import (
    pop_to_short_delta,
    strikes_ranked_by_delta,
)
from icici_breeze_backend.app.services.options_strategy_engine.helpers import pre_filter_pop_floor, skip
from icici_breeze_backend.app.services.options_strategy_engine.pop import pop_for_legs
from icici_breeze_backend.app.services.options_strategy_engine.ranking import score_credit_trade
from icici_breeze_backend.app.services.options_strategy_engine.sizing import min_qty_for_one_lot
from icici_breeze_backend.app.services.options_strategy_engine.strategies.common import all_liquid, ok_with_pop
from icici_breeze_backend.app.services.options_strategy_engine.strategies.income._common import (
    income_constraint_violations,
    mark_relaxed_result,
)
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


def calc_naked_ce_short(ctx: EngineContext) -> StrategyResult:
    sid, name = "naked_ce_short", "Naked CE Short"
    if ctx.halted:
        return skip(sid, name, ctx.halt_reason or "Market halted")
    collector = audit_collector_for(ctx)
    if collector is not None:
        collector.min_pop_pct = ctx.min_pop_pct
    target = pop_to_short_delta(pre_filter_pop_floor(ctx.min_pop_pct), 1)
    L = ctx.lot_size
    candidates = strikes_ranked_by_delta(
        all_liquid(ctx, "Call"),
        ctx.cache,
        "Call",
        target,
        strike_filter=lambda s: s > ctx.atm_strike,
    )
    best: tuple[float, list[TradeLeg], float, float] | None = None
    best_relaxed: tuple[float, list[TradeLeg], float, float] | None = None

    for stp in candidates:
        q = ctx.cache.get((stp, "Call"))
        if not q or not q.liquid:
            record_simple_attempt(collector, reject_reason="illiquid", strike=stp)
            continue
        prem = q.best_bid_price or q.ltp
        qty = min_qty_for_one_lot(L)
        if qty < L:
            record_simple_attempt(collector, reject_reason="quantity", strike=stp)
            continue
        legs = [TradeLeg("Call", "Sell", stp, qty, prem)]
        pop = pop_for_legs(ctx, legs)
        max_profit = prem * qty
        score = score_credit_trade(pop, max_profit, float("inf"))
        if pop < ctx.min_pop_pct:
            record_simple_attempt(collector, reject_reason="pop_floor", pop_pct=pop, strike=stp)
            if best_relaxed is None or score > best_relaxed[0]:
                best_relaxed = (score, legs, max_profit, pop)
            continue
        record_simple_attempt(collector, pop_pct=pop, strike=stp)
        if collector is not None:
            collector.record_stage("passed_credit")
            collector.record_stage("passed_pop")
        if best is None or score > best[0]:
            best = (score, legs, max_profit, pop)

    if best:
        score, legs, max_profit, pop = best
        record_simple_winner(
            collector,
            legs,
            metrics={"pop_pct": pop, "net_credit": max_profit, "engine_score": score},
            stages_passed=list(_INCOME_STAGES),
        )
        return ok_with_pop(
            ctx,
            sid,
            name,
            legs,
            max_loss=None,
            max_profit=max_profit,
            rr=f"Unlimited : {max_profit:.0f}",
            modified=ctx.structure_modified,
            net_premium_val=max_profit,
        )

    if best_relaxed:
        score, legs, max_profit, pop = best_relaxed
        violations = income_constraint_violations(ctx, pop=pop)
        record_simple_winner(
            collector,
            legs,
            metrics={"pop_pct": pop, "net_credit": max_profit, "engine_score": score},
            stages_passed=list(_INCOME_STAGES),
        )
        result = ok_with_pop(
            ctx,
            sid,
            name,
            legs,
            max_loss=None,
            max_profit=max_profit,
            rr=f"Unlimited : {max_profit:.0f}",
            modified=ctx.structure_modified,
            net_premium_val=max_profit,
            require_pop=False,
        )
        if result.status != "ok":
            return result
        return mark_relaxed_result(result, violations)

    return skip(sid, name, "No naked CE short meets minimum PoP on the liquid chain.")


def prefetch_naked_ce_short(ctx: EngineContext) -> set[tuple[int, Right]]:
    from icici_breeze_backend.app.services.options_strategy_engine.strategies.common import (
        estimate_strike_for_abs_delta,
    )

    strike = estimate_strike_for_abs_delta(
        ctx, "Call", pop_to_short_delta(pre_filter_pop_floor(ctx.min_pop_pct), 1)
    )
    return {(strike, "Call")} if strike is not None else set()
