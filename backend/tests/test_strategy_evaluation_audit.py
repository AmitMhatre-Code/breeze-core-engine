"""Unit tests for structured strategy evaluation audit helpers."""
import unittest

from icici_breeze_backend.audit.strategy_evaluation_audit import (
    StrategyAuditCollector,
    build_histogram,
    build_rejection_funnel_by_pop_bucket,
    candidate_id_for_legs,
    canonical_rejection_reason,
    credit_bucket,
    pop_bucket_label,
    pop_policy_for,
    strategy_config_snapshot,
)
from icici_breeze_backend.app.services.options_strategy_engine.types import TradeLeg


class TestStrategyEvaluationAudit(unittest.TestCase):
    def test_pop_bucket_label(self):
        self.assertEqual(pop_bucket_label(64.0, 65.0), "<65")
        self.assertEqual(pop_bucket_label(65.5, 65.0), "65-66")
        self.assertEqual(pop_bucket_label(68.0, 65.0, band_width=2.0), ">=68")

    def test_candidate_id_stable(self):
        legs = [
            TradeLeg("Put", "Sell", 23600, 75, 20.0),
            TradeLeg("Put", "Buy", 23550, 75, 5.0),
        ]
        a = candidate_id_for_legs(legs)
        b = candidate_id_for_legs(legs)
        self.assertEqual(a, b)
        self.assertEqual(len(a), 16)

    def test_canonical_rejection_reason(self):
        self.assertEqual(canonical_rejection_reason("no_credit"), "min_credit")
        self.assertEqual(canonical_rejection_reason("illiquid_wing"), "liquidity")
        self.assertEqual(canonical_rejection_reason("liquidity"), "liquidity")
        self.assertEqual(canonical_rejection_reason("unknown_reason"), "other")

    def test_build_histogram(self):
        hist = build_histogram([1.0, 6.0, 11.0], credit_bucket)
        self.assertEqual(hist["0-5"], 1)
        self.assertEqual(hist["5-10"], 1)
        self.assertEqual(hist["10-15"], 1)

    def test_rejection_funnel_by_pop_bucket(self):
        evaluations = [
            {"outcome": "rejected", "reject_reason": "pop_floor", "pop_pct": 65.5},
            {"outcome": "rejected", "reject_reason": "economic_prune", "pop_pct": 66.2},
            {"outcome": "accepted", "pop_pct": 70.0},
        ]
        funnel = build_rejection_funnel_by_pop_bucket(evaluations, min_pop_pct=65.0)
        self.assertEqual(funnel["65-66"]["pop_floor"], 1)
        self.assertEqual(funnel["66-67"]["economic_prune"], 1)

    def test_collector_summary_vs_debug(self):
        collector = StrategyAuditCollector(strategy_id="bull_put_spread", detail_level="summary")
        collector.record_generated()
        collector.record("pop_floor", short_strike=23600)
        collector.record_evaluation(
            outcome="rejected",
            reject_reason="pop_floor",
            pop_pct=60.0,
            short_strike=23600,
        )
        summary = collector.to_dict()
        self.assertNotIn("candidate_traces", summary)
        self.assertIn("rejection_funnel", summary)
        self.assertEqual(summary["strategy_summary"]["generated"], 1)

        debug = StrategyAuditCollector(strategy_id="bull_put_spread", detail_level="debug")
        debug.record_evaluation(outcome="rejected", reject_reason="pop_floor", pop_pct=60.0)
        debug_doc = debug.to_dict()
        self.assertIn("candidate_traces", debug_doc)
        self.assertGreater(len(debug_doc["candidate_traces"]), 0)

    def test_pop_policy_registry(self):
        income = pop_policy_for("bull_put_spread")
        self.assertTrue(income.used_for_filtering)
        self.assertFalse(income.used_for_ranking)
        self.assertIsNone(income.pop_weight)
        directional = pop_policy_for("bull_call_spread")
        self.assertFalse(directional.used_for_filtering)
        self.assertFalse(directional.used_for_ranking)
        self.assertTrue(directional.ignored)

    def test_strategy_config_snapshot_bps(self):
        snap = strategy_config_snapshot("bull_put_spread")
        self.assertIn("SPAN_SHORTLIST_N", snap)
        self.assertIn("OBJECTIVE_BADGES", snap)

    def test_strategy_config_snapshot_directional(self):
        snap = strategy_config_snapshot("long_call")
        self.assertIn("CONVICTION_PROFILES", snap)
        self.assertIn("delta_templates", snap)
        self.assertIn("long_option", snap["delta_templates"])
        self.assertIn("spread", snap["delta_templates"])
        self.assertIn("DELTA_TOLERANCE_SEQUENCE", snap)

    def test_directional_stage_dedupe_and_funnel_invariants(self):
        from icici_breeze_backend.audit.strategy_evaluation_audit import (
            record_directional_candidate_stage,
            record_directional_profile_winner,
        )

        collector = StrategyAuditCollector(strategy_id="long_call", detail_level="summary")
        legs = [TradeLeg("Call", "Buy", 23500, 25, 100.0)]
        cid = candidate_id_for_legs(legs)
        for stage in ("generated", "passed_liquidity", "passed_credit", "passed_constraints"):
            record_directional_candidate_stage(
                collector,
                candidate_id=cid,
                stage=stage,
                conviction_profile="moderate",
            )
        record_directional_candidate_stage(
            collector,
            candidate_id=cid,
            stage="generated",
            conviction_profile="conservative",
        )
        record_directional_profile_winner(
            collector,
            legs,
            conviction_profile="moderate",
            metrics={"pop_pct": 50.0, "engine_score": 0.8},
        )
        summary = collector.stage_counts
        self.assertEqual(summary["generated"], 1)
        self.assertLessEqual(summary["passed_liquidity"], summary["generated"])
        self.assertLessEqual(summary["passed_credit"], summary["passed_liquidity"])
        self.assertLessEqual(summary["passed_constraints"], summary["passed_credit"])
        self.assertLessEqual(summary["returned"], summary["passed_constraints"])
        self.assertEqual(summary["returned"], 1)


if __name__ == "__main__":
    unittest.main()
