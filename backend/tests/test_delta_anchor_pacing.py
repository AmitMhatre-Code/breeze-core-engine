"""Unit tests for delta anchoring and global ICICI API pacing."""
import time
import unittest
from unittest.mock import MagicMock, patch

from icici_breeze_backend.app.services.icici_api_pacing import GlobalIciciApiPacer
from icici_breeze_backend.app.services.options_strategy_engine.delta_anchor import (
    pop_to_short_delta,
    profile_deltas,
)
from icici_breeze_backend.app.services.options_strategy_engine.sizing import size_lots


class TestDeltaAnchor(unittest.TestCase):
    def test_pop_to_short_delta_85_pct(self):
        self.assertAlmostEqual(pop_to_short_delta(85.0, 1), 0.15)

    def test_pop_to_short_delta_strangle_wings(self):
        self.assertAlmostEqual(pop_to_short_delta(85.0, 2), 0.075)

    def test_profile_deltas_spread_moderate(self):
        self.assertEqual(profile_deltas("moderate", kind="spread"), (0.50, 0.25))

    def test_profile_deltas_spread_aggressive(self):
        self.assertEqual(profile_deltas("aggressive", kind="spread"), (0.60, 0.30))

    def test_profile_deltas_long_option_conservative(self):
        self.assertEqual(profile_deltas("conservative", kind="long_option"), (0.60, 0.0))

    def test_profile_deltas_long_option_aggressive(self):
        self.assertEqual(profile_deltas("aggressive", kind="long_option"), (0.40, 0.0))


class TestDirectionalConvictionScore(unittest.TestCase):
    def test_long_option_score_is_finite(self):
        from icici_breeze_backend.app.services.options_strategy_engine.strategies.directional.scoring import (
            finalize_long_option_score,
            score_long_option_components,
        )
        from icici_breeze_backend.app.services.options_strategy_engine.types import QuoteRow
        from unittest.mock import MagicMock

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
        ctx = MagicMock()
        ctx.spot = 23500.0
        ctx.t_years = 0.05
        components = score_long_option_components(
            ctx, q, target_delta=0.50, premium_per_unit=100.0
        )
        score, _ = finalize_long_option_score(components, premium_efficiency_norm=0.5)
        self.assertTrue(0 <= score <= 1.5)


class TestIciciApiPacer(unittest.TestCase):
    def setUp(self) -> None:
        GlobalIciciApiPacer.reset_user("test-user")

    def test_wait_for_slot_enforces_spacing(self):
        GlobalIciciApiPacer.mark_call_complete("test-user")
        t0 = time.monotonic()
        with patch("icici_breeze_backend.app.services.icici_api_pacing.time.sleep") as mock_sleep:
            GlobalIciciApiPacer.wait_for_slot("test-user", 0.25, endpoint="test")
            mock_sleep.assert_called_once()
            wait_arg = mock_sleep.call_args[0][0]
            self.assertGreaterEqual(wait_arg, 0.0)
            self.assertLessEqual(wait_arg, 0.25)
        _ = t0  # timing not asserted (patched sleep)

    def test_503_backoff_capped_at_three_seconds(self):
        b1 = GlobalIciciApiPacer.rate_limit_backoff("test-user", 1.0, endpoint="test")
        b2 = GlobalIciciApiPacer.rate_limit_backoff("test-user", 1.0, endpoint="test")
        b3 = GlobalIciciApiPacer.rate_limit_backoff("test-user", 1.0, endpoint="test")
        self.assertEqual(b1, 1.0)
        self.assertEqual(b2, 2.0)
        self.assertEqual(b3, 3.0)


class TestUndefinedRiskSizing(unittest.TestCase):
    def test_naked_short_ignores_max_loss(self):
        lots = size_lots(
            "naked_ce_short",
            100_000,
            999_999,
            margin_rupees=500_000,
            max_loss_rupees=50_000,
            lot_size=75,
            unit_legs=[],
            spot=23310,
            provision_elm=False,
        )
        self.assertEqual(lots, 5)


if __name__ == "__main__":
    unittest.main()
