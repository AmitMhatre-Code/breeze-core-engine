"""Unit tests for delta anchoring and global ICICI API pacing."""
import time
import unittest
from unittest.mock import MagicMock, patch

from icici_breeze_backend.app.services.icici_api_pacing import GlobalIciciApiPacer
from icici_breeze_backend.app.services.options_strategy_engine.delta_anchor import (
    pop_to_short_delta,
    profile_deltas,
)
from icici_breeze_backend.app.services.options_strategy_engine.ranking import (
    score_directional_candidate,
)
from icici_breeze_backend.app.services.options_strategy_engine.sizing import size_lots


class TestDeltaAnchor(unittest.TestCase):
    def test_pop_to_short_delta_85_pct(self):
        self.assertAlmostEqual(pop_to_short_delta(85.0, 1), 0.15)

    def test_pop_to_short_delta_strangle_wings(self):
        self.assertAlmostEqual(pop_to_short_delta(85.0, 2), 0.075)

    def test_profile_deltas_moderate(self):
        self.assertEqual(profile_deltas("moderate"), (0.50, 0.30))


class TestDirectionalCER(unittest.TestCase):
    def test_higher_ev_per_capital_wins(self):
        low = score_directional_candidate(60.0, 5000.0, 10000.0)
        high = score_directional_candidate(60.0, 8000.0, 10000.0)
        self.assertGreater(high, low)


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
            unit_short_lots=1,
            spot=23310,
            provision_elm=False,
        )
        self.assertEqual(lots, 5)


if __name__ == "__main__":
    unittest.main()
