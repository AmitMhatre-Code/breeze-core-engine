"""Bull call spread strategy calculator."""
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


def calc_bull_call_spread(ctx: EngineContext) -> StrategyResult:
    sid, name = "bull_call_spread", "Bull Call Spread"
    if ctx.halted:
        return skip(sid, name, ctx.halt_reason or "Market halted")
    long_target, short_target = long_short_targets(ctx)
    L = ctx.lot_size
    liquid_ce = all_liquid(ctx, "Call")
    long_strikes = strikes_ranked_by_delta(
        liquid_ce, ctx.cache, "Call", long_target, strike_filter=lambda s: s <= ctx.atm_strike
    )
    short_strikes = strikes_ranked_by_delta(
        liquid_ce, ctx.cache, "Call", short_target, strike_filter=lambda s: s > ctx.atm_strike
    )

    best: tuple[float, list[TradeLeg], float, float, float] | None = None
    for stp_l in long_strikes:
        ql = ctx.cache[(stp_l, "Call")]
        if not delta_match(ql.delta, long_target):
            continue
        buy_prem = ql.best_offer_price or ql.ltp
        for stp_h in short_strikes:
            if stp_l >= stp_h:
                continue
            qh = ctx.cache[(stp_h, "Call")]
            if not delta_match(qh.delta, short_target):
                continue
            sell_prem = qh.best_bid_price or qh.ltp
            net_per = buy_prem - sell_prem
            if net_per <= 0:
                continue
            max_loss_lot = net_per * L
            qty = size_quantity_from_budgets(
                sid,
                buy_prem * L,
                max_loss_lot,
                margin_rupees=ctx.margin_rupees,
                max_loss_rupees=ctx.max_loss_rupees,
                lot_size=L,
                unit_short_lots=1,
                spot=ctx.spot,
                provision_elm=ctx.provision_elm,
            )
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
            cer = score_directional_candidate(pop, max_profit, max_loss)
            if best is None or cer > best[0]:
                best = (cer, legs, max_loss, pop, max_profit)

    if not best:
        return skip(sid, name, "No bull call spread meets delta profile and max-loss budget.")
    _, legs, max_loss, pop, max_profit = best
    return make_result(
        ctx, sid, name, legs,
        max_loss=max_loss,
        rr=f"{max_loss:.0f} : {max_profit:.0f}",
        pop=pop,
        net_premium_val=-max_loss,
    )
