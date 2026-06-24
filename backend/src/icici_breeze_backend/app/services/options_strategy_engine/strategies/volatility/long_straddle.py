"""Long straddle strategy calculator."""
from __future__ import annotations

from icici_breeze_backend.app.services.options_strategy_engine.helpers import skip
from icici_breeze_backend.app.services.options_strategy_engine.pop import pop_for_legs
from icici_breeze_backend.app.services.options_strategy_engine.ranking import score_debit_trade
from icici_breeze_backend.app.services.options_strategy_engine.sizing import size_quantity_loss_only
from icici_breeze_backend.app.services.options_strategy_engine.strategies.common import make_result, windowed_liquid
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

_VOL_STAGES = ("passed_liquidity", "returned")


def calc_long_straddle(ctx: EngineContext) -> StrategyResult:
    sid, name = "long_straddle", "Long Straddle"
    if ctx.halted:
        return skip(sid, name, ctx.halt_reason or "Market halted")
    collector = audit_collector_for(ctx)
    if collector is not None:
        collector.min_pop_pct = ctx.min_pop_pct
    L = ctx.lot_size
    candidates = [
        s
        for s in windowed_liquid(ctx, sid, "Call")
        if ctx.range_lower <= s <= ctx.range_upper
        and (s, "Call") in ctx.cache
        and (s, "Put") in ctx.cache
        and ctx.cache[(s, "Call")].liquid
        and ctx.cache[(s, "Put")].liquid
    ]
    best: tuple[float, list[TradeLeg], float, float] | None = None
    for stp in sorted(candidates, key=lambda s: abs(s - ctx.atm_strike))[:5]:
        ce, pe = ctx.cache[(stp, "Call")], ctx.cache[(stp, "Put")]
        debit_lot = ((ce.best_offer_price or ce.ltp) + (pe.best_offer_price or pe.ltp)) * L
        qty = size_quantity_loss_only(ctx.effective_loss_sizing_budget(), debit_lot, L)
        if qty < L:
            record_simple_attempt(collector, reject_reason="quantity", strike=stp)
            continue
        legs = [
            TradeLeg("Call", "Buy", stp, qty, ce.best_offer_price or ce.ltp),
            TradeLeg("Put", "Buy", stp, qty, pe.best_offer_price or pe.ltp),
        ]
        max_loss = debit_lot * (qty // L)
        if ctx.max_loss_rupees is not None and max_loss > ctx.max_loss_rupees:
            record_simple_attempt(
                collector,
                reject_reason="budget",
                strike=stp,
                max_loss=max_loss,
            )
            continue
        pop = pop_for_legs(ctx, legs)
        ev = score_debit_trade(pop, float("inf"), max_loss)
        record_simple_attempt(collector, pop_pct=pop, strike=stp, max_loss=max_loss)
        if best is None or ev > best[0]:
            best = (ev, legs, max_loss, pop)
    if not best:
        return skip(sid, name, "No long straddle meets risk limits within the outlook range.")
    ev, legs, max_loss, pop = best
    record_simple_winner(
        collector,
        legs,
        metrics={"pop_pct": pop, "max_loss": max_loss, "engine_score": ev},
        stages_passed=list(_VOL_STAGES),
    )
    return make_result(
        ctx, sid, name, legs,
        max_loss=max_loss,
        rr=f"{max_loss:.0f} : Unlimited",
        pop=pop,
        net_premium_val=-max_loss,
    )


def prefetch_long_straddle(ctx: EngineContext) -> set[tuple[int, Right]]:
    pairs: set[tuple[int, Right]] = set()
    for strike in ctx.strikes:
        if ctx.range_lower <= strike <= ctx.range_upper:
            pairs.add((strike, "Call"))
            pairs.add((strike, "Put"))
    return pairs
