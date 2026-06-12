"""Plan minimal strike/right pairs to fetch after bulk chain ingest."""
from __future__ import annotations

from icici_breeze_backend.app.services.options_strategy_engine.anchors import (
    build_anchor_index,
    max_steps_for_strategy,
    strikes_in_window,
)
from icici_breeze_backend.app.services.options_strategy_engine.delta_anchor import (
    pop_to_short_delta,
    profile_deltas,
)
from icici_breeze_backend.app.services.options_strategy_engine.greeks import (
    snap_strike,
    strike_for_abs_delta,
)
from icici_breeze_backend.app.services.options_strategy_engine.helpers import sigma_for_pop
from icici_breeze_backend.app.services.options_strategy_engine.pruning import wing_strikes_from_multipliers
from icici_breeze_backend.app.services.options_strategy_engine.types import (
    WING_WIDTH_MULTIPLIERS,
    EngineContext,
    Right,
    StrategyCategory,
)

def _sigma(ctx: EngineContext) -> float:
    if ctx.atm_iv and ctx.atm_iv > 0:
        return ctx.atm_iv
    return sigma_for_pop(ctx)


def _estimate_strike(ctx: EngineContext, right: Right, target_abs_delta: float) -> int | None:
    k = strike_for_abs_delta(ctx.spot, ctx.t_years, _sigma(ctx), right, target_abs_delta)
    if right == "Call":
        prefer = "ceil" if k >= ctx.spot else "nearest"
    else:
        prefer = "floor" if k <= ctx.spot else "nearest"
    return snap_strike(ctx.strikes, k, prefer=prefer)


def _add_pair(pairs: set[tuple[int, Right]], strike: int | None, right: Right) -> None:
    if strike is not None:
        pairs.add((strike, right))


def _add_wings(
    pairs: set[tuple[int, Right]],
    short_strike: int | None,
    right: Right,
    ctx: EngineContext,
    *,
    wing_is_higher: bool,
) -> None:
    if short_strike is None:
        return
    liquid = set(ctx.strikes)
    for wing in wing_strikes_from_multipliers(
        short_strike, ctx.strike_step, liquid, wing_is_higher=wing_is_higher
    ):
        _add_pair(pairs, wing, right)


def _add_atm_pairs(pairs: set[tuple[int, Right]], ctx: EngineContext) -> None:
    _add_pair(pairs, ctx.atm_strike, "Call")
    _add_pair(pairs, ctx.atm_strike, "Put")


def _add_income_strikes(pairs: set[tuple[int, Right]], ctx: EngineContext) -> None:
    d1 = pop_to_short_delta(ctx.min_pop_pct, 1)
    d2 = pop_to_short_delta(ctx.min_pop_pct, 2)

    _add_atm_pairs(pairs, ctx)

    short_put = _estimate_strike(ctx, "Put", d2)
    short_call = _estimate_strike(ctx, "Call", d2)
    _add_pair(pairs, short_put, "Put")
    _add_pair(pairs, short_call, "Call")
    _add_wings(pairs, short_put, "Put", ctx, wing_is_higher=False)
    _add_wings(pairs, short_call, "Call", ctx, wing_is_higher=True)

    if short_put is not None and short_call is not None:
        for mult in WING_WIDTH_MULTIPLIERS:
            spread = mult * ctx.strike_step
            _add_pair(pairs, short_put - spread, "Put")
            lc = short_call + spread
            _add_pair(pairs, lc, "Call")

    naked_put = _estimate_strike(ctx, "Put", d1)
    naked_call = _estimate_strike(ctx, "Call", d1)
    _add_pair(pairs, naked_put, "Put")
    _add_pair(pairs, naked_call, "Call")

    anchors = build_anchor_index(ctx.strikes, ctx.spot, ctx.strike_step)
    for sid in ("bull_put_spread", "bear_call_spread"):
        max_steps = max_steps_for_strategy(sid)
        for right in ("Call", "Put"):
            for s in strikes_in_window(ctx.strikes, anchors, right, max_steps):
                _add_pair(pairs, s, right)


def _add_directional_strikes(
    pairs: set[tuple[int, Right]], ctx: EngineContext, category: StrategyCategory
) -> None:
    long_target, short_target = profile_deltas(ctx.risk_reward_profile)
    anchors = build_anchor_index(ctx.strikes, ctx.spot, ctx.strike_step)

    if category == "bullish":
        _add_pair(pairs, _estimate_strike(ctx, "Call", long_target), "Call")
        _add_pair(pairs, _estimate_strike(ctx, "Call", short_target), "Call")
        for s in strikes_in_window(
            ctx.strikes, anchors, "Call", max_steps_for_strategy("bull_call_spread")
        ):
            _add_pair(pairs, s, "Call")
    else:
        _add_pair(pairs, _estimate_strike(ctx, "Put", long_target), "Put")
        _add_pair(pairs, _estimate_strike(ctx, "Put", short_target), "Put")
        for s in strikes_in_window(
            ctx.strikes, anchors, "Put", max_steps_for_strategy("bear_put_spread")
        ):
            _add_pair(pairs, s, "Put")


def plan_required_strike_pairs(ctx: EngineContext) -> set[tuple[int, Right]]:
    """Union of (strike, right) pairs needed by all calculators in the active category."""
    pairs: set[tuple[int, Right]] = set()
    if ctx.strategy_category == "income":
        _add_income_strikes(pairs, ctx)
    elif ctx.strategy_category == "bullish":
        _add_directional_strikes(pairs, ctx, "bullish")
    else:
        _add_directional_strikes(pairs, ctx, "bearish")
    return pairs


def pairs_missing_from_cache(
    ctx: EngineContext,
    required: set[tuple[int, Right]],
) -> set[tuple[int, Right]]:
    return {(strike, right) for strike, right in required if (strike, right) not in ctx.cache}


def plan_targeted_fetches(ctx: EngineContext) -> set[tuple[int, Right]]:
    """Return strike/right pairs that require individual API calls."""
    required = plan_required_strike_pairs(ctx)
    to_fetch = pairs_missing_from_cache(ctx, required)
    cache_hits = required - to_fetch

    if ctx.audit:
        ctx.audit.record(
            "strike_planner",
            "Planned targeted strike fetches",
            {
                "strategy_category": ctx.strategy_category,
                "required_count": len(required),
                "cache_hit_count": len(cache_hits),
                "fetch_count": len(to_fetch),
                "cache_hits": [{"strike": s, "right": r} for s, r in sorted(cache_hits)],
                "to_fetch": [{"strike": s, "right": r} for s, r in sorted(to_fetch)],
            },
            rationale="Fetch only outer/target strikes missing from bulk chain cache.",
        )
    return to_fetch
