"""Long condor strategy calculator."""
from __future__ import annotations

from icici_breeze_backend.app.services.options_strategy_engine.helpers import skip
from icici_breeze_backend.app.services.options_strategy_engine.pop import pop_for_legs
WING_WIDTH_MULTIPLIERS: tuple[int, ...] = (1, 2, 3, 4)
from icici_breeze_backend.app.services.options_strategy_engine.ranking import score_debit_trade
from icici_breeze_backend.app.services.options_strategy_engine.sizing import size_quantity_loss_only
from icici_breeze_backend.app.services.options_strategy_engine.strategies.common import (
    ensure_liquid_above,
    ensure_liquid_below,
    make_result,
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

_VOL_STAGES = ("passed_liquidity", "returned")


def _long_condor_wings(
    ctx: EngineContext,
    short_put: int,
    short_call: int,
    *,
    collector=None,
) -> tuple[int, int, float, float, int, float, float, list[TradeLeg], float] | None:
    L = ctx.lot_size
    liquid_pe = set(ctx.liquid_pe_strikes)
    liquid_ce = set(ctx.liquid_ce_strikes)
    best: tuple[float, int, int, float, float, int, float, float, list[TradeLeg]] | None = None
    for mult in WING_WIDTH_MULTIPLIERS:
        w = mult * ctx.strike_step
        lp = short_put - w
        lc = short_call + w
        if lp not in liquid_pe or lc not in liquid_ce:
            record_simple_attempt(
                collector,
                reject_reason="illiquid_wing",
                short_put=short_put,
                short_call=short_call,
                wing_width=w,
            )
            continue
        sp, sc = ctx.cache[(short_put, "Put")], ctx.cache[(short_call, "Call")]
        lpq, lcq = ctx.cache[(lp, "Put")], ctx.cache[(lc, "Call")]
        debit = (lpq.best_offer_price or lpq.ltp) - (sp.best_bid_price or sp.ltp)
        debit += (lcq.best_offer_price or lcq.ltp) - (sc.best_bid_price or sc.ltp)
        if debit <= 0:
            record_simple_attempt(
                collector,
                reject_reason="no_credit",
                short_put=short_put,
                short_call=short_call,
                wing_width=w,
            )
            continue
        max_loss_u = debit
        max_profit_u = w - debit
        if max_profit_u <= 0:
            record_simple_attempt(
                collector,
                reject_reason="economic_prune",
                short_put=short_put,
                short_call=short_call,
                wing_width=w,
            )
            continue
        loss_budget = ctx.effective_loss_sizing_budget()
        qty = size_quantity_loss_only(loss_budget, max_loss_u * L, L)
        if qty < L or (ctx.max_loss_rupees is not None and max_loss_u * qty > ctx.max_loss_rupees):
            record_simple_attempt(
                collector,
                reject_reason="budget",
                short_put=short_put,
                short_call=short_call,
                wing_width=w,
            )
            continue
        legs = [
            TradeLeg("Put", "Buy", lp, qty, lpq.best_offer_price or lpq.ltp),
            TradeLeg("Put", "Sell", short_put, qty, sp.best_bid_price or sp.ltp),
            TradeLeg("Call", "Sell", short_call, qty, sc.best_bid_price or sc.ltp),
            TradeLeg("Call", "Buy", lc, qty, lcq.best_offer_price or lcq.ltp),
        ]
        pop = pop_for_legs(ctx, legs)
        max_loss = max_loss_u * qty
        max_profit = max_profit_u * qty
        ev = score_debit_trade(pop, max_profit, max_loss)
        record_simple_attempt(
            collector,
            pop_pct=pop,
            short_put=short_put,
            short_call=short_call,
            wing_width=w,
            max_loss=max_loss,
        )
        if best is None or ev > best[0]:
            best = (ev, lp, lc, debit, max_loss_u, qty, pop, max_profit_u, legs)
    if not best:
        return None
    ev, lp, lc, debit, max_loss_u, qty, pop, max_profit_u, legs = best
    return lp, lc, debit, max_loss_u, qty, pop, max_profit_u, legs, ev


def calc_long_condor(ctx: EngineContext) -> StrategyResult:
    sid, name = "long_condor", "Long Condor"
    if ctx.halted:
        return skip(sid, name, ctx.halt_reason or "Market halted")
    collector = audit_collector_for(ctx)
    if collector is not None:
        collector.min_pop_pct = ctx.min_pop_pct
    stp_sp = ensure_liquid_below(ctx, ctx.range_lower, "Put", purpose="long_condor: short PE")
    stp_sc = ensure_liquid_above(ctx, ctx.range_upper, "Call", purpose="long_condor: short CE")
    if stp_sp is None or stp_sc is None:
        record_simple_attempt(collector, reject_reason="illiquid")
        return skip(sid, name, "Could not resolve long condor short strikes.")
    wings = _long_condor_wings(ctx, stp_sp, stp_sc, collector=collector)
    if not wings:
        return skip(sid, name, "No long condor wings meet risk limits within the outlook range.")
    _, _, debit, max_loss_u, qty, pop, max_profit_u, legs, ev = wings
    max_loss = max_loss_u * qty
    max_profit = max_profit_u * qty
    record_simple_winner(
        collector,
        legs,
        metrics={
            "pop_pct": pop,
            "max_loss": max_loss,
            "engine_score": ev,
        },
        stages_passed=list(_VOL_STAGES),
    )
    return make_result(
        ctx, sid, name, legs,
        max_loss=max_loss,
        rr=f"{max_loss:.0f} : {max_profit:.0f}",
        pop=pop,
        net_premium_val=-(debit * qty),
    )


def prefetch_long_condor(ctx: EngineContext) -> set[tuple[int, Right]]:
    pairs: set[tuple[int, Right]] = set()
    for strike in ctx.strikes:
        if strike <= ctx.range_lower:
            pairs.add((strike, "Put"))
        if strike >= ctx.range_upper:
            pairs.add((strike, "Call"))
    for mult in WING_WIDTH_MULTIPLIERS:
        spread = mult * ctx.strike_step
        for strike in ctx.strikes:
            if strike <= ctx.range_lower:
                pairs.add((strike - spread, "Put"))
            if strike >= ctx.range_upper:
                pairs.add((strike + spread, "Call"))
    return pairs
