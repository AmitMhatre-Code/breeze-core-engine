"""Engine-level portfolio-aware (incremental) margin sizing -- Phase 3.

See docs/strategy-builder-portfolio-margin-plan.md (D1-D10). Covers the
secant sizing math, resize_results_to_budgets with netting active, and
attach_margins_and_returns's final netted fetch + shrink check + D8 ranking.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import MagicMock

from icici_breeze_backend.app.services.options_strategy_engine.budget_resize import (
    _secant_lots_for_budget,
    resize_results_to_budgets,
)
from icici_breeze_backend.app.services.options_strategy_engine.orchestrator import (
    attach_margins_and_returns,
)
from icici_breeze_backend.app.services.options_strategy_engine.types import (
    EngineContext,
    StrategyResult,
    TradeLeg,
)
from icici_breeze_backend.app.services.portfolio_margin_netting import PositionSet


def _existing_row(strike="24000", qty="50"):
    return {
        "stock_code": "NIFTY",
        "exchange_code": "NFO",
        "expiry_date": "16-Jun-2026T06:00:00.000Z",
        "product_type": "Options",
        "right": "Put",
        "strike_price": strike,
        "quantity": qty,
        "action": "Sell",
    }


def _ctx(
    *,
    margin_rupees: float = 500_000.0,
    max_loss_rupees: float | None = None,
    netting_available: bool = True,
    existing_span: float | None = 90_000.0,
) -> EngineContext:
    position_set = PositionSet(
        rows=[_existing_row()],
        fingerprint="fp1",
        expiries=["16-Jun-2026T06:00:00.000Z"],
        available=True,
    )
    return EngineContext(
        processor=MagicMock(),
        user_id="u1",
        stock_code="NIFTY",
        exchange_code="NFO",
        expiry_display="16-Jun-2026",
        margin_rupees=margin_rupees,
        max_loss_rupees=max_loss_rupees,
        min_pop_pct=95.0,
        provision_elm=False,
        strategy_category="income",
        lot_size=65,
        strikes=[23600],
        strike_step=50,
        search_interval=50,
        spot=23622.9,
        atm_strike=23600,
        is_index=True,
        positions=position_set,
        netting_legs=[_existing_row()],
        existing_span=existing_span,
        netting_available=netting_available,
    )


class TestSecantSolver(unittest.TestCase):
    def test_normal_case_solves_between_anchors(self):
        # incr(1)=1000, incr(10)=10000 -> linear at rate 1000/lot, no ELM.
        # budget=5500 -> N ~= 5.5 -> floor 5.
        n = _secant_lots_for_budget(1, 1000.0, 10, 10000.0, 0.0, 5500.0)
        self.assertEqual(n, 5)

    def test_unbounded_when_margin_never_binds(self):
        # incr constant (fully saturated hedge) and no ELM -> slope=0, denom<=0.
        n = _secant_lots_for_budget(1, -5000.0, 10, -5000.0, 0.0, 100_000.0)
        self.assertEqual(n, float("inf"))

    def test_none_on_monotonicity_violation(self):
        # incr DECREASING from n1 to n2 -- violates the documented invariant.
        n = _secant_lots_for_budget(1, 5000.0, 10, 1000.0, 0.0, 50_000.0)
        self.assertIsNone(n)

    def test_clamped_to_anchor_window(self):
        # Budget far exceeds what even the anchor point would need -> clamp to n2.
        n = _secant_lots_for_budget(1, 100.0, 5, 500.0, 0.0, 10_000_000.0)
        self.assertEqual(n, 5.0)

    def test_same_anchor_returns_that_lot_count(self):
        n = _secant_lots_for_budget(3, 1000.0, 3, 1000.0, 0.0, 50_000.0)
        self.assertEqual(n, 3)


class TestResizeResultsToBudgetsNetting(unittest.TestCase):
    def _short_put_result(self):
        return StrategyResult(
            "naked_pe_short",
            "Naked PE Short",
            status="ok",
            max_loss=None,
            net_premium=3.35 * 65,
            legs=[TradeLeg("Put", "Sell", 24000, 65, 3.35)],
        )

    def test_netting_inactive_matches_pre_netting_linear_sizing(self):
        """ctx.netting_available False -> exact pre-netting behaviour."""
        ctx = _ctx(netting_available=False, margin_rupees=500_000.0)
        result = self._short_put_result()
        proc = MagicMock()
        proc.strategy_builder_margin.return_value = {
            "Status": 200,
            "Success": {"span_margin_required": 40_000.0},
        }
        asyncio.run(
            resize_results_to_budgets(
                proc, "u1", "NFO", "NIFTY", "16-Jun-2026", [result], ctx
            )
        )
        self.assertEqual(result.status, "ok")
        self.assertFalse(result.netted_against_positions)
        # 500000 // 40000 = 12 lots, exactly the pre-netting formula.
        self.assertEqual(result.legs[0].quantity, 65 * 12)
        # Confirms no netted probe call was ever made (only standalone calls).
        for call in proc.strategy_builder_margin.call_args_list:
            self.assertNotIn("existing_legs", call.kwargs)

    def test_netting_active_no_overlap_short_circuits_to_linear(self):
        """A structure whose netted probe barely differs from standalone (< 2%)
        takes the exact pre-netting path -- no secant, no extra live-fetched
        anchor call beyond the one shared netted one-lot probe."""
        ctx = _ctx(netting_available=True, margin_rupees=500_000.0)
        result = self._short_put_result()
        proc = MagicMock()

        def _side_effect(user_id, exchange_code, margin_input, **kwargs):
            if kwargs.get("existing_legs"):
                # Netted call barely differs from standalone (no real overlap).
                return {"Status": 200, "Success": {"span_margin_required": 40_100.0}}
            return {"Status": 200, "Success": {"span_margin_required": 40_000.0}}

        proc.strategy_builder_margin.side_effect = _side_effect
        asyncio.run(
            resize_results_to_budgets(
                proc, "u1", "NFO", "NIFTY", "16-Jun-2026", [result], ctx
            )
        )
        self.assertEqual(result.status, "ok")
        self.assertFalse(result.netted_against_positions)
        self.assertEqual(result.legs[0].quantity, 65 * 12)

    def test_netting_active_overlap_sizes_more_lots_than_standalone(self):
        """A structure that meaningfully offsets the existing short should be
        sized MORE generously than the standalone linear formula would allow --
        the whole point of D3 (size on incremental margin)."""
        ctx = _ctx(netting_available=True, margin_rupees=500_000.0)
        result = self._short_put_result()
        proc = MagicMock()

        # Standalone one-lot = 40000. Netted: heavily offset up to ~8 lots,
        # then grows at the standalone rate beyond that (saturating curve).
        def _side_effect(user_id, exchange_code, margin_input, **kwargs):
            qty = int(margin_input[0]["quantity"])
            lots = qty // 65
            if kwargs.get("existing_legs"):
                if lots <= 8:
                    incr = 2_000.0 * lots  # heavily offset: 2000/lot up to 8 lots
                else:
                    incr = 16_000.0 + 40_000.0 * (lots - 8)  # standalone rate beyond
                return {"Status": 200, "Success": {"span_margin_required": incr}}
            return {"Status": 200, "Success": {"span_margin_required": 40_000.0 * lots}}

        proc.strategy_builder_margin.side_effect = _side_effect
        asyncio.run(
            resize_results_to_budgets(
                proc, "u1", "NFO", "NIFTY", "16-Jun-2026", [result], ctx
            )
        )
        self.assertEqual(result.status, "ok")
        self.assertTrue(result.netted_against_positions)
        netted_lots = result.legs[0].quantity // 65
        # Standalone-only sizing would cap at 500000 // 40000 = 12 lots.
        self.assertGreater(netted_lots, 12)

    def test_positions_fetch_unavailable_falls_back_to_standalone(self):
        """D7: netting_available False (positions fetch failed) behaves
        exactly like no netting at all -- never sizes against an unverified
        offset."""
        ctx = _ctx(netting_available=False, existing_span=None, margin_rupees=500_000.0)
        ctx.netting_legs = []
        result = self._short_put_result()
        proc = MagicMock()
        proc.strategy_builder_margin.return_value = {
            "Status": 200,
            "Success": {"span_margin_required": 40_000.0},
        }
        asyncio.run(
            resize_results_to_budgets(
                proc, "u1", "NFO", "NIFTY", "16-Jun-2026", [result], ctx
            )
        )
        self.assertFalse(result.netted_against_positions)
        self.assertEqual(result.legs[0].quantity, 65 * 12)


class TestAttachMarginsAndReturnsNetting(unittest.TestCase):
    def _priced_result(self, quantity=65 * 12):
        r = StrategyResult(
            "naked_pe_short",
            "Naked PE Short",
            status="ok",
            net_premium=3.35 * quantity,
            legs=[TradeLeg("Put", "Sell", 24000, quantity, 3.35)],
        )
        r.netted_against_positions = True
        return r

    def test_negative_incremental_stays_negative_not_nulled(self):
        """D8: span_margin must NOT be nulled when incremental <= 0 -- the
        pre-netting `span if span > 0 else None` gate must not apply to a
        netted, successfully-verified result."""
        ctx = _ctx(margin_rupees=500_000.0)
        ctx.unit_span_by_structure[(("Put", "Sell", 24000),)] = 40_000.0
        result = self._priced_result()
        proc = MagicMock()
        proc.strategy_builder_margin.return_value = {
            "Status": 200,
            "Success": {"span_margin_required": -15_000.0},
        }
        asyncio.run(
            attach_margins_and_returns(
                proc, "u1", "NFO", "NIFTY", "16-Jun-2026", [result], ctx
            )
        )
        self.assertEqual(result.span_margin, -15_000.0)
        self.assertTrue(result.margin_released)
        self.assertIsNone(result.annualized_return_pct)
        self.assertIsNotNone(result.standalone_span_margin)
        self.assertGreater(result.positions_margin_benefit, 0)

    def test_positive_incremental_computes_return_and_benefit(self):
        ctx = _ctx(margin_rupees=500_000.0)
        ctx.unit_span_by_structure[(("Put", "Sell", 24000),)] = 40_000.0
        result = self._priced_result(quantity=65 * 5)
        proc = MagicMock()
        proc.strategy_builder_margin.return_value = {
            "Status": 200,
            "Success": {"span_margin_required": 100_000.0},
        }
        asyncio.run(
            attach_margins_and_returns(
                proc, "u1", "NFO", "NIFTY", "16-Jun-2026", [result], ctx
            )
        )
        self.assertEqual(result.span_margin, 100_000.0)
        self.assertFalse(result.margin_released)
        self.assertIsNotNone(result.annualized_return_pct)
        # standalone = 5 lots * 40000 = 200000; benefit = 200000-100000=100000.
        self.assertEqual(result.standalone_span_margin, 200_000.0)
        self.assertEqual(result.positions_margin_benefit, 100_000.0)

    def test_shrink_check_reduces_lots_when_final_netted_call_exceeds_budget(self):
        """D3/§7.5: the secant is an approximation; the FINAL full-quantity
        netted fetch is authoritative. If it exceeds budget, shrink exactly
        once and re-fetch -- never crash, never leave an over-budget result."""
        ctx = _ctx(margin_rupees=100_000.0)
        ctx.unit_span_by_structure[(("Put", "Sell", 24000),)] = 40_000.0
        result = self._priced_result(quantity=65 * 10)  # secant over-estimated 10 lots
        proc = MagicMock()

        calls = []

        def _side_effect(user_id, exchange_code, margin_input, **kwargs):
            qty = int(margin_input[0]["quantity"])
            lots = qty // 65
            calls.append(lots)
            # True incremental grows faster than the secant assumed: 15000/lot.
            return {"Status": 200, "Success": {"span_margin_required": 15_000.0 * lots}}

        proc.strategy_builder_margin.side_effect = _side_effect
        asyncio.run(
            attach_margins_and_returns(
                proc, "u1", "NFO", "NIFTY", "16-Jun-2026", [result], ctx
            )
        )
        self.assertEqual(result.status, "ok")
        # Shrunk from 10 lots: 100000 // 15000 = 6 (int(10 * 100000/150000)=6).
        self.assertEqual(result.legs[0].quantity, 65 * 6)
        self.assertLessEqual(result.span_margin, ctx.margin_rupees)
        self.assertEqual(len(calls), 2)  # exactly one shrink + refetch, not a loop

    def test_shrink_to_one_lot_still_over_budget_skips_result(self):
        ctx = _ctx(margin_rupees=1_000.0)
        ctx.unit_span_by_structure[(("Put", "Sell", 24000),)] = 40_000.0
        result = self._priced_result(quantity=65 * 5)
        proc = MagicMock()
        proc.strategy_builder_margin.return_value = {
            "Status": 200,
            "Success": {"span_margin_required": 200_000.0},
        }
        asyncio.run(
            attach_margins_and_returns(
                proc, "u1", "NFO", "NIFTY", "16-Jun-2026", [result], ctx
            )
        )
        self.assertEqual(result.status, "skipped")
        self.assertEqual(result.legs, [])

    def test_failed_netted_fetch_never_shows_unverified_figure(self):
        """D7 at single-structure granularity: a failed final netted call must
        not surface a margin figure the system never verified."""
        ctx = _ctx(margin_rupees=500_000.0)
        result = self._priced_result()
        proc = MagicMock()
        proc.strategy_builder_margin.return_value = {
            "Status": 400,
            "Error": "rate limited",
            "Success": None,
        }
        asyncio.run(
            attach_margins_and_returns(
                proc, "u1", "NFO", "NIFTY", "16-Jun-2026", [result], ctx
            )
        )
        self.assertIsNone(result.span_margin)
        self.assertFalse(result.netted_against_positions)


if __name__ == "__main__":
    unittest.main()
