"""Income strategy calculators (Gemini §4.1)."""
from __future__ import annotations

from icici_breeze_backend.app.services.options_strategy_engine.anchors import STRANGLE_OTM_PAIRS
from icici_breeze_backend.app.services.options_strategy_engine.helpers import skip
from icici_breeze_backend.app.services.options_strategy_engine.pop import pop_for_legs
from icici_breeze_backend.app.services.options_strategy_engine.pruning import (
    DELTA_INCOME_SHORT,
    iron_condor_candidates,
    top_k_strikes,
)
from icici_breeze_backend.app.services.options_strategy_engine.ranking import score_credit_trade
from icici_breeze_backend.app.services.options_strategy_engine.sizing import (
    size_quantity_loss_only,
    size_quantity_margin_only,
)
from icici_breeze_backend.app.services.options_strategy_engine.strategies.base import (
    anchors_for,
    atm_with_liquidity,
    credit_spread_wing,
    ensure_liquid_above,
    ensure_liquid_below,
    iron_wings_symmetric,
    make_result,
    ok_with_pop,
    windowed_liquid,
)
from icici_breeze_backend.app.services.options_strategy_engine.types import EngineContext, StrategyResult, TradeLeg


def calc_naked_ce_short(ctx: EngineContext) -> StrategyResult:
    sid, name = "naked_ce_short", "Naked CE Short"
    if ctx.halted:
        return skip(sid, name, ctx.halt_reason or "Market halted")
    candidates = top_k_strikes(
        [s for s in windowed_liquid(ctx, sid, "Call") if s > ctx.range_upper],
        ctx.cache,
        "Call",
        1,
        credit=True,
        delta_window=DELTA_INCOME_SHORT,
    )
    stp = candidates[0] if candidates else ensure_liquid_above(ctx, ctx.range_upper, "Call")
    if stp is None:
        return skip(sid, name, "No liquid CE strike above range upper bound.")
    q = ctx.cache.get((stp, "Call"))
    if not q:
        return skip(sid, name, "Quote missing for selected strike.")
    prem = q.best_bid_price or q.ltp
    L = ctx.lot_size
    qty = size_quantity_margin_only(ctx.margin_rupees, prem * L * 2, L)
    if qty < L:
        return skip(sid, name, "Insufficient margin for one lot.")
    legs = [TradeLeg("Call", "Sell", stp, qty, prem)]
    max_profit = prem * qty
    return ok_with_pop(
        ctx,
        sid,
        name,
        legs,
        max_loss=None,
        rr=f"Unlimited : {max_profit:.0f}",
        modified=ctx.structure_modified,
        net_premium_val=max_profit,
    )


def calc_naked_pe_short(ctx: EngineContext) -> StrategyResult:
    sid, name = "naked_pe_short", "Naked PE Short"
    if ctx.halted:
        return skip(sid, name, ctx.halt_reason or "Market halted")
    candidates = top_k_strikes(
        [s for s in windowed_liquid(ctx, sid, "Put") if s < ctx.range_lower],
        ctx.cache,
        "Put",
        1,
        credit=True,
        delta_window=DELTA_INCOME_SHORT,
    )
    stp = candidates[0] if candidates else ensure_liquid_below(ctx, ctx.range_lower, "Put")
    if stp is None:
        return skip(sid, name, "No liquid PE strike below range lower bound.")
    q = ctx.cache.get((stp, "Put"))
    if not q:
        return skip(sid, name, "Quote missing for selected strike.")
    prem = q.best_bid_price or q.ltp
    L = ctx.lot_size
    qty = size_quantity_margin_only(ctx.margin_rupees, prem * L * 2, L)
    if qty < L:
        return skip(sid, name, "Insufficient margin for one lot.")
    legs = [TradeLeg("Put", "Sell", stp, qty, prem)]
    max_profit = prem * qty
    max_risk = (stp - prem) * qty
    if max_risk > ctx.max_loss_rupees:
        return skip(sid, name, "Naked PE max risk exceeds user max loss budget.")
    return ok_with_pop(
        ctx,
        sid,
        name,
        legs,
        max_loss=max_risk,
        rr=f"{max_risk:.0f} : {max_profit:.0f}",
        modified=ctx.structure_modified,
        net_premium_val=max_profit,
    )


