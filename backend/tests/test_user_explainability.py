"""Tests for user explainability report builder."""
import unittest

from icici_breeze_backend.audit.user_explainability import (
    USER_REPORT_SCHEMA_VERSION,
    build_user_explainability_report,
    resolve_explainability_from_audit_doc,
    split_report_into_levels,
)
from icici_breeze_backend.app.services.options_strategy_engine.strategies.income._common import (
    BADGE_CAPITAL,
    BADGE_INCOME,
    BADGE_MARGIN,
)


def _base_request(**overrides):
    req = {
        "margin_lacs": 5.0,
        "max_loss_lacs": 2.0,
        "min_pop_pct": 65.0,
        "min_ann_return_pct": 5.0,
        "strategy_category": "income",
    }
    req.update(overrides)
    return req


def _base_summary(**overrides):
    summary = {
        "strategies_ok": [],
        "strategies_skipped": [],
    }
    summary.update(overrides)
    return summary


class TestUserExplainabilityReport(unittest.TestCase):
    def test_schema_version_present(self):
        report = build_user_explainability_report(
            request=_base_request(),
            strategy_evaluations={},
            trades=[],
            summary=_base_summary(),
        )
        self.assertEqual(report["user_report_schema_version"], USER_REPORT_SCHEMA_VERSION)

    def test_pop_floor_skip_why_not_and_what_if(self):
        ev = {
            "strategy_summary": {
                "generated": 12,
                "passed_pop": 0,
                "returned": 0,
            },
            "pop_policy": {
                "used_for_filtering": True,
                "ignored": False,
            },
            "rejection_funnel": {"pop_floor": 12},
            "near_misses": [
                {
                    "rejection_reason": "pop_floor",
                    "metrics": {"pop_pct": 57.4},
                }
            ],
            "distributions": {},
        }
        trades = [
            {
                "strategy_id": "short_straddle",
                "strategy_name": "Short Straddle",
                "status": "skipped",
                "skip_reason": "No Short Straddle meets minimum PoP",
            }
        ]
        report = build_user_explainability_report(
            request=_base_request(),
            strategy_evaluations={"short_straddle": ev},
            trades=trades,
            summary=_base_summary(
                strategies_skipped=[
                    {"strategy_id": "short_straddle", "skip_reason": trades[0]["skip_reason"]}
                ]
            ),
        )
        why_not = report["why_not"][0]
        self.assertEqual(why_not["strategy_id"], "short_straddle")
        self.assertIn("57.4%", why_not["explanation"])
        self.assertIn("65%", why_not["explanation"])
        self.assertEqual(why_not["primary_reason"], "pop_floor")

        what_if = report["what_if_insights"]
        self.assertTrue(any("Reducing your PoP threshold below" in i["message"] for i in what_if))
        self.assertTrue(any("short_straddle" in i["affected_strategies"] for i in what_if))

    def test_income_ok_with_badges_funnel_and_metrics(self):
        ev = {
            "strategy_summary": {
                "generated": 20,
                "passed_pop": 8,
                "passed_liquidity": 15,
                "passed_credit": 14,
                "passed_constraints": 12,
                "passed_economic_prune": 10,
                "passed_capital": 8,
                "passed_loss": 8,
                "margin_refined": 3,
                "returned": 1,
            },
            "pop_policy": {"used_for_filtering": True, "ignored": False},
            "rejection_funnel": {},
            "winners": [],
        }
        trades = [
            {
                "strategy_id": "iron_condor",
                "strategy_name": "Iron Condor",
                "status": "ok",
                "badges": [BADGE_INCOME, BADGE_CAPITAL],
                "pop_pct": 72.5,
                "net_premium": 12500.0,
                "annualized_return_pct": 18.2,
                "span_margin": 85000.0,
                "variant_rank": 1,
                "ranking_summary": "Best feasible trade for Income Maximiser.",
            }
        ]
        report = build_user_explainability_report(
            request=_base_request(),
            strategy_evaluations={"iron_condor": ev},
            trades=trades,
            summary=_base_summary(strategies_ok=["iron_condor"]),
        )
        why_this = report["why_this"][0]
        self.assertEqual(why_this["strategy_id"], "iron_condor")
        funnel = {s["stage"]: s["count"] for s in why_this["funnel"]}
        self.assertEqual(funnel["candidates_generated"], 20)
        self.assertEqual(funnel["passed_pop"], 8)
        self.assertEqual(funnel["recommended"], 1)

        returned = why_this["returned_trades"][0]
        self.assertEqual(returned["badges"], [BADGE_INCOME, BADGE_CAPITAL])
        self.assertEqual(len(returned["badge_explanations"]), 2)
        self.assertEqual(returned["metrics"]["pop_pct"], 72.5)
        self.assertEqual(returned["metrics"]["net_credit"], 12500.0)

    def test_below_min_ann_return_what_if(self):
        ev = {
            "strategy_summary": {"generated": 5, "returned": 0},
            "pop_policy": {"used_for_filtering": True, "ignored": False},
            "rejection_funnel": {"min_ann_return": 3},
            "near_misses": [
                {
                    "rejection_reason": "below_min_ann_return",
                    "context": "Best annualized return 3.2% below minimum 5.0%.",
                    "metrics": {"annualized_return_pct": 3.2},
                }
            ],
        }
        trades = [
            {
                "strategy_id": "short_strangle",
                "strategy_name": "Short Strangle",
                "status": "skipped",
                "skip_reason": "Below min ann return",
            }
        ]
        report = build_user_explainability_report(
            request=_base_request(),
            strategy_evaluations={"short_strangle": ev},
            trades=trades,
            summary=_base_summary(),
        )
        why_not = report["why_not"][0]
        self.assertIn("3.2%", why_not["explanation"])
        self.assertTrue(
            any("Lowering your minimum annual return" in i["message"] for i in report["what_if_insights"])
        )

    def test_directional_pop_not_applied(self):
        ev = {
            "strategy_summary": {
                "generated": 6,
                "passed_pop": 0,
                "passed_liquidity": 6,
                "passed_credit": 5,
                "passed_constraints": 4,
                "margin_refined": 1,
                "returned": 1,
            },
            "pop_policy": {"used_for_filtering": False, "ignored": True},
            "rejection_funnel": {},
        }
        trades = [
            {
                "strategy_id": "bull_call_spread",
                "strategy_name": "Bull Call Spread",
                "status": "ok",
                "conviction_profile": "moderate",
                "pop_pct": 45.0,
                "net_premium": -5000.0,
                "annualized_return_pct": None,
                "span_margin": 40000.0,
            }
        ]
        report = build_user_explainability_report(
            request=_base_request(strategy_category="bullish", min_pop_pct=65),
            strategy_evaluations={"bull_call_spread": ev},
            trades=trades,
            summary=_base_summary(strategies_ok=["bull_call_spread"]),
        )
        why_this = report["why_this"][0]
        pop_stage = next(s for s in why_this["funnel"] if s["stage"] == "passed_pop")
        self.assertEqual(pop_stage["count"], "not_applied")
        self.assertIsNotNone(why_this["pop_filter_note"])

    def test_executive_summary_counts(self):
        report = build_user_explainability_report(
            request=_base_request(),
            strategy_evaluations={
                "iron_condor": {"rejection_funnel": {}, "strategy_summary": {}},
                "short_straddle": {"rejection_funnel": {"pop_floor": 5}, "strategy_summary": {}},
            },
            trades=[
                {"strategy_id": "iron_condor", "strategy_name": "Iron Condor", "status": "ok"},
                {
                    "strategy_id": "short_straddle",
                    "strategy_name": "Short Straddle",
                    "status": "skipped",
                    "skip_reason": "PoP",
                },
            ],
            summary=_base_summary(),
        )
        exec_sum = report["executive_summary"]
        self.assertEqual(exec_sum["strategies_evaluated"], 2)
        self.assertEqual(len(exec_sum["strategies_recommended"]), 1)
        self.assertEqual(len(exec_sum["strategies_skipped"]), 1)
        self.assertEqual(exec_sum["user_inputs"]["margin_lacs"], 5.0)
        self.assertEqual(exec_sum["user_inputs"]["min_pop_pct"], 65.0)

    def test_recommended_pop_elimination_insight(self):
        trades = [
            {
                "strategy_id": "iron_condor",
                "strategy_name": "Iron Condor",
                "status": "ok",
                "badges": [BADGE_INCOME],
                "pop_pct": 73.0,
            }
        ]
        report = build_user_explainability_report(
            request=_base_request(min_pop_pct=65),
            strategy_evaluations={"iron_condor": {"rejection_funnel": {}, "strategy_summary": {}}},
            trades=trades,
            summary=_base_summary(strategies_ok=["iron_condor"]),
        )
        self.assertTrue(
            any(
                "Increasing your PoP threshold above 73" in i["message"]
                for i in report["what_if_insights"]
            )
        )

    def test_split_report_into_levels(self):
        report = build_user_explainability_report(
            request=_base_request(),
            strategy_evaluations={},
            trades=[],
            summary=_base_summary(),
        )
        levels = split_report_into_levels(report)
        self.assertEqual(levels["schema_version"], USER_REPORT_SCHEMA_VERSION)
        self.assertIn("user_inputs", levels["level_1"])
        self.assertIn("why_this", levels["level_2"])
        self.assertIn("why_not", levels["level_2"])
        self.assertIsInstance(levels["level_3"], list)

    def test_resolve_explainability_rebuilds_from_evaluations(self):
        doc = {
            "request": _base_request(),
            "summary": {
                "strategies_ok": ["iron_condor"],
                "strategies_skipped": [
                    {"strategy_id": "short_straddle", "skip_reason": "PoP too low"}
                ],
            },
            "strategy_evaluations": {
                "iron_condor": {
                    "strategy_summary": {"generated": 5, "returned": 1},
                    "pop_policy": {"used_for_filtering": True, "ignored": False},
                    "rejection_funnel": {},
                    "winners": [
                        {
                            "metrics": {
                                "pop_pct": 70.0,
                                "net_collected": 10000.0,
                                "annualized_return_pct": 12.0,
                                "margin": 80000.0,
                                "badges": [BADGE_INCOME],
                            }
                        }
                    ],
                },
                "short_straddle": {
                    "strategy_summary": {"generated": 3, "returned": 0},
                    "pop_policy": {"used_for_filtering": True, "ignored": False},
                    "rejection_funnel": {"pop_floor": 3},
                    "near_misses": [{"metrics": {"pop_pct": 58.0}}],
                },
            },
        }
        levels = resolve_explainability_from_audit_doc(doc)
        self.assertIsNotNone(levels)
        assert levels is not None
        self.assertEqual(levels["level_1"]["strategies_evaluated"], 2)
        self.assertEqual(len(levels["level_2"]["why_this"]), 1)
        self.assertEqual(len(levels["level_2"]["why_not"]), 1)


if __name__ == "__main__":
    unittest.main()
