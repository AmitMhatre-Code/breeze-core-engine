"""Backward-compatible re-exports — prefer strategies.common."""
from icici_breeze_backend.app.services.options_strategy_engine.strategies.common import *  # noqa: F403
from icici_breeze_backend.app.services.options_strategy_engine.strategies.income.credit_spread import (  # noqa: F401
    credit_spread_wing,
    credit_spread_wing_full,
)