def calc_bear_call_spread(ctx: EngineContext) -> StrategyResult:
    sid, name = "bear_call_spread", "Bear Call Spread"
    if ctx.halted:
        return skip(sid, name, ctx.halt_reason or "Market halted")
    short_candidates = top_k_strikes(
        [s for s in windowed_liquid(ctx, sid, "Call") if s >= ctx.range_upper],
        ctx.cache,
        "Call",
        3,
        credit=True,
        delta_window=DELTA_INCOME_SHORT,
    )
    stp_s = short_candidates[0] if short_candidates else ensure_liquid_above(
        ctx, ctx.range_upper, "Call", purpose="bear_call_spread: short CE above range upper"
    )
    if stp_s is None:
        return skip(sid, name, "No liquid short CE above range.")
    wing = credit_spread_wing(ctx, stp_s, "Call", ctx.liquid_ce_strikes, True, strategy_id=sid)
    if not wing:
        return skip(sid, name, "No viable call wing meets minimum PoP within risk limits.")
    stp_l, credit, max_loss_u, qty, pop = wing
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


def calc_bull_put_spread(ctx: EngineContext) -> StrategyResult:
    sid, name = "bull_put_spread", "Bull Put Spread"
    if ctx.halted:
        return skip(sid, name, ctx.halt_reason or "Market halted")
    short_candidates = top_k_strikes(
        [s for s in windowed_liquid(ctx, sid, "Put") if s <= ctx.range_lower],
        ctx.cache,
        "Put",
        3,
        credit=True,
        delta_window=DELTA_INCOME_SHORT,
    )
    stp_s = short_candidates[0] if short_candidates else ensure_liquid_below(
        ctx, ctx.range_lower, "Put", purpose="bull_put_spread: short PE below range lower"
    )
    if stp_s is None:
        return skip(sid, name, "No liquid short PE below range.")
    wing = credit_spread_wing(ctx, stp_s, "Put", ctx.liquid_pe_strikes, False, strategy_id=sid)
    if not wing:
        return skip(sid, name, "No viable put wing meets minimum PoP within risk limits.")
    stp_l, credit, max_loss_u, qty, pop = wing
    qs, ql = ctx.cache[(stp_s, "Put")], ctx.cache[(stp_l, "Put")]
    legs = [
        TradeLeg("Put", "Sell", stp_s, qty, qs.best_bid_price or qs.ltp),
        TradeLeg("Put", "Buy", stp_l, qty, ql.best_offer_price or ql.ltp),
    ]
    max_loss = max_loss_u * qty
    return make_result(
        ctx, sid, name, legs,
        max_loss=max_loss,
        rr=f"{max_loss:.0f} : {credit * qty:.0f}",
        pop=pop,
        net_premium_val=credit * qty,
    )


def calc_short_straddle(ctx: EngineContext) -> StrategyResult:
    sid, name = "short_straddle", "Short Straddle"
    if ctx.halted:
        return skip(sid, name, ctx.halt_reason or "Market halted")
    stp = atm_with_liquidity(ctx)
    if stp is None:
        return skip(sid, name, "No liquid ATM straddle strike.")
    ce, pe = ctx.cache[(stp, "Call")], ctx.cache[(stp, "Put")]
    prem_c, prem_p = ce.best_bid_price or ce.ltp, pe.best_bid_price or pe.ltp
    L = ctx.lot_size
    qty = size_quantity_margin_only(ctx.margin_rupees, (prem_c + prem_p) * L * 3, L)
    if qty < L:
        return skip(sid, name, "Insufficient margin for one lot.")
    legs = [
        TradeLeg("Call", "Sell", stp, qty, prem_c),
        TradeLeg("Put", "Sell", stp, qty, prem_p),
    ]
    max_profit = (prem_c + prem_p) * qty
    return ok_with_pop(
        ctx, sid, name, legs,
        max_loss=None,
        rr=f"Unlimited : {max_profit:.0f}",
        modified=ctx.structure_modified,
        net_premium_val=max_profit,
    )


