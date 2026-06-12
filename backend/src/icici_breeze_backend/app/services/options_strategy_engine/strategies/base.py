"""Shared strategy construction helpers."""
from __future__ import annotations

from icici_breeze_backend.app.services.options_strategy_engine.anchors import (
    AnchorIndex,
    build_anchor_index,
    max_steps_for_strategy,
    strikes_in_window,
)
from icici_breeze_backend.app.services.options_strategy_engine.audit_helpers import audit_calc, audit_decision
from icici_breeze_backend.app.services.options_strategy_engine.helpers import (
    margin_key,
    meets_pop_floor,
    net_premium,
    requires_pop_gate,
    skip,
)
from icici_breeze_backend.app.services.options_strategy_engine.pop import pop_for_legs
from icici_breeze_backend.app.services.options_strategy_engine.pruning import (
    DELTA_INCOME_HEDGE,
    passes_economic_prune,
    top_m_wings,
)
from icici_breeze_backend.app.services.options_strategy_engine.ranking import score_credit_trade, score_debit_trade
from icici_breeze_backend.app.services.options_strategy_engine.sizing import (
    size_quantity_loss_only as size_by_loss,
)
from icici_breeze_backend.app.services.options_strategy_engine.types import (
    TOP_K_SHORT_STRIKES,
    TOP_M_WING_STRIKES,
    WING_WIDTH_MULTIPLIERS,
    EngineContext,
    Right,
    StrategyResult,
    TradeLeg,
)
from icici_breeze_backend.audit.strategy_builder_audit import quote_row_to_audit


def anchors_for(ctx: EngineContext) -> AnchorIndex:
    return build_anchor_index(ctx.strikes, ctx.spot, ctx.strike_step)


def first_liquid_above(strikes: list[int], level: float) -> int | None:
    for s in strikes:
        if s > level:
            return s
    return None


def first_liquid_below(strikes: list[int], level: float) -> int | None:
    for s in reversed(strikes):
        if s < level:
            return s
    return None


def nearest_liquid_ge(strikes: list[int], level: float) -> int | None:
    for s in strikes:
        if s >= level:
            return s
    return None


def nearest_liquid_le(strikes: list[int], level: float) -> int | None:
    for s in reversed(strikes):
        if s <= level:
            return s
    return None


def ensure_liquid_above(
    ctx: EngineContext,
    level: float,
    right: Right,
    max_attempts: int = 3,
    *,
    purpose: str | None = None,
) -> int | None:
    liquid = ctx.liquid_ce_strikes if right == "Call" else ctx.liquid_pe_strikes
    hit = first_liquid_above(liquid, level)
    if hit is not None:
        audit_decision(
            ctx,
            f"Select liquid {right} above {level}",
            f"strike {hit}",
            f"First liquid {right} strictly above {level} from cached liquid set.",
            {"level": level, "liquid_pool": liquid, "purpose": purpose},
        )
        return hit
    candidates = [s for s in ctx.strikes if s > level]
    attempts = 0
    for s in candidates:
        q = ctx.cache.get((s, right))
        if q and q.liquid:
            return s
        from icici_breeze_backend.app.services.options_strategy_engine.universe import fetch_pairs_for_strikes

        fetch_pairs_for_strikes(
            ctx,
            {s},
            fetch_reason=purpose or f"On-demand quote for {right} {s} (first liquid above {level})",
        )
        attempts += 1
        q = ctx.cache.get((s, right))
        if q and q.liquid:
            return s
        if ctx.audit:
            ctx.audit.record_strike(
                s,
                right,
                included=False,
                reason="Still illiquid after on-demand fetch",
                quote=quote_row_to_audit(q) if q else None,
                context=purpose,
            )
        if attempts >= max_attempts:
            break
    return None


def ensure_liquid_below(
    ctx: EngineContext,
    level: float,
    right: Right,
    max_attempts: int = 3,
    *,
    purpose: str | None = None,
) -> int | None:
    liquid = ctx.liquid_ce_strikes if right == "Call" else ctx.liquid_pe_strikes
    hit = first_liquid_below(liquid, level)
    if hit is not None:
        return hit
    candidates = [s for s in reversed(ctx.strikes) if s < level]
    attempts = 0
    for s in candidates:
        q = ctx.cache.get((s, right))
        if q and q.liquid:
            return s
        from icici_breeze_backend.app.services.options_strategy_engine.universe import fetch_pairs_for_strikes

        fetch_pairs_for_strikes(
            ctx,
            {s},
            fetch_reason=purpose or f"On-demand quote for {right} {s} (first liquid below {level})",
        )
        attempts += 1
        q = ctx.cache.get((s, right))
        if q and q.liquid:
            return s
        if attempts >= max_attempts:
            break
    return None


def atm_with_liquidity(ctx: EngineContext) -> int | None:
    for s in sorted(ctx.strikes, key=lambda x: abs(x - ctx.atm_strike)):
        ce = ctx.cache.get((s, "Call"))
        pe = ctx.cache.get((s, "Put"))
        if ce and pe and ce.liquid and pe.liquid:
            return s
    return None


