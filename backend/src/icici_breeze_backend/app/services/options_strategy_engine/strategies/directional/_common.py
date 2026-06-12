"""Shared helpers for directional strategy calculators."""
from __future__ import annotations

from icici_breeze_backend.app.services.options_strategy_engine.delta_anchor import (
    DELTA_TOLERANCE,
    profile_deltas,
)
from icici_breeze_backend.app.services.options_strategy_engine.types import EngineContext


def long_short_targets(ctx: EngineContext) -> tuple[float, float]:
    return profile_deltas(ctx.risk_reward_profile)


def delta_match(q_delta: float | None, target: float) -> bool:
    if q_delta is None:
        return False
    return abs(abs(q_delta) - target) <= DELTA_TOLERANCE
