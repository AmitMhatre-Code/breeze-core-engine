"""Plan minimal strike/right pairs to fetch after bulk chain ingest."""
from __future__ import annotations

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.services.options_strategy_engine.registry import prefetch_for_category
from icici_breeze_backend.app.core.strike import Strike
from icici_breeze_backend.app.services.options_strategy_engine.types import EngineContext, Right
from icici_breeze_backend.app.services.quote_source_router import resolve_quote_source


def plan_required_strike_pairs(ctx: EngineContext) -> set[tuple[Strike, Right]]:
    """Union of (strike, right) pairs declared by each strategy in the active category."""
    pairs: set[tuple[Strike, Right]] = set()
    for prefetch in prefetch_for_category(ctx.strategy_category):
        pairs |= prefetch(ctx)
    return pairs


def pairs_missing_from_cache(
    ctx: EngineContext,
    required: set[tuple[Strike, Right]],
) -> set[tuple[Strike, Right]]:
    return {(strike, right) for strike, right in required if (strike, right) not in ctx.cache}


def _right_label(right: Right) -> str:
    return cfg.CALL if right == "Call" else cfg.PUT


def _filter_to_bhavcopy_backed(
    ctx: EngineContext,
    missing: set[tuple[Strike, Right]],
) -> tuple[set[tuple[Strike, Right]], set[tuple[Strike, Right]]]:
    from icici_breeze_backend.app.services.reference_data.bhavcopy_store import has_bhavcopy_quote

    fetchable: set[tuple[Strike, Right]] = set()
    skipped: set[tuple[Strike, Right]] = set()
    for strike, right in missing:
        if has_bhavcopy_quote(
            ctx.stock_code,
            ctx.expiry_display,
            _right_label(right),
            strike,
            ctx.exchange_code,
        ):
            fetchable.add((strike, right))
        else:
            skipped.add((strike, right))
    return fetchable, skipped


def plan_targeted_fetches(ctx: EngineContext) -> set[tuple[Strike, Right]]:
    """Chain-only mode: all quotes must come from bulk chain ingest."""
    del ctx
    return set()
