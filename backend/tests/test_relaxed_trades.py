"""Relaxed near-miss trades and infinite-loss segregation."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from icici_breeze_backend.app.services.options_strategy_engine.compliance_split import (
    split_and_segregate_results,
)
from icici_breeze_backend.app.services.options_strategy_engine.sizing import size_lots
from icici_breeze_backend.app.services.options_strategy_engine.strategies.income._common import (
    income_constraint_violations,
    mark_relaxed_result,
)
from icici_breeze_backend.app.services.options_strategy_engine.strategies.income.short_straddle import (
    calc_short_straddle,
)
from icici_breeze_backend.app.services.options_strategy_engine.types import (
    EngineContext,
    QuoteRow,
    StrategyResult,
    TradeLeg,
)


def _quote(strike: int, right: str, *, bid: float, ask: float, spot: float = 23600.0) -> QuoteRow:
    return QuoteRow(
        strike=strike,
        right=right,
        ltp=(bid + ask) / 2,
        best_bid_price=bid,
        best_offer_price=ask,
        total_buy_qty=100,
        total_sell_qty=100,
        buy_sell_ratio=1.0,
        spot_price=spot,
        delta=0.2,
        liquidity_score=0.9,
        iv=0.15,
    )


def _ctx(
    *,
    min_pop_pct: float = 99.0,
    max_loss_rupees: float | None = 4_000_000,
    allow_infinite_loss: bool = False,
) -> EngineContext:
    atm = 23600
    cache = {
        (atm, "Call"): _quote(atm, "Call", bid=80.0, ask=81.0),
        (atm, "Put"): _quote(atm, "Put", bid=75.0, ask=76.0),
    }
    return EngineContext(
        processor=MagicMock(),
        user_id="u1",
        stock_code="NIFTY",
        exchange_code="NFO",
        expiry_display="16-Jun-2026",
        margin_rupees=50_000_000,
        max_loss_rupees=max_loss_rupees,
        allow_infinite_loss=allow_infinite_loss,
        min_pop_pct=min_pop_pct,
        provision_elm=False,
        strategy_category="income",
        lot_size=65,
        strikes=[atm],
        strike_step=50,
        search_interval=50,
        spot=23623.0,
        atm_strike=atm,
        atm_iv=0.15,
        min_ann_return_pct=5.0,
        cache=cache,
    )


class TestRelaxedShortStraddle(unittest.TestCase):
    def test_pop_near_miss_returns_relaxed_trade_with_legs(self):
        ctx = _ctx(min_pop_pct=99.0)
        result = calc_short_straddle(ctx)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.compliance, "relaxed")
        self.assertIn("pop_floor", result.constraint_violations)
        self.assertGreater(len(result.legs), 0)


class TestUndefinedRiskSegregation(unittest.TestCase):
    def test_undefined_risk_moves_to_relaxed_when_max_loss_set(self):
        ctx = _ctx(max_loss_rupees=2_000_000, allow_infinite_loss=False)
        ok = StrategyResult(
            "short_straddle",
            "Short Straddle",
            status="ok",
            legs=[TradeLeg("Call", "Sell", 23600, 65, 80.0)],
            max_loss=None,
            pop_pct=99.5,
        )
        recommended, relaxed = split_and_segregate_results([ok], ctx)
        self.assertEqual(len(recommended), 0)
        self.assertEqual(len(relaxed), 1)
        self.assertIn("infinite_loss", relaxed[0].constraint_violations)

    def test_undefined_risk_stays_recommended_with_infinite_loss_mode(self):
        ctx = _ctx(max_loss_rupees=None, allow_infinite_loss=True)
        ok = StrategyResult(
            "short_straddle",
            "Short Straddle",
            status="ok",
            legs=[TradeLeg("Call", "Sell", 23600, 65, 80.0)],
            max_loss=None,
            pop_pct=99.5,
        )
        recommended, relaxed = split_and_segregate_results([ok], ctx)
        self.assertEqual(len(recommended), 1)
        self.assertEqual(len(relaxed), 0)


class TestInfiniteLossSizing(unittest.TestCase):
    def test_defined_risk_uses_margin_only_when_no_cap(self):
        lots = size_lots(
            "iron_condor",
            100_000,
            50_000,
            margin_rupees=500_000,
            max_loss_rupees=None,
            lot_size=65,
            unit_legs=[],
            spot=23600,
            provision_elm=False,
        )
        self.assertEqual(lots, 5)


class TestRelaxedHelpers(unittest.TestCase):
    def test_income_constraint_violations_detects_pop_and_roi(self):
        ctx = _ctx(min_pop_pct=90.0)
        violations = income_constraint_violations(ctx, pop=85.0, ann_return=3.0)
        self.assertEqual(violations, ["pop_floor", "min_ann_return"])

    def test_mark_relaxed_result_sets_compliance(self):
        res = StrategyResult("iron_condor", "Iron Condor", status="ok")
        mark_relaxed_result(res, ["pop_floor"])
        self.assertEqual(res.compliance, "relaxed")
        self.assertEqual(res.constraint_violations, ["pop_floor"])


if __name__ == "__main__":
    unittest.main()
