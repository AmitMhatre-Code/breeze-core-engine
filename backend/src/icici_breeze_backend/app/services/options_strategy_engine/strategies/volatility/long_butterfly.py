"""Long butterfly strategy calculator."""
from __future__ import annotations

from icici_breeze_backend.app.services.options_strategy_engine.helpers import skip
from icici_breeze_backend.app.services.options_strategy_engine.pop import pop_for_legs
WING_WIDTH_MULTIPLIERS: tuple[int, ...] = (1, 2, 3, 4)
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


def calc_long_butterfly(ctx: EngineContext) -> StrategyResult:
    sid, name = "long_butterfly", "Long Butterfly"
    if ctx.halted:
        return skip(sid, name, ctx.halt_reason or "Market halted")
    collector = audit_collector_for(ctx)
    if collector is not None:
        collector.min_pop_pct = ctx.min_pop_pct
    mid = (ctx.range_lower + ctx.range_upper) / 2
    centers = [s for s in windowed_liquid(ctx, sid, "Call") if ctx.range_lower <= s <= ctx.range_upper][:5]
    if not centers:
        record_simple_attempt(collector, reject_reason="illiquid")
        return skip(sid, name, "No liquid center strike for butterfly.")
    L = ctx.lot_size
    best: tuple[float, list[TradeLeg], float, float, float] | None = None
    for stp_m in sorted(centers, key=lambda s: abs(s - mid)):
        for mult in WING_WIDTH_MULTIPLIERS:
            stp_l = stp_m - mult * ctx.strike_step
            stp_h = stp_m + mult * ctx.strike_step
            if stp_l not in ctx.liquid_ce_strikes or stp_h not in ctx.liquid_ce_strikes:
                record_simple_attempt(
                    collector,
                    reject_reason="illiquid_wing",
                    center_strike=stp_m,
                    wing_width=mult * ctx.strike_step,
                )
                continue
            ql, qm, qh = ctx.cache[(stp_l, "Call")], ctx.cache[(stp_m, "Call")], ctx.cache[(stp_h, "Call")]
            net_per = (ql.best_offer_price or ql.ltp) + (qh.best_offer_price or qh.ltp) - 2 * (qm.best_bid_price or qm.ltp)
            left_w = stp_m - stp_l
            right_w = stp_h - stp_m
            extra_risk = max(0, right_w - left_w)
            max_loss_lot = net_per * L + extra_risk * L
            if max_loss_lot <= 0:
                record_simple_attempt(
                    collector,
                    reject_reason="no_credit",
                    center_strike=stp_m,
                    wing_width=mult * ctx.strike_step,
                )
                continue
            qty_m = size_quantity_loss_only(ctx.margin_rupees, net_per * L, L)
            qty_l = size_quantity_loss_only(ctx.effective_loss_sizing_budget(), max_loss_lot, L)
            qty = min(qty_m, qty_l) if qty_m and qty_l else 0
            if qty < L:
                record_simple_attempt(
                    collector,
                    reject_reason="quantity",
                    center_strike=stp_m,
                    wing_width=mult * ctx.strike_step,
                )
                continue
            short_qty = 2 * (qty // L) * L
            legs = [
                TradeLeg("Call", "Buy", stp_l, qty, ql.best_offer_price or ql.ltp),
                TradeLeg("Call", "Sell", stp_m, short_qty, qm.best_bid_price or qm.ltp),
                TradeLeg("Call", "Buy", stp_h, qty, qh.best_offer_price or qh.ltp),
            ]
            max_loss = net_per * qty + extra_risk * (qty // L) * L
            max_profit = (left_w - net_per) * qty
            if ctx.max_loss_rupees is not None and max_loss > ctx.max_loss_rupees:
                record_simple_attempt(
                    collector,
                    reject_reason="budget",
                    center_strike=stp_m,
                    wing_width=mult * ctx.strike_step,
                    max_loss=max_loss,
                )
                continue
            pop = pop_for_legs(ctx, legs)
            ev = score_debit_trade(pop, max_profit, max_loss)
            record_simple_attempt(
                collector,
                pop_pct=pop,
                center_strike=stp_m,
                wing_width=mult * ctx.strike_step,
                max_loss=max_loss,
            )
            if best is None or ev > best[0]:
                best = (ev, legs, max_loss, max_profit, pop)
    if not best:
        return skip(sid, name, "No long butterfly meets risk limits within the outlook range.")
    ev, legs, max_loss, max_profit, pop = best
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
        net_premium_val=-(max_loss if max_loss > 0 else 0),
    )


def prefetch_long_butterfly(ctx: EngineContext) -> set[tuple[int, Right]]:
    pairs: set[tuple[int, Right]] = set()
    for strike in windowed_liquid(ctx, "long_butterfly", "Call"):
        if ctx.range_lower <= strike <= ctx.range_upper:
            pairs.add((strike, "Call"))
            for mult in WING_WIDTH_MULTIPLIERS:
                pairs.add((strike - mult * ctx.strike_step, "Call"))
                pairs.add((strike + mult * ctx.strike_step, "Call"))
    return pairs
