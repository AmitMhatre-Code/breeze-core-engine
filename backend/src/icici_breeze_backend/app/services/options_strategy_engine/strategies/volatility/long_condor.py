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


def _long_condor_wings(
    ctx: EngineContext,
    short_put: int,
    short_call: int,
) -> tuple[int, int, float, float, int, float, float] | None:
    L = ctx.lot_size
    liquid_pe = set(ctx.liquid_pe_strikes)
    liquid_ce = set(ctx.liquid_ce_strikes)
    best: tuple[float, int, int, float, float, int, float, float] | None = None
    for mult in WING_WIDTH_MULTIPLIERS:
        w = mult * ctx.strike_step
        lp = short_put - w
        lc = short_call + w
        if lp not in liquid_pe or lc not in liquid_ce:
            continue
        sp, sc = ctx.cache[(short_put, "Put")], ctx.cache[(short_call, "Call")]
        lpq, lcq = ctx.cache[(lp, "Put")], ctx.cache[(lc, "Call")]
        debit = (lpq.best_offer_price or lpq.ltp) - (sp.best_bid_price or sp.ltp)
        debit += (lcq.best_offer_price or lcq.ltp) - (sc.best_bid_price or sc.ltp)
        if debit <= 0:
            continue
        max_loss_u = debit
        max_profit_u = w - debit
        if max_profit_u <= 0:
            continue
        qty = size_quantity_loss_only(ctx.max_loss_rupees, max_loss_u * L, L)
        if qty < L or max_loss_u * qty > ctx.max_loss_rupees:
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
        if best is None or ev > best[0]:
            best = (ev, lp, lc, debit, max_loss_u, qty, pop, max_profit_u)
    if not best:
        return None
    _, lp, lc, debit, max_loss_u, qty, pop, max_profit_u = best
    return lp, lc, debit, max_loss_u, qty, pop, max_profit_u


def calc_long_condor(ctx: EngineContext) -> StrategyResult:
    sid, name = "long_condor", "Long Condor"
    if ctx.halted:
        return skip(sid, name, ctx.halt_reason or "Market halted")
    stp_sp = ensure_liquid_below(ctx, ctx.range_lower, "Put", purpose="long_condor: short PE")
    stp_sc = ensure_liquid_above(ctx, ctx.range_upper, "Call", purpose="long_condor: short CE")
    if stp_sp is None or stp_sc is None:
        return skip(sid, name, "Could not resolve long condor short strikes.")
    wings = _long_condor_wings(ctx, stp_sp, stp_sc)
    if not wings:
        return skip(sid, name, "No long condor wings meet risk limits within the outlook range.")
    lp, lc, debit, max_loss_u, qty, pop, max_profit_u = wings
    sp, sc, lpq, lcq = (
        ctx.cache[(stp_sp, "Put")],
        ctx.cache[(stp_sc, "Call")],
        ctx.cache[(lp, "Put")],
        ctx.cache[(lc, "Call")],
    )
    legs = [
        TradeLeg("Put", "Buy", lp, qty, lpq.best_offer_price or lpq.ltp),
        TradeLeg("Put", "Sell", stp_sp, qty, sp.best_bid_price or sp.ltp),
        TradeLeg("Call", "Sell", stp_sc, qty, sc.best_bid_price or sc.ltp),
        TradeLeg("Call", "Buy", lc, qty, lcq.best_offer_price or lcq.ltp),
    ]
    max_loss = max_loss_u * qty
    max_profit = max_profit_u * qty
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
