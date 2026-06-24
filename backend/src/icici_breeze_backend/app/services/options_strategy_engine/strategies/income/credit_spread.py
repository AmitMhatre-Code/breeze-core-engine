"""Credit spread wing selection — shared by bull put and bear call spread only."""
from __future__ import annotations

from icici_breeze_backend.app.services.options_strategy_engine.helpers import (
    meets_pop_floor,
    requires_pop_gate,
)
from icici_breeze_backend.app.services.options_strategy_engine.pop import pop_for_legs
from icici_breeze_backend.app.services.options_strategy_engine.pruning import (
    DeltaWindow,
    passes_economic_prune,
    top_m_wings,
    wing_strikes_from_multipliers,
)
from icici_breeze_backend.app.services.options_strategy_engine.ranking import score_credit_trade
from icici_breeze_backend.app.services.options_strategy_engine.sizing import min_qty_for_one_lot
from icici_breeze_backend.app.services.options_strategy_engine.types import (
    TOP_M_WING_STRIKES,
    EngineContext,
    Right,
    TradeLeg,
)

CREDIT_SPREAD_HEDGE_DELTA = DeltaWindow(0.02, 0.10)


def credit_spread_wing(
    ctx: EngineContext,
    short_stp: int,
    short_right: Right,
    wing_strikes: list[int],
    wing_is_higher: bool,
    *,
    strategy_id: str | None = None,
) -> tuple[int, float, float, int, float] | None:
    del strategy_id
    L = ctx.lot_size
    qs = ctx.cache.get((short_stp, short_right))
    if not qs:
        return None
    m_wings = top_m_wings(
        short_stp,
        wing_strikes,
        ctx.cache,
        short_right,
        TOP_M_WING_STRIKES,
        wing_is_higher=wing_is_higher,
        delta_window=CREDIT_SPREAD_HEDGE_DELTA,
    )
    short_prem = qs.best_bid_price or qs.ltp
    best: tuple[float, int, float, float, int, float] | None = None
    for wing in m_wings:
        qw = ctx.cache.get((wing, short_right))
        if not qw:
            continue
        wing_prem = qw.best_offer_price or qw.ltp
        credit = short_prem - wing_prem
        width = abs(wing - short_stp)
        max_loss_u = width - credit
        if not passes_economic_prune(
            net_credit=credit,
            max_loss_per_unit=max_loss_u,
            max_loss_total=max_loss_u * L,
            max_loss_budget=ctx.effective_max_loss_budget(),
            require_pop=requires_pop_gate(ctx),
            min_pop_pct=ctx.min_pop_pct,
        ):
            continue
        qty = min_qty_for_one_lot(L)
        legs = [
            TradeLeg(short_right, "Sell", short_stp, qty, short_prem),
            TradeLeg(short_right, "Buy", wing, qty, wing_prem),
        ]
        pop = pop_for_legs(ctx, legs)
        if not meets_pop_floor(ctx, pop):
            continue
        net_collected = credit * qty
        score = score_credit_trade(pop, net_collected, max_loss_u * qty)
        if best is None or score > best[0]:
            best = (score, wing, credit, max_loss_u, qty, pop)
    if not best:
        return None
    _, wing, credit, max_loss_u, qty, pop = best
    return wing, credit, max_loss_u, qty, pop


def credit_spread_wing_full(
    ctx: EngineContext,
    short_stp: int,
    short_right: Right,
    wing_strikes: list[int],
    wing_is_higher: bool,
) -> tuple[int, float, float, int, float] | None:
    """Enumerate all liquid wings (no top-M cap) and pick best credit score."""
    L = ctx.lot_size
    qs = ctx.cache.get((short_stp, short_right))
    if not qs:
        return None
    liquid_set = set(wing_strikes)
    wings = wing_strikes_from_multipliers(
        short_stp, ctx.strike_step, liquid_set, wing_is_higher=wing_is_higher
    )
    if not wings:
        wings = [
            s
            for s in wing_strikes
            if (s > short_stp if wing_is_higher else s < short_stp)
        ]
    short_prem = qs.best_bid_price or qs.ltp
    best: tuple[float, int, float, float, int, float] | None = None
    for wing in wings:
        qw = ctx.cache.get((wing, short_right))
        if not qw or not qw.liquid:
            continue
        wing_prem = qw.best_offer_price or qw.ltp
        credit = short_prem - wing_prem
        width = abs(wing - short_stp)
        max_loss_u = width - credit
        if not passes_economic_prune(
            net_credit=credit,
            max_loss_per_unit=max_loss_u,
            max_loss_total=max_loss_u * L,
            max_loss_budget=ctx.effective_max_loss_budget(),
            require_pop=requires_pop_gate(ctx),
            min_pop_pct=ctx.min_pop_pct,
        ):
            continue
        qty = min_qty_for_one_lot(L)
        legs = [
            TradeLeg(short_right, "Sell", short_stp, qty, short_prem),
            TradeLeg(short_right, "Buy", wing, qty, wing_prem),
        ]
        pop = pop_for_legs(ctx, legs)
        if not meets_pop_floor(ctx, pop):
            continue
        net_collected = credit * qty
        score = score_credit_trade(pop, net_collected, max_loss_u * qty)
        if best is None or score > best[0]:
            best = (score, wing, credit, max_loss_u, qty, pop)
    if not best:
        return None
    _, wing, credit, max_loss_u, qty, pop = best
    return wing, credit, max_loss_u, qty, pop
