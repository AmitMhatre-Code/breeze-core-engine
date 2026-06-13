"""Long put strategy calculator."""
from __future__ import annotations

from icici_breeze_backend.app.services.options_strategy_engine.strategies.directional._common import (
    prefetch_all_conviction_strikes,
    run_long_option_profiles,
)
from icici_breeze_backend.app.services.options_strategy_engine.types import EngineContext, Right, StrategyResult


def calc_long_put(ctx: EngineContext) -> list[StrategyResult]:
    return run_long_option_profiles(ctx, sid="long_put", base_name="Long Put", right="Put")


def prefetch_long_put(ctx: EngineContext) -> set[tuple[int, Right]]:
    return prefetch_all_conviction_strikes(ctx, "Put")
