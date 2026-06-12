"""Directional strategy calculators (Gemini §4.2)."""
from __future__ import annotations

from icici_breeze_backend.app.services.options_strategy_engine.helpers import skip
from icici_breeze_backend.app.services.options_strategy_engine.pop import expected_value_heuristic, pop_for_legs
from icici_breeze_backend.app.services.options_strategy_engine.pruning import DELTA_DIRECTIONAL_LONG, top_k_strikes
from icici_breeze_backend.app.services.options_strategy_engine.ranking import score_debit_trade
from icici_breeze_backend.app.services.options_strategy_engine.sizing import size_quantity_from_budgets, size_quantity_loss_only
from icici_breeze_backend.app.services.options_strategy_engine.strategies.base import (
    make_result,
    nearest_liquid_ge,
    nearest_liquid_le,
    windowed_liquid,
)
from icici_breeze_backend.app.services.options_strategy_engine.types import EngineContext, StrategyResult, TradeLeg


def calc_bull_call_spread(ctx: EngineContext) -> StrategyResult:
    sid, name = "bull_call_spread", "Bull Call Spread"
    if ctx.halted:
        return skip(sid, name, ctx.halt_reason or "Market halted")
    stp_h = nearest_liquid_ge(ctx.liquid_ce_strikes, ctx.range_upper)
    if stp_h is None:
        return skip(sid, name, "Could not resolve liquid short CE at/above range upper.")
    qh = ctx.cache[(stp_h, "Call")]
    sell_prem = qh.best_bid_price or qh.ltp
    buy_candidates = top_k_strikes(
        [s for s in windowed_liquid(ctx, sid, "Call") if ctx.spot <= s < stp_h],
        ctx.cache,
        "Call",
        8,
        credit=False,
        delta_window=DELTA_DIRECTIONAL_LONG,
    )
    L = ctx.lot_size
    best: tuple[float, int, list[TradeLeg], float, float, float] | None = None
    for stp_l in sorted(buy_candidates):
        ql = ctx.cache[(stp_l, "Call")]
        buy_prem = ql.best_offer_price or ql.ltp
        net_per = buy_prem - sell_prem
        if net_per <= 0:
            continue
        max_loss_lot = net_per * L
        qty_m = size_quantity_loss_only(ctx.margin_rupees, buy_prem * L, L)
        qty_l = size_quantity_loss_only(ctx.max_loss_rupees, max_loss_lot, L)
        qty = min(qty_m, qty_l) if qty_m and qty_l else 0
        if qty < L:
            continue
        legs = [
            TradeLeg("Call", "Buy", stp_l, qty, buy_prem),
            TradeLeg("Call", "Sell", stp_h, qty, sell_prem),
        ]
        max_loss = net_per * qty
        if max_loss > ctx.max_loss_rupees:
            continue
        pop = pop_for_legs(ctx, legs)
        max_profit = ((stp_h - stp_l) - net_per) * qty
        ev = score_debit_trade(pop, max_profit, max_loss)
        if best is None or ev > best[0]:
            best = (ev, stp_l, legs, max_loss, pop, max_profit)
    if not best:
        return skip(sid, name, "No bull call spread meets risk limits within the outlook range.")
    _, _, legs, max_loss, pop, max_profit = best
    return make_result(
        ctx, sid, name, legs,
        max_loss=max_loss,
        rr=f"{max_loss:.0f} : {max_profit:.0f}",
        pop=pop,
        net_premium_val=-max_loss,
    )


