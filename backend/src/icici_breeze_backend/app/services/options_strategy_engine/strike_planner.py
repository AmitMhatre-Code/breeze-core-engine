"""Plan minimal strike/right pairs to fetch after bulk chain ingest."""
from __future__ import annotations

from icici_breeze_backend.app.services.options_strategy_engine.registry import prefetch_for_category
from icici_breeze_backend.app.services.options_strategy_engine.types import EngineContext, Right


def plan_required_strike_pairs(ctx: EngineContext) -> set[tuple[int, Right]]:
    """Union of (strike, right) pairs declared by each strategy in the active category."""
    pairs: set[tuple[int, Right]] = set()
    for prefetch in prefetch_for_category(ctx.strategy_category):
        pairs |= prefetch(ctx)
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
            rationale="Union per-strategy prefetch hooks for the active category.",
        )
    return to_fetch
