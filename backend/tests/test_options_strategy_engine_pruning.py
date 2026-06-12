"""Unit tests for strategy engine pruning, anchors, and sizing (no broker I/O)."""
import importlib
import unittest
from unittest.mock import MagicMock

from icici_breeze_backend.app.services.options_strategy_engine.anchors import (
    STRANGLE_OTM_PAIRS,
    build_anchor_index,
    max_steps_for_strategy,
)
from icici_breeze_backend.app.services.options_strategy_engine.pruning import (
    DELTA_INCOME_SHORT,
    delta_in_window,
    iron_condor_candidates,
    passes_economic_prune,
    pop_within_tolerance,
    top_k_strikes,
    wing_strikes_from_multipliers,
)
from icici_breeze_backend.app.services.options_strategy_engine.sizing import size_lots
from icici_breeze_backend.app.services.options_strategy_engine.types import (
    MAX_IRON_CONDOR_CANDIDATES,
    WING_WIDTH_MULTIPLIERS,
    EngineContext,
    QuoteRow,
)


def _mock_ctx() -> EngineContext:
    strikes = list(range(22000, 24600, 50))
    cache: dict = {}
    for s in strikes:
        delta = 0.22 if 22600 <= s <= 23800 else 0.35
        for right in ("Call", "Put"):
            d = delta if right == "Call" else -delta
            cache[(s, right)] = QuoteRow(
                strike=s,
                right=right,
                ltp=50.0,
                best_bid_price=49.0,
                best_offer_price=51.0,
                total_buy_qty=100,
                total_sell_qty=100,
                buy_sell_ratio=1.0,
                spot_price=23310.0,
                delta=d,
                liquidity_score=0.8,
            )
    return EngineContext(
        processor=MagicMock(),
        user_id="u1",
        stock_code="NIFTY",
        exchange_code="NFO",
        expiry_display="20-Jun-2026",
        margin_rupees=1_000_000,
        max_loss_rupees=500_000,
        min_pop_pct=65.0,
        provision_elm=False,
        strategy_category="income",
        lot_size=75,
        strikes=strikes,
        strike_step=50,
        search_interval=50,
        spot=23310,
        atm_strike=23300,
        cache=cache,
    )


class TestAnchors(unittest.TestCase):
    def test_otm_buckets(self):
        strikes = list(range(23000, 24100, 50))
        idx = build_anchor_index(strikes, 23525, 50)
        self.assertEqual(idx.atm, 23500)
        self.assertEqual(idx.otm_ce[1], 23550)
        self.assertEqual(idx.otm_pe[1], 23450)

    def test_strategy_window_sizes(self):
        self.assertEqual(max_steps_for_strategy("short_strangle"), 5)
        self.assertEqual(max_steps_for_strategy("long_strangle"), 8)
        self.assertEqual(max_steps_for_strategy("iron_condor"), 5)


class TestPruning(unittest.TestCase):
    def test_delta_window(self):
        self.assertTrue(delta_in_window(0.20, DELTA_INCOME_SHORT))
        self.assertFalse(delta_in_window(0.40, DELTA_INCOME_SHORT))

    def test_wing_multipliers_only_spec_values(self):
        liquid = {22000, 22100, 22200}
        wings = wing_strikes_from_multipliers(22500, 100, liquid, wing_is_higher=False)
        self.assertEqual(wings, [22200, 22000])

    def test_iron_condor_candidate_cap(self):
        ctx = _mock_ctx()
        candidates = iron_condor_candidates(ctx)
        self.assertLessEqual(len(candidates), MAX_IRON_CONDOR_CANDIDATES)

    def test_top_k_truncates(self):
        ctx = _mock_ctx()
        pool = ctx.liquid_ce_strikes
        top = top_k_strikes(pool, ctx.cache, "Call", 3, credit=True)
        self.assertLessEqual(len(top), 3)

    def test_pop_tolerance(self):
        self.assertTrue(pop_within_tolerance(68.0, 65.0))
        self.assertFalse(pop_within_tolerance(50.0, 65.0))

    def test_economic_prune_rejects_tiny_credit(self):
        self.assertFalse(passes_economic_prune(net_credit=0.001, min_premium=0.01))


class TestSizing(unittest.TestCase):
    def test_undefined_risk_ignores_max_loss(self):
        lots = size_lots(
            "short_straddle",
            100_000,
            999_999,
            margin_rupees=500_000,
            max_loss_rupees=50_000,
            lot_size=75,
            leg_count=2,
            spot=23310,
            provision_elm=False,
        )
        self.assertEqual(lots, 5)

    def test_defined_risk_uses_min_constraint(self):
        lots = size_lots(
            "bull_put_spread",
            100_000,
            50_000,
            margin_rupees=500_000,
            max_loss_rupees=200_000,
            lot_size=75,
            leg_count=2,
            spot=23310,
            provision_elm=False,
        )
        self.assertEqual(lots, 4)


class TestStranglePairs(unittest.TestCase):
    def test_efficient_pairing_count(self):
        self.assertEqual(len(STRANGLE_OTM_PAIRS), 4)


class TestPackageLazyImport(unittest.TestCase):
    def test_pruning_submodule_without_processor(self):
        mod = importlib.import_module("icici_breeze_backend.app.services.options_strategy_engine.pruning")
        self.assertEqual(mod.MAX_IRON_CONDOR_CANDIDATES, 15)
        self.assertEqual(mod.WING_WIDTH_MULTIPLIERS, WING_WIDTH_MULTIPLIERS)