def calc_short_strangle(ctx: EngineContext) -> StrategyResult:
    sid, name = "short_strangle", "Short Strangle"
    if ctx.halted:
        return skip(sid, name, ctx.halt_reason or "Market halted")
    anchors = anchors_for(ctx)
    L = ctx.lot_size
    best: tuple[float, list[TradeLeg], float] | None = None

    for ce_step, pe_step in STRANGLE_OTM_PAIRS:
        stp_c = anchors.otm_ce.get(ce_step)
        stp_p = anchors.otm_pe.get(pe_step)
        if stp_c is None or stp_p is None:
            continue
        ce = ctx.cache.get((stp_c, "Call"))
        pe = ctx.cache.get((stp_p, "Put"))
        if not ce or not pe or not ce.liquid or not pe.liquid:
            continue
        prem_c, prem_p = ce.best_bid_price or ce.ltp, pe.best_bid_price or pe.ltp
        qty = size_quantity_margin_only(ctx.margin_rupees, (prem_c + prem_p) * L * 3, L)
        if qty < L:
            continue
        legs = [
            TradeLeg("Call", "Sell", stp_c, qty, prem_c),
            TradeLeg("Put", "Sell", stp_p, qty, prem_p),
        ]
        pop = pop_for_legs(ctx, legs)
        if pop < ctx.min_pop_pct:
            continue
        max_profit = (prem_c + prem_p) * qty
        score = score_credit_trade(pop, max_profit, float("inf"))
        if best is None or score > best[0]:
            best = (score, legs, max_profit)

    if not best:
        stp_c = ensure_liquid_above(ctx, ctx.range_upper, "Call")
        stp_p = ensure_liquid_below(ctx, ctx.range_lower, "Put")
        if stp_c is None or stp_p is None:
            return skip(sid, name, "Could not resolve liquid strangle strikes.")
        ce, pe = ctx.cache[(stp_c, "Call")], ctx.cache[(stp_p, "Put")]
        prem_c, prem_p = ce.best_bid_price or ce.ltp, pe.best_bid_price or pe.ltp
        qty = size_quantity_margin_only(ctx.margin_rupees, (prem_c + prem_p) * L * 3, L)
        if qty < L:
            return skip(sid, name, "Insufficient margin for one lot.")
        legs = [
            TradeLeg("Call", "Sell", stp_c, qty, prem_c),
            TradeLeg("Put", "Sell", stp_p, qty, prem_p),
        ]
        max_profit = (prem_c + prem_p) * qty
        return ok_with_pop(
            ctx, sid, name, legs,
            max_loss=None,
            rr=f"Unlimited : {max_profit:.0f}",
            modified=ctx.structure_modified,
            net_premium_val=max_profit,
        )

    _, legs, max_profit = best
    return ok_with_pop(
        ctx, sid, name, legs,
        max_loss=None,
        rr=f"Unlimited : {max_profit:.0f}",
        modified=ctx.structure_modified,
        net_premium_val=max_profit,
    )


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
        qty = size_quantity_loss_only(ctx.max_loss_rupees, max_loss_u * L, L)
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
        stp_sp = ensure_liquid_below(ctx, ctx.range_lower, "Put", purpose="iron_condor: short PE")
        stp_sc = ensure_liquid_above(ctx, ctx.range_upper, "Call", purpose="iron_condor: short CE")
        if stp_sp is None or stp_sc is None:
            return skip(sid, name, "Could not resolve iron condor short strikes.")
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


def calc_iron_butterfly(ctx: EngineContext) -> StrategyResult:
    sid, name = "iron_butterfly", "Iron Butterfly"
    if ctx.halted:
        return skip(sid, name, ctx.halt_reason or "Market halted")
    stp = atm_with_liquidity(ctx)
    if stp is None:
        return skip(sid, name, "No liquid ATM for iron butterfly.")
    wings = iron_wings_symmetric(ctx, stp, stp, strategy_id=sid)
    if not wings:
        return skip(sid, name, "No symmetric wings meet minimum PoP within risk limits.")
    lp, lc, credit, max_loss_u, qty, pop = wings
    ce, pe, lpq, lcq = ctx.cache[(stp, "Call")], ctx.cache[(stp, "Put")], ctx.cache[(lp, "Put")], ctx.cache[(lc, "Call")]
    legs = [
        TradeLeg("Put", "Sell", stp, qty, pe.best_bid_price or pe.ltp),
        TradeLeg("Put", "Buy", lp, qty, lpq.best_offer_price or lpq.ltp),
        TradeLeg("Call", "Sell", stp, qty, ce.best_bid_price or ce.ltp),
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
