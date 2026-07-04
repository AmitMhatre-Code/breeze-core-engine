"""Universal strategy helpers — no strategy-specific business logic."""
from __future__ import annotations

from icici_breeze_backend.app.services.options_strategy_engine.anchors import (
    AnchorIndex,
    build_anchor_index,
    max_steps_for_strategy,
    strikes_in_window,
)
from icici_breeze_backend.app.services.options_strategy_engine.audit_helpers import audit_decision
from icici_breeze_backend.app.services.options_strategy_engine.helpers import (
    margin_key,
    net_premium,
    requires_pop_gate,
    skip,
)
from icici_breeze_backend.app.services.options_strategy_engine.pop import pop_for_legs
from icici_breeze_backend.app.services.options_strategy_engine.types import (
    EngineContext,
    Right,
    StrategyResult,
    TradeLeg,
)


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
    del max_attempts
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
    for s in ctx.strikes:
        if s <= level:
            continue
        q = ctx.cache.get((s, right))
        if q and q.liquid:
            return s
    return None


def ensure_liquid_below(
    ctx: EngineContext,
    level: float,
    right: Right,
    max_attempts: int = 3,
    *,
    purpose: str | None = None,
) -> int | None:
    del max_attempts, purpose
    liquid = ctx.liquid_ce_strikes if right == "Call" else ctx.liquid_pe_strikes
    hit = first_liquid_below(liquid, level)
    if hit is not None:
        return hit
    for s in reversed(ctx.strikes):
        if s >= level:
            continue
        q = ctx.cache.get((s, right))
        if q and q.liquid:
            return s
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
    max_profit: float | None = None,
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
        max_profit=max_profit,
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
    variant_rank: int | None = None,
    engine_score: float | None = None,
    ranking_summary: str | None = None,
    score_breakdown: dict | None = None,
    conviction_profile: str | None = None,
    hero_metric: Any | None = None,
    secondary_metrics: list[Any] | None = None,
    badges: list[str] | None = None,
    max_profit: float | None = None,
) -> StrategyResult:
    return StrategyResult(
        sid,
        name,
        "ok",
        legs=legs,
        net_premium=net_premium_val if net_premium_val is not None else net_premium(legs),
        max_loss=max_loss,
        max_profit=max_profit,
        risk_reward_ratio=rr,
        pop_pct=round(pop, 2),
        structure_modified=ctx.structure_modified,
        margin_key=margin_key(legs, ctx.stock_code, ctx.expiry_display, ctx.exchange_code),
        variant_rank=variant_rank,
        engine_score=engine_score,
        ranking_summary=ranking_summary,
        score_breakdown=score_breakdown,
        conviction_profile=conviction_profile,
        hero_metric=hero_metric,
        secondary_metrics=secondary_metrics or [],
        badges=badges or [],
    )


def all_liquid(ctx: EngineContext, right: Right) -> list[int]:
    return ctx.liquid_ce_strikes if right == "Call" else ctx.liquid_pe_strikes


def windowed_liquid(
    ctx: EngineContext,
    strategy_id: str,
    right: Right,
) -> list[int]:
    anchors = anchors_for(ctx)
    liquid = ctx.liquid_ce_strikes if right == "Call" else ctx.liquid_pe_strikes
    return strikes_in_window(liquid, anchors, right, max_steps_for_strategy(strategy_id))


def estimate_strike_for_abs_delta(
    ctx: EngineContext,
    right: Right,
    target_abs_delta: float,
) -> int | None:
    """Snap chain strike nearest target absolute delta (for prefetch planning)."""
    from icici_breeze_backend.app.services.options_strategy_engine.greeks import (
        snap_strike,
        strike_for_abs_delta,
    )
    from icici_breeze_backend.app.services.options_strategy_engine.helpers import sigma_for_pop

    sigma = ctx.atm_iv if ctx.atm_iv and ctx.atm_iv > 0 else sigma_for_pop(ctx)
    k = strike_for_abs_delta(ctx.spot, ctx.t_years, sigma, right, target_abs_delta)
    if right == "Call":
        prefer = "ceil" if k >= ctx.spot else "nearest"
    else:
        prefer = "floor" if k <= ctx.spot else "nearest"
    return snap_strike(ctx.strikes, k, prefer=prefer)


def prefetch_atm_pairs(ctx: EngineContext) -> set[tuple[int, Right]]:
    return {(ctx.atm_strike, "Call"), (ctx.atm_strike, "Put")}
