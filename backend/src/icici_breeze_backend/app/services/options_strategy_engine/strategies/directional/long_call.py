"""Long call strategy calculator."""
from __future__ import annotations

from icici_breeze_backend.app.services.options_strategy_engine.delta_anchor import strikes_ranked_by_delta
from icici_breeze_backend.app.services.options_strategy_engine.helpers import skip
from icici_breeze_backend.app.services.options_strategy_engine.pop import pop_for_legs
from icici_breeze_backend.app.services.options_strategy_engine.ranking import score_directional_candidate
from icici_breeze_backend.app.services.options_strategy_engine.sizing import size_quantity_from_budgets
from icici_breeze_backend.app.services.options_strategy_engine.strategies.base import all_liquid, make_result
from icici_breeze_backend.app.services.options_strategy_engine.strategies.directional._common import (
    delta_match,
    long_short_targets,
)
from icici_breeze_backend.app.services.options_strategy_engine.types import EngineContext, StrategyResult, TradeLeg


def calc_long_call(ctx: EngineContext) -> StrategyResult:
    sid, name = "long_call", "Long Call"
    if ctx.halted:
        return skip(sid, name, ctx.halt_reason or "Market halted")
    long_target, _ = long_short_targets(ctx)
    L = ctx.lot_size
    candidates = strikes_ranked_by_delta(all_liquid(ctx, "Call"), ctx.cache, "Call", long_target)

    best: tuple[float, list[TradeLeg], float, float] | None = None
    for stp in candidates:
        q = ctx.cache[(stp, "Call")]
        if not delta_match(q.delta, long_target):
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
            leg_count=1,
            spot=ctx.spot,
            provision_elm=ctx.provision_elm,
        )
        if qty < L:
            continue
        legs = [TradeLeg("Call", "Buy", stp, qty, buy_prem)]
        max_loss = buy_prem * qty
        if max_loss > ctx.max_loss_rupees:
            continue
        pop = pop_for_legs(ctx, legs)
        cer = score_directional_candidate(pop, float("inf"), max_loss)
        if best is None or cer > best[0]:
            best = (cer, legs, max_loss, pop)

    if not best:
        return skip(sid, name, "No long call meets delta profile and max-loss budget.")
    _, legs, max_loss, pop = best
    return make_result(
        ctx, sid, name, legs,
        max_loss=max_loss,
        rr=f"{max_loss:.0f} : Unlimited",
        pop=pop,
        net_premium_val=-max_loss,
    )
