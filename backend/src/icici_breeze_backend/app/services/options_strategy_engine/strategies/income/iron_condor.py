"""Iron condor strategy calculator."""
from __future__ import annotations

from icici_breeze_backend.app.services.options_strategy_engine.delta_anchor import best_strike_near_delta
from icici_breeze_backend.app.services.options_strategy_engine.helpers import skip
from icici_breeze_backend.app.services.options_strategy_engine.pop import pop_for_legs
from icici_breeze_backend.app.services.options_strategy_engine.pruning import iron_condor_candidates
from icici_breeze_backend.app.services.options_strategy_engine.ranking import score_credit_trade
from icici_breeze_backend.app.services.options_strategy_engine.sizing import min_qty_for_one_lot
from icici_breeze_backend.app.services.options_strategy_engine.strategies.base import (
    all_liquid,
    iron_wings_symmetric,
    make_result,
)
from icici_breeze_backend.app.services.options_strategy_engine.strategies.income._common import short_delta
from icici_breeze_backend.app.services.options_strategy_engine.types import EngineContext, StrategyResult, TradeLeg


def calc_iron_condor(ctx: EngineContext) -> StrategyResult:
    sid, name = "iron_condor", "Iron Condor"
    if ctx.halted:
        return skip(sid, name, ctx.halt_reason or "Market halted")
    best: tuple[float, int, int, int, int, float, float, int, float] | None = None
    L = ctx.lot_size

    for lp, sp, sc, lc in iron_condor_candidates(ctx):
        spq, scq, lpq, lcq = ctx.cache[(sp, "Put")], ctx.cache[(sc, "Call")], ctx.cache[(lp, "Put")], ctx.cache[(lc, "Call")]
        sp_prem = spq.best_bid_price or spq.ltp
        sc_prem = scq.best_bid_price or scq.ltp
        lp_prem = lpq.best_offer_price or lpq.ltp
        lc_prem = lcq.best_offer_price or lcq.ltp
        credit = sp_prem + sc_prem - lp_prem - lc_prem
        w = sp - lp
        max_loss_u = w - credit
        if max_loss_u <= 0:
            continue
        qty = min_qty_for_one_lot(L)
        if qty < L:
            continue
        legs = [
            TradeLeg("Put", "Sell", sp, qty, sp_prem),
            TradeLeg("Put", "Buy", lp, qty, lp_prem),
            TradeLeg("Call", "Sell", sc, qty, sc_prem),
            TradeLeg("Call", "Buy", lc, qty, lc_prem),
        ]
        pop = pop_for_legs(ctx, legs)
        if pop < ctx.min_pop_pct:
            continue
        net_collected = credit * qty
        score = score_credit_trade(pop, net_collected, max_loss_u * qty)
        if best is None or score > best[0]:
            best = (score, lp, sp, sc, lc, credit, max_loss_u, qty, pop)

    if not best:
        target = short_delta(ctx, 2)
        stp_sp = best_strike_near_delta(
            all_liquid(ctx, "Put"), ctx.cache, "Put", target, strike_filter=lambda s: s < ctx.spot
        )
        stp_sc = best_strike_near_delta(
            all_liquid(ctx, "Call"), ctx.cache, "Call", target, strike_filter=lambda s: s > ctx.spot
        )
        if stp_sp is None or stp_sc is None:
            return skip(sid, name, "Could not resolve iron condor short strikes at target delta.")
        wings = iron_wings_symmetric(ctx, stp_sp, stp_sc, strategy_id=sid)
        if not wings:
            return skip(sid, name, "No symmetric wings meet minimum PoP within risk limits.")
        lp, lc, credit, max_loss_u, qty, pop = wings
        sp, sc = stp_sp, stp_sc
    else:
        _, lp, sp, sc, lc, credit, max_loss_u, qty, pop = best

    spq, scq, lpq, lcq = ctx.cache[(sp, "Put")], ctx.cache[(sc, "Call")], ctx.cache[(lp, "Put")], ctx.cache[(lc, "Call")]
    legs = [
        TradeLeg("Put", "Sell", sp, qty, spq.best_bid_price or spq.ltp),
        TradeLeg("Put", "Buy", lp, qty, lpq.best_offer_price or lpq.ltp),
        TradeLeg("Call", "Sell", sc, qty, scq.best_bid_price or scq.ltp),
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
