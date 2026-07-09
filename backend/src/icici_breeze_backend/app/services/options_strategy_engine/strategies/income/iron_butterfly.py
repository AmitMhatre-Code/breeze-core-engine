"""Iron butterfly strategy calculator."""
from __future__ import annotations

from icici_breeze_backend.app.services.options_strategy_engine.helpers import skip
from icici_breeze_backend.app.services.options_strategy_engine.strategies.common import (
    atm_with_liquidity,
    make_result,
)
from icici_breeze_backend.app.services.options_strategy_engine.strategies.income._common import (
    income_constraint_violations,
    mark_relaxed_result,
)
from icici_breeze_backend.app.services.options_strategy_engine.strategies.income.iron_condor import (
    enumerate_symmetric_iron_condors,
    prefetch_iron_condor_strikes,
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

_INCOME_STAGES = (
    "passed_liquidity",
    "passed_credit",
    "passed_economic_prune",
    "passed_pop",
    "returned",
)


def calc_iron_butterfly(ctx: EngineContext) -> StrategyResult:
    sid, name = "iron_butterfly", "Iron Butterfly"
    if ctx.halted:
        return skip(sid, name, ctx.halt_reason or "Market halted")
    collector = audit_collector_for(ctx)
    if collector is not None:
        collector.min_pop_pct = ctx.min_pop_pct
    stp = atm_with_liquidity(ctx)
    if stp is None:
        record_simple_attempt(collector, reject_reason="illiquid")
        return skip(sid, name, "No liquid ATM for iron butterfly.")
    candidates = enumerate_symmetric_iron_condors(ctx, stp, stp, stats=collector)
    if not candidates:
        candidates = enumerate_symmetric_iron_condors(
            ctx, stp, stp, stats=collector, enforce_pop=False
        )
    if not candidates:
        return skip(sid, name, "No symmetric wings meet minimum PoP within risk limits.")
    cand = max(candidates, key=lambda c: (c.final_score, c.net_collected, c.pop))
    lp, lc, credit, max_loss_u, qty, pop = (
        cand.long_put,
        cand.long_call,
        cand.credit,
        cand.max_loss_u,
        cand.qty,
        cand.pop,
    )
    ce, pe, lpq, lcq = ctx.cache[(stp, "Call")], ctx.cache[(stp, "Put")], ctx.cache[(lp, "Put")], ctx.cache[(lc, "Call")]
    legs = [
        TradeLeg("Put", "Sell", stp, qty, pe.best_bid_price or pe.ltp),
        TradeLeg("Put", "Buy", lp, qty, lpq.best_offer_price or lpq.ltp),
        TradeLeg("Call", "Sell", stp, qty, ce.best_bid_price or ce.ltp),
        TradeLeg("Call", "Buy", lc, qty, lcq.best_offer_price or lcq.ltp),
    ]
    max_loss = max_loss_u * qty
    violations = income_constraint_violations(ctx, pop=pop)
    record_simple_winner(
        collector,
        legs,
        metrics={
            "pop_pct": pop,
            "net_credit": credit * qty,
            "max_loss": max_loss,
            "engine_score": cand.final_score,
        },
        stages_passed=list(_INCOME_STAGES),
    )
    result = make_result(
        ctx, sid, name, legs,
        max_loss=max_loss,
        rr=f"{max_loss:.0f} : {credit * qty:.0f}",
        pop=pop,
        net_premium_val=credit * qty,
        max_profit=credit * qty,
    )
    if violations:
        return mark_relaxed_result(result, violations)
    return result


def prefetch_iron_butterfly(ctx: EngineContext) -> set[tuple[int, Right]]:
    from icici_breeze_backend.app.services.options_strategy_engine.strategies.common import prefetch_atm_pairs

    return prefetch_atm_pairs(ctx) | prefetch_iron_condor_strikes(ctx)
