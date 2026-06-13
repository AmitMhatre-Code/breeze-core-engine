"""Long call strategy calculator."""
from __future__ import annotations

from icici_breeze_backend.app.services.options_strategy_engine.strategies.directional._common import (
    prefetch_all_conviction_strikes,
    run_long_option_profiles,
)
from icici_breeze_backend.app.services.options_strategy_engine.types import EngineContext, Right, StrategyResult


def calc_long_call(ctx: EngineContext) -> list[StrategyResult]:
    return run_long_option_profiles(ctx, sid="long_call", base_name="Long Call", right="Call")


def prefetch_long_call(ctx: EngineContext) -> set[tuple[int, Right]]:
    return prefetch_all_conviction_strikes(ctx, "Call")
