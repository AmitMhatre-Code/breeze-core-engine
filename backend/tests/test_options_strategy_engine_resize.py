"""Unit tests for SPAN-based post-sizing in the strategy engine."""
import asyncio
import unittest
from unittest.mock import MagicMock

from icici_breeze_backend.app.services.options_strategy_engine.budget_resize import (
    resize_results_to_budgets,
)
from icici_breeze_backend.app.services.options_strategy_engine.sizing import (
    rescale_result_to_lots,
    unit_max_loss_per_lot,
)
from icici_breeze_backend.app.services.options_strategy_engine.types import (
    EngineContext,
    StrategyResult,
    TradeLeg,
)


def _ctx(*, margin_rupees: float = 50_000_000, max_loss_rupees: float = 4_000_000) -> EngineContext:
    return EngineContext(
        processor=MagicMock(),
        user_id="u1",
        stock_code="NIFTY",
        exchange_code="NFO",
        expiry_display="16-Jun-2026",
        margin_rupees=margin_rupees,
        max_loss_rupees=max_loss_rupees,
        min_pop_pct=95.0,
        provision_elm=True,
        strategy_category="income",
        lot_size=65,
        strikes=[23600],
        strike_step=50,
        search_interval=50,
        spot=23622.9,
        atm_strike=23600,
    )


class TestRescaleHelpers(unittest.TestCase):
    def test_unit_max_loss_per_lot_from_one_lot_structure(self):
        result = StrategyResult(
            "iron_condor",
            "Iron Condor",
            status="ok",
            max_loss=3035.0,
            legs=[
                TradeLeg("Put", "Sell", 22200, 65, 3.35),
                TradeLeg("Put", "Buy", 22150, 65, 3.45),
            ],
        )
        self.assertEqual(unit_max_loss_per_lot(result, 65), 3035.0)

    def test_rescale_preserves_butterfly_ratios(self):
        result = StrategyResult(
            "long_butterfly",
            "Long Butterfly",
            status="ok",
            max_loss=5000.0,
            risk_reward_ratio="5000 : 10000",
            legs=[
                TradeLeg("Call", "Buy", 23500, 65, 10.0),
                TradeLeg("Call", "Sell", 23600, 130, 20.0),
                TradeLeg("Call", "Buy", 23700, 65, 10.0),
            ],
        )
        rescale_result_to_lots(result, lot_size=65, lots=10)
        self.assertEqual(result.legs[0].quantity, 650)
        self.assertEqual(result.legs[1].quantity, 1300)
        self.assertEqual(result.legs[2].quantity, 650)
        self.assertEqual(result.max_loss, 50000.0)


class TestResizeResultsToBudgets(unittest.TestCase):
    def test_defined_risk_scales_down_to_margin_budget(self):
        ctx = _ctx()
        result = StrategyResult(
            "iron_condor",
            "Iron Condor",
            status="ok",
            max_loss=3035.0,
            net_premium=718.75,
            risk_reward_ratio="3035 : 719",
            legs=[
                TradeLeg("Put", "Sell", 22200, 65, 3.35),
                TradeLeg("Put", "Buy", 22150, 65, 3.45),
                TradeLeg("Call", "Sell", 24150, 65, 16.7),
                TradeLeg("Call", "Buy", 24200, 65, 13.3),
            ],
        )
        proc = MagicMock()
        proc.strategy_builder_margin.return_value = {
            "Success": {"span_margin_required": 65_624.0},
            "Status": 200,
        }
        asyncio.run(
            resize_results_to_budgets(
            proc, "u1", "NFO", "NIFTY", "16-Jun-2026", [result], ctx
        )
        )
        self.assertEqual(result.status, "ok")
        # SPAN 65_624 + ELM ~61_422 per lot → ~393 lots within 5 Cr margin budget
        self.assertEqual(result.legs[0].quantity, 65 * 393)
        self.assertLessEqual(result.max_loss, ctx.max_loss_rupees)

    def test_undefined_risk_uses_margin_only(self):
        ctx = _ctx()
        result = StrategyResult(
            "naked_pe_short",
            "Naked PE Short",
            status="ok",
            max_loss=None,
            net_premium=572.0,
            risk_reward_ratio="Unlimited : 572",
            legs=[TradeLeg("Put", "Sell", 22950, 65, 8.8)],
        )
        proc = MagicMock()
        proc.strategy_builder_margin.return_value = {
            "Success": {"span_margin_required": 2_400_000.0},
            "Status": 200,
        }
        asyncio.run(
            resize_results_to_budgets(
            proc, "u1", "NFO", "NIFTY", "16-Jun-2026", [result], ctx
        )
        )
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.legs[0].quantity, 65 * 20)

    def test_skips_when_span_unavailable(self):
        ctx = _ctx()
        result = StrategyResult(
            "short_strangle",
            "Short Strangle",
            status="ok",
            legs=[
                TradeLeg("Call", "Sell", 24150, 65, 10.0),
                TradeLeg("Put", "Sell", 22950, 65, 8.0),
            ],
        )
        proc = MagicMock()
        proc.strategy_builder_margin.return_value = {
            "Success": {"span_margin_required": 0},
            "Status": 200,
        }
        asyncio.run(
            resize_results_to_budgets(
            proc, "u1", "NFO", "NIFTY", "16-Jun-2026", [result], ctx
        )
        )
        self.assertEqual(result.status, "skipped")
        self.assertEqual(result.legs, [])


if __name__ == "__main__":
    unittest.main()