def calc_bear_put_spread(ctx: EngineContext) -> StrategyResult:
    sid, name = "bear_put_spread", "Bear Put Spread"
    if ctx.halted:
        return skip(sid, name, ctx.halt_reason or "Market halted")
    stp_l = nearest_liquid_le(ctx.liquid_pe_strikes, ctx.range_lower)
    if stp_l is None:
        return skip(sid, name, "Could not resolve liquid short PE at/below range lower.")
    ql = ctx.cache[(stp_l, "Put")]
    sell_prem = ql.best_bid_price or ql.ltp
    buy_candidates = top_k_strikes(
        [s for s in windowed_liquid(ctx, sid, "Put") if stp_l < s <= ctx.spot],
        ctx.cache,
        "Put",
        8,
        credit=False,
        delta_window=DELTA_DIRECTIONAL_LONG,
    )
    L = ctx.lot_size
    best: tuple[float, int, list[TradeLeg], float, float, float] | None = None
    for stp_h in sorted(buy_candidates, reverse=True):
        qh = ctx.cache[(stp_h, "Put")]
        buy_prem = qh.best_offer_price or qh.ltp
        net_per = buy_prem - sell_prem
        if net_per <= 0:
            continue
        max_loss_lot = net_per * L
        qty_m = size_quantity_loss_only(ctx.margin_rupees, buy_prem * L, L)
        qty_l = size_quantity_loss_only(ctx.max_loss_rupees, max_loss_lot, L)
        qty = min(qty_m, qty_l) if qty_m and qty_l else 0
        if qty < L:
            continue
        legs = [
            TradeLeg("Put", "Buy", stp_h, qty, buy_prem),
            TradeLeg("Put", "Sell", stp_l, qty, sell_prem),
        ]
        max_loss = net_per * qty
        if max_loss > ctx.max_loss_rupees:
            continue
        pop = pop_for_legs(ctx, legs)
        max_profit = ((stp_h - stp_l) - net_per) * qty
        ev = score_debit_trade(pop, max_profit, max_loss)
        if best is None or ev > best[0]:
            best = (ev, stp_h, legs, max_loss, pop, max_profit)
    if not best:
        return skip(sid, name, "No bear put spread meets risk limits within the outlook range.")
    _, _, legs, max_loss, pop, max_profit = best
    return make_result(
        ctx, sid, name, legs,
        max_loss=max_loss,
        rr=f"{max_loss:.0f} : {max_profit:.0f}",
        pop=pop,
        net_premium_val=-max_loss,
    )


def calc_long_call(ctx: EngineContext) -> StrategyResult:
    sid, name = "long_call", "Long Call"
    if ctx.halted:
        return skip(sid, name, ctx.halt_reason or "Market halted")
    L = ctx.lot_size
    candidates = top_k_strikes(
        [s for s in windowed_liquid(ctx, sid, "Call") if ctx.range_lower <= s <= ctx.range_upper],
        ctx.cache,
        "Call",
        8,
        credit=False,
        delta_window=DELTA_DIRECTIONAL_LONG,
    )
    best: tuple[float, list[TradeLeg], float, float] | None = None
    for stp in candidates:
        q = ctx.cache[(stp, "Call")]
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
        ev = expected_value_heuristic(pop, float("inf"), max_loss)
        if best is None or ev > best[0]:
            best = (ev, legs, max_loss, pop)
    if not best:
        return skip(sid, name, "No long call meets risk limits within the outlook range.")
    _, legs, max_loss, pop = best
    return make_result(
        ctx, sid, name, legs,
        max_loss=max_loss,
        rr=f"{max_loss:.0f} : Unlimited",
        pop=pop,
        net_premium_val=-max_loss,
    )


def calc_long_put(ctx: EngineContext) -> StrategyResult:
    sid, name = "long_put", "Long Put"
    if ctx.halted:
        return skip(sid, name, ctx.halt_reason or "Market halted")
    L = ctx.lot_size
    candidates = top_k_strikes(
        [s for s in windowed_liquid(ctx, sid, "Put") if ctx.range_lower <= s <= ctx.range_upper],
        ctx.cache,
        "Put",
        8,
        credit=False,
        delta_window=DELTA_DIRECTIONAL_LONG,
    )
    best: tuple[float, list[TradeLeg], float, float] | None = None
    for stp in candidates:
        q = ctx.cache[(stp, "Put")]
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
        legs = [TradeLeg("Put", "Buy", stp, qty, buy_prem)]
        max_loss = buy_prem * qty
        if max_loss > ctx.max_loss_rupees:
            continue
        pop = pop_for_legs(ctx, legs)
        ev = expected_value_heuristic(pop, float("inf"), max_loss)
        if best is None or ev > best[0]:
            best = (ev, legs, max_loss, pop)
    if not best:
        return skip(sid, name, "No long put meets risk limits within the outlook range.")
    _, legs, max_loss, pop = best
    return make_result(
        ctx, sid, name, legs,
        max_loss=max_loss,
        rr=f"{max_loss:.0f} : Unlimited",
        pop=pop,
        net_premium_val=-max_loss,
    )
