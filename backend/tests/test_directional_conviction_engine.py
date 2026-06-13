"""Integration tests for conviction-based directional engines."""
import unittest
from unittest.mock import MagicMock

from icici_breeze_backend.app.services.options_strategy_engine.strategies.directional.bull_call_spread import (
    calc_bull_call_spread,
)
from icici_breeze_backend.app.services.options_strategy_engine.strategies.directional.long_call import (
    calc_long_call,
)
from icici_breeze_backend.app.services.options_strategy_engine.types import EngineContext, QuoteRow
from icici_breeze_backend.audit.strategy_evaluation_audit import (
    StrategyAuditCollector,
    pop_policy_for,
)


def _quote(strike: int, right: str, *, delta: float, ask: float, bid: float | None = None) -> QuoteRow:
    bid = bid if bid is not None else ask - 1.0
    return QuoteRow(
        strike=strike,
        right=right,
        ltp=(bid + ask) / 2,
        best_bid_price=bid,
        best_offer_price=ask,
        total_buy_qty=1000,
        total_sell_qty=1000,
        buy_sell_ratio=1.0,
        spot_price=23500.0,
        delta=delta,
        liquidity_score=0.85,
        iv=0.18,
    )


class TestDirectionalConvictionEngine(unittest.TestCase):
    def _ctx(self, cache: dict, *, with_audit: bool = False) -> EngineContext:
        strikes = sorted({s for s, _ in cache})
        ctx = EngineContext(
            processor=MagicMock(),
            user_id="u1",
            stock_code="NIFTY",
            exchange_code="NFO",
            expiry_display="27-Jun-2026",
            margin_rupees=500_000,
            max_loss_rupees=200_000,
            min_pop_pct=65.0,
            provision_elm=False,
            strategy_category="bullish",
            risk_reward_profile="moderate",
            lot_size=25,
            strikes=strikes,
            strike_step=50,
            search_interval=50,
            spot=23500.0,
            atm_strike=23500,
            cache=cache,
        )
        if with_audit:
            ctx.audit_collector = StrategyAuditCollector(strategy_id="long_call", detail_level="debug")
        return ctx

    def test_long_call_returns_up_to_three_profiles(self):
        cache = {
            (23400, "Call"): _quote(23400, "Call", delta=0.40, ask=150.0),
            (23450, "Call"): _quote(23450, "Call", delta=0.45, ask=130.0),
            (23500, "Call"): _quote(23500, "Call", delta=0.50, ask=110.0),
            (23550, "Call"): _quote(23550, "Call", delta=0.55, ask=95.0),
            (23600, "Call"): _quote(23600, "Call", delta=0.60, ask=80.0),
        }
        results = calc_long_call(self._ctx(cache))
        self.assertGreaterEqual(len(results), 1)
        self.assertLessEqual(len(results), 3)
        profiles = {r.conviction_profile for r in results if r.status == "ok"}
        self.assertTrue(profiles.issubset({"conservative", "moderate", "aggressive"}))
        for r in results:
            if r.status != "ok":
                continue
            self.assertIsNotNone(r.hero_metric)
            self.assertEqual(r.hero_metric.label, "Capital at Risk")
            self.assertIsNotNone(r.engine_score)
            self.assertIsNotNone(r.score_breakdown)

    def test_bull_call_spread_returns_list(self):
        cache = {
            (23400, "Call"): _quote(23400, "Call", delta=0.40, ask=150.0),
            (23450, "Call"): _quote(23450, "Call", delta=0.50, ask=120.0),
            (23550, "Call"): _quote(23550, "Call", delta=0.30, ask=70.0),
            (23600, "Call"): _quote(23600, "Call", delta=0.25, ask=55.0),
            (23650, "Call"): _quote(23650, "Call", delta=0.60, ask=45.0),
        }
        results = calc_bull_call_spread(self._ctx(cache))
        self.assertIsInstance(results, list)
        ok = [r for r in results if r.status == "ok"]
        if ok:
            r = ok[0]
            self.assertEqual(r.hero_metric.label, "Reward : Risk")
            self.assertIn("reward_to_risk", r.score_breakdown or {})

    def test_ranking_summary_omits_conviction_label(self):
        cache = {
            (23400, "Call"): _quote(23400, "Call", delta=0.40, ask=150.0),
            (23450, "Call"): _quote(23450, "Call", delta=0.50, ask=120.0),
            (23550, "Call"): _quote(23550, "Call", delta=0.30, ask=70.0),
            (23600, "Call"): _quote(23600, "Call", delta=0.25, ask=55.0),
        }
        results = calc_bull_call_spread(self._ctx(cache))
        ok = [r for r in results if r.status == "ok"]
        if not ok:
            self.skipTest("no bull call spread candidates in fixture")
        for r in ok:
            self.assertIsNotNone(r.ranking_summary)
            self.assertNotIn("conviction", r.ranking_summary.lower())
            self.assertTrue(r.ranking_summary.startswith("Δ target"))

    def test_long_call_conservative_picks_higher_delta(self):
        cache = {
            (23400, "Call"): _quote(23400, "Call", delta=0.40, ask=150.0),
            (23500, "Call"): _quote(23500, "Call", delta=0.50, ask=110.0),
            (23600, "Call"): _quote(23600, "Call", delta=0.60, ask=80.0),
        }
        results = calc_long_call(self._ctx(cache))
        conservative = next(
            (r for r in results if r.conviction_profile == "conservative" and r.status == "ok"),
            None,
        )
        self.assertIsNotNone(conservative)
        self.assertEqual(conservative.legs[0].strike, 23600)

    def test_long_call_tolerance_widening_when_tight_window_empty(self):
        cache = {
            (23350, "Call"): _quote(23350, "Call", delta=0.32, ask=170.0),
        }
        ctx = self._ctx(cache, with_audit=True)
        results = calc_long_call(ctx)
        aggressive = next(
            (r for r in results if r.conviction_profile == "aggressive" and r.status == "ok"),
            None,
        )
        self.assertIsNotNone(aggressive)
        audit_doc = ctx.audit_collector.to_dict()
        profile_audits = audit_doc.get("profile_audits", [])
        aggressive_audit = next(
            (p for p in profile_audits if p["conviction_profile"] == "aggressive"),
            None,
        )
        self.assertIsNotNone(aggressive_audit)
        self.assertEqual(aggressive_audit["status"], "success")
        self.assertGreater(aggressive_audit["widening_attempts"], 0)
        self.assertGreater(aggressive_audit["final_delta_tolerance"], 0.05)

    def test_directional_pop_policy_ignored(self):
        policy = pop_policy_for("long_call")
        self.assertFalse(policy.used_for_filtering)
        self.assertFalse(policy.used_for_ranking)
        self.assertTrue(policy.ignored)


if __name__ == "__main__":
    unittest.main()
