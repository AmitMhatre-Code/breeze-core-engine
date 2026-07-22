"""Tests for strike planner targeted fetch filtering."""
from unittest.mock import MagicMock

from icici_breeze_backend.app.services.options_strategy_engine.strike_planner import (
    plan_targeted_fetches,
)
from icici_breeze_backend.app.services.options_strategy_engine.types import EngineContext


def _ctx() -> EngineContext:
    return EngineContext(
        processor=MagicMock(),
        user_id="u1",
        stock_code="NIFTY",
        exchange_code="NFO",
        expiry_display="30-Jun-2026",
        margin_rupees=500_000,
        max_loss_rupees=None,
        min_pop_pct=98.0,
        provision_elm=True,
        strategy_category="income",
        lot_size=65,
        strikes=[24000, 24050],
        strike_step=50,
        search_interval=50,
        spot=24050.0,
        atm_strike=24050,
        allow_infinite_loss=True,
    )


def test_plan_targeted_fetches_chain_only_returns_empty():
    assert plan_targeted_fetches(_ctx()) == set()
