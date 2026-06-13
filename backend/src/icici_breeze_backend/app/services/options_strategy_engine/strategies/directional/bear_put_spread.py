"""Bear put spread strategy calculator."""
from __future__ import annotations

from icici_breeze_backend.app.services.options_strategy_engine.strategies.directional._common import (
    prefetch_all_conviction_strikes,
    run_spread_profiles,
)
from icici_breeze_backend.app.services.options_strategy_engine.types import EngineContext, Right, StrategyResult


def calc_bear_put_spread(ctx: EngineContext) -> list[StrategyResult]:
    return run_spread_profiles(
        ctx,
        sid="bear_put_spread",
        base_name="Bear Put Spread",
        right="Put",
        spread_kind="bear_put",
    )


def prefetch_bear_put_spread(ctx: EngineContext) -> set[tuple[int, Right]]:
    return prefetch_all_conviction_strikes(
        ctx,
        "Put",
        include_spread_window=True,
        strategy_id="bear_put_spread",
    )