def ok_with_pop(
    ctx: EngineContext,
    strategy_id: str,
    name: str,
    legs: list[TradeLeg],
    *,
    max_loss: float | None,
    rr: str,
    modified: bool = False,
    net_premium_val: float | None = None,
    require_pop: bool | None = None,
) -> StrategyResult:
    pop = pop_for_legs(ctx, legs)
    gate = require_pop if require_pop is not None else requires_pop_gate(ctx)
    if gate and pop < ctx.min_pop_pct:
        return skip(
            strategy_id,
            name,
            f"PoP {pop:.1f}% below minimum {ctx.min_pop_pct:.1f}%.",
            modified,
        )
    prem = net_premium_val if net_premium_val is not None else net_premium(legs)
    return StrategyResult(
        strategy_id=strategy_id,
        strategy_name=name,
        status="ok",
        legs=legs,
        net_premium=prem,
        max_loss=max_loss,
        risk_reward_ratio=rr,
        pop_pct=round(pop, 2),
        structure_modified=modified,
        margin_key=margin_key(legs, ctx.stock_code, ctx.expiry_display, ctx.exchange_code),
    )


def make_result(
    ctx: EngineContext,
    sid: str,
    name: str,
    legs: list[TradeLeg],
    *,
    max_loss: float | None,
    rr: str,
    pop: float,
    net_premium_val: float | None = None,
) -> StrategyResult:
    return StrategyResult(
        sid,
        name,
        "ok",
        legs=legs,
        net_premium=net_premium_val if net_premium_val is not None else net_premium(legs),
        max_loss=max_loss,
        risk_reward_ratio=rr,
        pop_pct=round(pop, 2),
        structure_modified=ctx.structure_modified,
        margin_key=margin_key(legs, ctx.stock_code, ctx.expiry_display, ctx.exchange_code),
    )


def credit_spread_wing(
    ctx: EngineContext,
    short_stp: int,
    short_right: Right,
    wing_strikes: list[int],
    wing_is_higher: bool,
    *,
    strategy_id: str | None = None,
) -> tuple[int, float, float, int, float] | None:
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
        delta_window=DELTA_INCOME_HEDGE,
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
            max_loss_budget=ctx.max_loss_rupees,
            require_pop=requires_pop_gate(ctx),
            min_pop_pct=ctx.min_pop_pct,
        ):
            continue
        qty = size_by_loss(ctx.max_loss_rupees, max_loss_u * L, L)
        if qty < L:
            continue
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


def iron_wings_symmetric(
    ctx: EngineContext,
    short_put: int,
    short_call: int,
    *,
    strategy_id: str | None = None,
) -> tuple[int, int, float, float, int, float] | None:
    L = ctx.lot_size
    liquid_pe = set(ctx.liquid_pe_strikes)
    liquid_ce = set(ctx.liquid_ce_strikes)
    best: tuple[float, int, int, float, float, int, float] | None = None

    for mult in WING_WIDTH_MULTIPLIERS:
        w = mult * ctx.strike_step
        lp = short_put - w
        lc = short_call + w
        if lp not in liquid_pe or lc not in liquid_ce:
            continue
        sp = ctx.cache[(short_put, "Put")]
        sc = ctx.cache[(short_call, "Call")]
        lpq = ctx.cache[(lp, "Put")]
        lcq = ctx.cache[(lc, "Call")]
        sp_prem = sp.best_bid_price or sp.ltp
        sc_prem = sc.best_bid_price or sc.ltp
        lp_prem = lpq.best_offer_price or lpq.ltp
        lc_prem = lcq.best_offer_price or lcq.ltp
        credit = sp_prem + sc_prem - lp_prem - lc_prem
        max_loss_u = w - credit
        if not passes_economic_prune(
            net_credit=credit,
            max_loss_per_unit=max_loss_u,
            max_loss_total=max_loss_u * L,
            max_loss_budget=ctx.max_loss_rupees,
            require_pop=requires_pop_gate(ctx),
            min_pop_pct=ctx.min_pop_pct,
        ):
            continue
        qty = size_by_loss(ctx.max_loss_rupees, max_loss_u * L, L)
        if qty < L:
            continue
        legs = [
            TradeLeg("Put", "Sell", short_put, qty, sp_prem),
            TradeLeg("Put", "Buy", lp, qty, lp_prem),
            TradeLeg("Call", "Sell", short_call, qty, sc_prem),
            TradeLeg("Call", "Buy", lc, qty, lc_prem),
        ]
        pop = pop_for_legs(ctx, legs)
        if not meets_pop_floor(ctx, pop):
            continue
        net_collected = credit * qty
        score = score_credit_trade(pop, net_collected, max_loss_u * qty)
        if best is None or score > best[0]:
            best = (score, lp, lc, credit, max_loss_u, qty, pop)

    if not best:
        return None
    _, lp, lc, credit, max_loss_u, qty, pop = best
    return lp, lc, credit, max_loss_u, qty, pop


def windowed_liquid(
    ctx: EngineContext,
    strategy_id: str,
    right: Right,
) -> list[int]:
    anchors = anchors_for(ctx)
    liquid = ctx.liquid_ce_strikes if right == "Call" else ctx.liquid_pe_strikes
    return strikes_in_window(liquid, anchors, right, max_steps_for_strategy(strategy_id))
