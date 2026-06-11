"""Tests for Strategy Builder PoP estimator."""
import random
import unittest
from dataclasses import dataclass
from typing import Literal

from icici_breeze_backend.app.services.strategy_builder_pop import (
    estimate_probability_of_profit,
    portfolio_payoff_at_expiry,
)


@dataclass
class _Leg:
    right: Literal["Call", "Put"]
    side: Literal["Buy", "Sell"]
    strike: int
    quantity: int
    premium_per_unit: float


class TestStrategyBuilderPop(unittest.TestCase):
    def test_bull_call_spread_max_loss_at_low_spot(self):
        legs = [
            _Leg("Call", "Buy", 100, 1, 5.0),
            _Leg("Call", "Sell", 110, 1, 2.0),
        ]
        net_debit = 5.0 - 2.0
        low = portfolio_payoff_at_expiry(50.0, legs, 1)
        self.assertAlmostEqual(low, -net_debit, places=5)

    def test_pop_reproducible_with_seed(self):
        legs = [
            _Leg("Call", "Sell", 24000, 75, 50.0),
            _Leg("Call", "Buy", 24500, 75, 20.0),
        ]
        rng = random.Random(42)
        pop1 = estimate_probability_of_profit(
            23500.0, 30 / 365.0, 0.18, legs, 75, samples=2000, rng=rng
        )
        rng2 = random.Random(42)
        pop2 = estimate_probability_of_profit(
            23500.0, 30 / 365.0, 0.18, legs, 75, samples=2000, rng=rng2
        )
        self.assertEqual(pop1, pop2)
        self.assertGreater(pop1, 0.0)
        self.assertLess(pop1, 100.0)

    def test_pop_zero_when_invalid_inputs(self):
        legs = [_Leg("Call", "Buy", 100, 1, 5.0)]
        self.assertEqual(estimate_probability_of_profit(0, 0.1, 0.2, legs, 1), 0.0)
        self.assertEqual(estimate_probability_of_profit(100, 0, 0.2, legs, 1), 0.0)
