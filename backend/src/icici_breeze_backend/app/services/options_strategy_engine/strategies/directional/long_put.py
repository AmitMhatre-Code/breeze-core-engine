"""Long put strategy calculator."""
from __future__ import annotations

from icici_breeze_backend.app.services.options_strategy_engine.delta_anchor import strikes_ranked_by_delta
from icici_breeze_backend.app.services.options_strategy_engine.helpers import skip
from icici_breeze_backend.app.services.options_strategy_engine.pop import pop_for_legs
from icici_breeze_backend.app.services.options_strategy_engine.ranking import score_directional_candidate
from icici_breeze_backend.app.services.options_strategy_engine.sizing import size_quantity_from_budgets
from icici_breeze_backend.app.services.options_strategy_engine.strategies.common import all_liquid, make_result
from icici_breeze_backend.app.services.options_strategy_engine.strategies.directional._common import (
    delta_match,
    long_short_targets,
)
from icici_breeze_backend.app.services.options_strategy_engine.delta_anchor import profile_deltas
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

_DIRECTIONAL_STAGES = ("passed_liquidity", "passed_credit", "passed_pop", "returned")


def calc_long_put(ctx: EngineContext) -> StrategyResult:
    sid, name = "long_put", "Long Put"
    if ctx.halted:
        return skip(sid, name, ctx.halt_reason or "Market halted")
    collector = audit_collector_for(ctx)
    if collector is not None:
        collector.min_pop_pct = ctx.min_pop_pct
    long_target, _ = long_short_targets(ctx)
    L = ctx.lot_size
    candidates = strikes_ranked_by_delta(all_liquid(ctx, "Put"), ctx.cache, "Put", long_target)

    best: tuple[float, list[TradeLeg], float, float] | None = None
    for stp in candidates:
        q = ctx.cache[(stp, "Put")]
        if not delta_match(q.delta, long_target):
            record_simple_attempt(collector, reject_reason="delta_mismatch", strike=stp)
            continue
        buy_prem = q.best_offer_price or q.ltp
        debit_lot = buy_prem * L
        qty = size_quantity_from_budgets(
            sid,
            debit_lot,
            debit_lot,
            margin_rupees=ctx.margin_rupees,
            max_loss_rupees=ctx.max_loss_rupees,
            lot_size=L,
            unit_short_lots=0,
            spot=ctx.spot,
            provision_elm=ctx.provision_elm,
        )
        if qty < L:
            record_simple_attempt(collector, reject_reason="quantity", strike=stp)
            continue
        legs = [TradeLeg("Put", "Buy", stp, qty, buy_prem)]
        max_loss = buy_prem * qty
        if max_loss > ctx.max_loss_rupees:
            record_simple_attempt(
                collector,
                reject_reason="budget",
                strike=stp,
                max_loss=max_loss,
            )
            continue
        pop = pop_for_legs(ctx, legs)
        cer = score_directional_candidate(pop, float("inf"), max_loss)
        record_simple_attempt(collector, pop_pct=pop, strike=stp)
        if collector is not None:
            collector.record_stage("passed_credit")
            collector.record_stage("passed_pop")
        if best is None or cer > best[0]:
            best = (cer, legs, max_loss, pop)

    if not best:
        return skip(sid, name, "No long put meets delta profile and max-loss budget.")
    cer, legs, max_loss, pop = best
    record_simple_winner(
        collector,
        legs,
        metrics={
            "pop_pct": pop,
            "max_loss": max_loss,
            "net_credit": -max_loss,
            "engine_score": cer,
        },
        stages_passed=list(_DIRECTIONAL_STAGES),
    )
    return make_result(
        ctx, sid, name, legs,
        max_loss=max_loss,
        rr=f"{max_loss:.0f} : Unlimited",
        pop=pop,
        net_premium_val=-max_loss,
    )


def prefetch_long_put(ctx: EngineContext) -> set[tuple[int, Right]]:
    from icici_breeze_backend.app.services.options_strategy_engine.strategies.common import (
        estimate_strike_for_abs_delta,
    )

    long_target, _ = profile_deltas(ctx.risk_reward_profile)
    strike = estimate_strike_for_abs_delta(ctx, "Put", long_target)
    return {(strike, "Put")} if strike is not None else set()
