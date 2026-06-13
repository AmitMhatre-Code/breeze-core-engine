"""Unit tests for directional conviction scoring."""
import unittest
import unittest.mock

from icici_breeze_backend.app.services.options_strategy_engine.strategies.directional.scoring import (
    delta_alignment,
    finalize_long_option_score,
    finalize_spread_score,
    normalize_min_max,
    score_long_option_components,
    score_spread_components,
)
from icici_breeze_backend.app.services.options_strategy_engine.types import QuoteRow


class TestDeltaAlignment(unittest.TestCase):
    def test_on_target_is_one(self):
        self.assertAlmostEqual(delta_alignment(0.50, 0.50), 1.0)

    def test_at_tolerance_edge_is_zero(self):
        self.assertAlmostEqual(delta_alignment(0.55, 0.50), 0.0)


class TestNormalizeMinMax(unittest.TestCase):
    def test_spreads_values(self):
        self.assertEqual(normalize_min_max([1.0, 2.0, 3.0]), [0.0, 0.5, 1.0])

    def test_flat_values_all_one(self):
        self.assertEqual(normalize_min_max([2.0, 2.0]), [1.0, 1.0])


class TestLongOptionScoring(unittest.TestCase):
    def test_higher_premium_efficiency_wins_when_normalized(self):
        q = QuoteRow(
            strike=23500,
            right="Call",
            ltp=100.0,
            best_bid_price=99.0,
            best_offer_price=101.0,
            total_buy_qty=100,
            total_sell_qty=100,
            buy_sell_ratio=1.0,
            delta=0.50,
            liquidity_score=0.8,
            iv=0.18,
        )
        ctx = unittest.mock.MagicMock()
        ctx.spot = 23500.0
        ctx.t_years = 0.05
        components = score_long_option_components(
            ctx, q, target_delta=0.50, premium_per_unit=100.0
        )
        low_score, _ = finalize_long_option_score(components, premium_efficiency_norm=0.2)
        high_score, _ = finalize_long_option_score(components, premium_efficiency_norm=0.9)
        self.assertGreater(high_score, low_score)

    def test_pop_not_in_breakdown(self):
        q = QuoteRow(
            strike=23500,
            right="Call",
            ltp=50.0,
            best_bid_price=49.0,
            best_offer_price=51.0,
            total_buy_qty=100,
            total_sell_qty=100,
            buy_sell_ratio=1.0,
            delta=0.50,
            liquidity_score=0.8,
            iv=0.18,
        )
        ctx = unittest.mock.MagicMock()
        ctx.spot = 23500.0
        ctx.t_years = 0.05
        components = score_long_option_components(
            ctx, q, target_delta=0.50, premium_per_unit=50.0
        )
        _, breakdown = finalize_long_option_score(components, premium_efficiency_norm=0.5)
        self.assertNotIn("pop", breakdown)


class TestSpreadScoring(unittest.TestCase):
    def test_finite_reward_risk_used(self):
        long_q = QuoteRow(
            strike=23400,
            right="Call",
            ltp=120.0,
            best_bid_price=119.0,
            best_offer_price=121.0,
            total_buy_qty=100,
            total_sell_qty=100,
            buy_sell_ratio=1.0,
            delta=0.50,
            liquidity_score=0.9,
        )
        short_q = QuoteRow(
            strike=23600,
            right="Call",
            ltp=40.0,
            best_bid_price=39.0,
            best_offer_price=41.0,
            total_buy_qty=100,
            total_sell_qty=100,
            buy_sell_ratio=1.0,
            delta=0.25,
            liquidity_score=0.9,
        )
        components = score_spread_components(
            long_q,
            short_q,
            long_target=0.50,
            short_target=0.25,
            max_gain=10000.0,
            max_loss=5000.0,
            debit_paid=5000.0,
        )
        score, breakdown = finalize_spread_score(
            components, reward_risk_norm=1.0, capital_eff_norm=1.0
        )
        self.assertGreater(score, 0)
        self.assertIn("reward_to_risk", breakdown)
        self.assertNotIn("pop", breakdown)


if __name__ == "__main__":
    unittest.main()
