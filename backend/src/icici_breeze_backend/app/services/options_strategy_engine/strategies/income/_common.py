"""Shared helpers for income strategy calculators."""
from __future__ import annotations

from icici_breeze_backend.app.services.options_strategy_engine.delta_anchor import pop_to_short_delta
from icici_breeze_backend.app.services.options_strategy_engine.types import EngineContext


def short_delta(ctx: EngineContext, short_legs: int = 1) -> float:
    return pop_to_short_delta(ctx.min_pop_pct, short_legs)
