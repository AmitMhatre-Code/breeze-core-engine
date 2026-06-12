"""Volatility strategy calculators (Gemini §4.3)."""
from __future__ import annotations

from icici_breeze_backend.app.services.options_strategy_engine.anchors import STRANGLE_OTM_PAIRS, anchors_for
from icici_breeze_backend.app.services.options_strategy_engine.helpers import skip
from icici_breeze_backend.app.services.options_strategy_engine.pop import pop_for_legs
from icici_breeze_backend.app.services.options_strategy_engine.pruning import WING_WIDTH_MULTIPLIERS
from icici_breeze_backend.app.services.options_strategy_engine.ranking import score_debit_trade
from icici_breeze_backend.app.services.options_strategy_engine.sizing import size_quantity_loss_only
from icici_breeze_backend.app.services.options_strategy_engine.strategies.base import (
    ensure_liquid_above,
    ensure_liquid_below,
    make_result,
    windowed_liquid,
)
from icici_breeze_backend.app.services.options_strategy_engine.types import EngineContext, StrategyResult, TradeLeg


def calc_long_straddle(ctx: EngineContext) -> StrategyResult:
    sid, name = "long_straddle", "Long Straddle"
    if ctx.halted:
        return skip(sid, name, ctx.halt_reason or "Market halted")
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
        qty = size_quantity_loss_only(min(ctx.margin_rupees, ctx.max_loss_rupees), debit_lot, L)
        if qty < L:
            continue
        legs = [
            TradeLeg("Call", "Buy", stp, qty, ce.best_offer_price or ce.ltp),
            TradeLeg("Put", "Buy", stp, qty, pe.best_offer_price or pe.ltp),
        ]
        max_loss = debit_lot * (qty // L)
        if max_loss > ctx.max_loss_rupees:
            continue
        pop = pop_for_legs(ctx, legs)
        ev = score_debit_trade(pop, float("inf"), max_loss)
        if best is None or ev > best[0]:
            best = (ev, legs, max_loss, pop)
    if not best:
        return skip(sid, name, "No long straddle meets risk limits within the outlook range.")
    _, legs, max_loss, pop = best
    return make_result(
        ctx, sid, name, legs,
        max_loss=max_loss,
        rr=f"{max_loss:.0f} : Unlimited",
        pop=pop,
        net_premium_val=-max_loss,
    )


def calc_long_strangle(ctx: EngineContext) -> StrategyResult:
    sid, name = "long_strangle", "Long Strangle"
    if ctx.halted:
        return skip(sid, name, ctx.halt_reason or "Market halted")
    anchors = anchors_for(ctx)
    L = ctx.lot_size
    best: tuple[float, list[TradeLeg], float, float] | None = None

    for ce_step, pe_step in STRANGLE_OTM_PAIRS:
        stp_c = anchors.otm_ce.get(ce_step)
        stp_p = anchors.otm_pe.get(pe_step)
        if stp_c is None or stp_p is None:
            continue
        if stp_c < ctx.range_upper or stp_p > ctx.range_lower:
            continue
        ce, pe = ctx.cache.get((stp_c, "Call")), ctx.cache.get((stp_p, "Put"))
        if not ce or not pe or not ce.liquid or not pe.liquid:
            continue
        debit_lot = ((ce.best_offer_price or ce.ltp) + (pe.best_offer_price or pe.ltp)) * L
        qty = size_quantity_loss_only(min(ctx.margin_rupees, ctx.max_loss_rupees), debit_lot, L)
        if qty < L:
            continue
        legs = [
            TradeLeg("Call", "Buy", stp_c, qty, ce.best_offer_price or ce.ltp),
            TradeLeg("Put", "Buy", stp_p, qty, pe.best_offer_price or pe.ltp),
        ]
        max_loss = debit_lot * (qty // L)
        if max_loss > ctx.max_loss_rupees:
            continue
        pop = pop_for_legs(ctx, legs)
        ev = score_debit_trade(pop, float("inf"), max_loss)
        if best is None or ev > best[0]:
            best = (ev, legs, max_loss, pop)

    if not best:
        return skip(sid, name, "No long strangle meets risk limits within the outlook range.")
    _, legs, max_loss, pop = best
    return make_result(
        ctx, sid, name, legs,
        max_loss=max_loss,
        rr=f"{max_loss:.0f} : Unlimited",
        pop=pop,
        net_premium_val=-max_loss,
    )


def calc_long_butterfly(ctx: EngineContext) -> StrategyResult:
    sid, name = "long_butterfly", "Long Butterfly"
    if ctx.halted:
        return skip(sid, name, ctx.halt_reason or "Market halted")
    mid = (ctx.range_lower + ctx.range_upper) / 2
    centers = [s for s in windowed_liquid(ctx, sid, "Call") if ctx.range_lower <= s <= ctx.range_upper][:5]
    if not centers:
        return skip(sid, name, "No liquid center strike for butterfly.")
    L = ctx.lot_size
    best: tuple[float, list[TradeLeg], float, float, float] | None = None
    for stp_m in sorted(centers, key=lambda s: abs(s - mid)):
        for mult in WING_WIDTH_MULTIPLIERS:
            stp_l = stp_m - mult * ctx.strike_step
            stp_h = stp_m + mult * ctx.strike_step
            if stp_l not in ctx.liquid_ce_strikes or stp_h not in ctx.liquid_ce_strikes:
                continue
            ql, qm, qh = ctx.cache[(stp_l, "Call")], ctx.cache[(stp_m, "Call")], ctx.cache[(stp_h, "Call")]
            net_per = (ql.best_offer_price or ql.ltp) + (qh.best_offer_price or qh.ltp) - 2 * (qm.best_bid_price or qm.ltp)
            left_w = stp_m - stp_l
            right_w = stp_h - stp_m
            extra_risk = max(0, right_w - left_w)
            max_loss_lot = net_per * L + extra_risk * L
            if max_loss_lot <= 0:
                continue
            qty_m = size_quantity_loss_only(ctx.margin_rupees, net_per * L, L)
            qty_l = size_quantity_loss_only(ctx.max_loss_rupees, max_loss_lot, L)
            qty = min(qty_m, qty_l) if qty_m and qty_l else 0
            if qty < L:
                continue
            short_qty = 2 * (qty // L) * L
            legs = [
                TradeLeg("Call", "Buy", stp_l, qty, ql.best_offer_price or ql.ltp),
                TradeLeg("Call", "Sell", stp_m, short_qty, qm.best_bid_price or qm.ltp),
                TradeLeg("Call", "Buy", stp_h, qty, qh.best_offer_price or qh.ltp),
            ]
            max_loss = net_per * qty + extra_risk * (qty // L) * L
            max_profit = (left_w - net_per) * qty
            if max_loss > ctx.max_loss_rupees:
                continue
            pop = pop_for_legs(ctx, legs)
            ev = score_debit_trade(pop, max_profit, max_loss)
            if best is None or ev > best[0]:
                best = (ev, legs, max_loss, max_profit, pop)
    if not best:
        return skip(sid, name, "No long butterfly meets risk limits within the outlook range.")
    _, legs, max_loss, max_profit, pop = best
    return make_result(
        ctx, sid, name, legs,
        max_loss=max_loss,
        rr=f"{max_loss:.0f} : {max_profit:.0f}",
        pop=pop,
        net_premium_val=-(max_loss if max_loss > 0 else 0),
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
