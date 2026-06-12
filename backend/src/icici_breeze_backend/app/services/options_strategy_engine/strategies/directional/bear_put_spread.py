"""Bear put spread strategy calculator."""
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
from icici_breeze_backend.app.services.options_strategy_engine.anchors import (
    build_anchor_index,
    max_steps_for_strategy,
    strikes_in_window,
)
from icici_breeze_backend.app.services.options_strategy_engine.delta_anchor import profile_deltas
from icici_breeze_backend.app.services.options_strategy_engine.types import (
    EngineContext,
    Right,
    StrategyResult,
    TradeLeg,
)


def calc_bear_put_spread(ctx: EngineContext) -> StrategyResult:
    sid, name = "bear_put_spread", "Bear Put Spread"
    if ctx.halted:
        return skip(sid, name, ctx.halt_reason or "Market halted")
    long_target, short_target = long_short_targets(ctx)
    L = ctx.lot_size
    liquid_pe = all_liquid(ctx, "Put")
    long_strikes = strikes_ranked_by_delta(
        liquid_pe, ctx.cache, "Put", long_target, strike_filter=lambda s: s >= ctx.atm_strike
    )
    short_strikes = strikes_ranked_by_delta(
        liquid_pe, ctx.cache, "Put", short_target, strike_filter=lambda s: s < ctx.atm_strike
    )

    best: tuple[float, list[TradeLeg], float, float, float] | None = None
    for stp_h in long_strikes:
        qh = ctx.cache[(stp_h, "Put")]
        if not delta_match(qh.delta, long_target):
            continue
        buy_prem = qh.best_offer_price or qh.ltp
        for stp_l in short_strikes:
            if stp_h <= stp_l:
                continue
            ql = ctx.cache[(stp_l, "Put")]
            if not delta_match(ql.delta, short_target):
                continue
            sell_prem = ql.best_bid_price or ql.ltp
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
                TradeLeg("Put", "Buy", stp_h, qty, buy_prem),
                TradeLeg("Put", "Sell", stp_l, qty, sell_prem),
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
        return skip(sid, name, "No bear put spread meets delta profile and max-loss budget.")
    _, legs, max_loss, pop, max_profit = best
    return make_result(
        ctx, sid, name, legs,
        max_loss=max_loss,
        rr=f"{max_loss:.0f} : {max_profit:.0f}",
        pop=pop,
        net_premium_val=-max_loss,
    )


def prefetch_bear_put_spread(ctx: EngineContext) -> set[tuple[int, Right]]:
    from icici_breeze_backend.app.services.options_strategy_engine.strategies.common import (
        estimate_strike_for_abs_delta,
    )

    long_target, short_target = profile_deltas(ctx.risk_reward_profile)
    pairs: set[tuple[int, Right]] = set()
    for target in (long_target, short_target):
        strike = estimate_strike_for_abs_delta(ctx, "Put", target)
        if strike is not None:
            pairs.add((strike, "Put"))
    anchors = build_anchor_index(ctx.strikes, ctx.spot, ctx.strike_step)
    for strike in strikes_in_window(
        ctx.strikes, anchors, "Put", max_steps_for_strategy("bear_put_spread")
    ):
        pairs.add((strike, "Put"))
    return pairs
