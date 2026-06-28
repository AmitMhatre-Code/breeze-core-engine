"""Tests for strike planner targeted fetch filtering."""
from unittest.mock import MagicMock, patch

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


@patch("icici_breeze_backend.app.services.options_strategy_engine.strike_planner.prefetch_for_category")
@patch("icici_breeze_backend.app.services.options_strategy_engine.strike_planner.resolve_quote_source", return_value="bhavcopy")
@patch("icici_breeze_backend.app.services.reference_data.bhavcopy_store.has_bhavcopy_quote")
def test_plan_targeted_fetches_filters_non_bhavcopy_pairs(mock_has_bhav, _mock_source, mock_prefetch):
    mock_prefetch.return_value = [lambda ctx: {(24000, "Call"), (11800, "Put")}]
    mock_has_bhav.side_effect = lambda stock, expiry, right, strike, exchange: strike == 24000

    to_fetch = plan_targeted_fetches(_ctx())

    assert to_fetch == {(24000, "Call")}
    assert mock_has_bhav.call_count == 2


@patch("icici_breeze_backend.app.services.options_strategy_engine.strike_planner.prefetch_for_category")
@patch("icici_breeze_backend.app.services.options_strategy_engine.strike_planner.resolve_quote_source", return_value="icici_api")
@patch("icici_breeze_backend.app.services.reference_data.bhavcopy_store.has_bhavcopy_quote")
def test_plan_targeted_fetches_keeps_all_missing_when_icici_api(mock_has_bhav, _mock_source, mock_prefetch):
    mock_prefetch.return_value = [lambda ctx: {(24000, "Call"), (11800, "Put")}]

    to_fetch = plan_targeted_fetches(_ctx())

    assert to_fetch == {(24000, "Call"), (11800, "Put")}
    mock_has_bhav.assert_not_called()
