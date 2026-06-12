"""Long strangle strategy calculator."""
from __future__ import annotations

from icici_breeze_backend.app.services.options_strategy_engine.anchors import STRANGLE_OTM_PAIRS
from icici_breeze_backend.app.services.options_strategy_engine.helpers import skip
from icici_breeze_backend.app.services.options_strategy_engine.pop import pop_for_legs
from icici_breeze_backend.app.services.options_strategy_engine.ranking import score_debit_trade
from icici_breeze_backend.app.services.options_strategy_engine.sizing import size_quantity_loss_only
from icici_breeze_backend.app.services.options_strategy_engine.strategies.common import anchors_for, make_result
from icici_breeze_backend.app.services.options_strategy_engine.types import (
    EngineContext,
    Right,
    StrategyResult,
    TradeLeg,
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


def prefetch_long_strangle(ctx: EngineContext) -> set[tuple[int, Right]]:
    anchors = anchors_for(ctx)
    pairs: set[tuple[int, Right]] = set()
    for ce_step, pe_step in STRANGLE_OTM_PAIRS:
        stp_c = anchors.otm_ce.get(ce_step)
        stp_p = anchors.otm_pe.get(pe_step)
        if stp_c is not None:
            pairs.add((stp_c, "Call"))
        if stp_p is not None:
            pairs.add((stp_p, "Put"))
    return pairs
