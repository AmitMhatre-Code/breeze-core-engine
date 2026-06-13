"""Bull call spread strategy calculator."""
from __future__ import annotations

from icici_breeze_backend.app.services.options_strategy_engine.strategies.directional._common import (
    prefetch_all_conviction_strikes,
    run_spread_profiles,
)
from icici_breeze_backend.app.services.options_strategy_engine.types import EngineContext, Right, StrategyResult


def calc_bull_call_spread(ctx: EngineContext) -> list[StrategyResult]:
    return run_spread_profiles(
        ctx,
        sid="bull_call_spread",
        base_name="Bull Call Spread",
        right="Call",
        spread_kind="bull_call",
    )


def prefetch_bull_call_spread(ctx: EngineContext) -> set[tuple[int, Right]]:
    return prefetch_all_conviction_strikes(
        ctx,
        "Call",
        include_spread_window=True,
        strategy_id="bull_call_spread",
    )
