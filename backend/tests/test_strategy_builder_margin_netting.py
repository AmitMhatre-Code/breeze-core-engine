"""Portfolio-aware (incremental) margin netting inside strategy_builder_margin.

See docs/strategy-builder-portfolio-margin-plan.md (D1-D10). Phase 2: the
method supports netting via `existing_legs` / `existing_span_value`, but no
caller wires it in yet -- these tests exercise the method directly and assert
the no-netting default path is byte-identical to before.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from icici_breeze_backend.app.services.processor import processor


def _candidate_legs(expiry="09-Jun-2099"):
    return [
        {
            "stock_code": "NIFTY",
            "exchange_code": "NFO",
            "expiry_date": expiry,
            "product_type": "Options",
            "right": "Call",
            "strike_price": "23500",
            "quantity": "75",
            "action": "Sell",
        }
    ]


def _existing_leg_row(strike="24000", expiry="09-Jun-2099", qty="50"):
    return {
        "stock_code": "NIFTY",
        "exchange_code": "NFO",
        "expiry_date": expiry,
        "product_type": "Options",
        "right": "Put",
        "strike_price": strike,
        "quantity": qty,
        "action": "Sell",
    }


class TestBreezeApiNettingDefaultOff(unittest.TestCase):
    """No existing_legs -> byte-identical to the pre-netting implementation."""

    def test_no_existing_legs_makes_exactly_one_margin_call(self):
        proc = processor()
        mock_breeze = MagicMock()
        mock_breeze.margin_calculator.return_value = {
            "Status": 200,
            "Success": {"span_margin_required": 40_000.0},
        }
        with patch.object(proc, "get_session_breeze", return_value=mock_breeze), patch.object(
            proc, "get_strategy_builder_margin_source", return_value="breeze_api"
        ):
            res = proc.strategy_builder_margin("u1", "NFO", _candidate_legs())

        self.assertEqual(res["Status"], 200)
        self.assertEqual(res["Success"]["span_margin_required"], 40_000.0)
        self.assertNotIn("standalone_span_margin", res["Success"])
        self.assertNotIn("netted_against_positions", res["Success"])
        mock_breeze.margin_calculator.assert_called_once()

    def test_empty_existing_legs_list_also_skips_netting(self):
        proc = processor()
        mock_breeze = MagicMock()
        mock_breeze.margin_calculator.return_value = {
            "Status": 200,
            "Success": {"span_margin_required": 40_000.0},
        }
        with patch.object(proc, "get_session_breeze", return_value=mock_breeze), patch.object(
            proc, "get_strategy_builder_margin_source", return_value="breeze_api"
        ):
            res = proc.strategy_builder_margin(
                "u1", "NFO", _candidate_legs(), existing_legs=[], existing_span_value=0.0
            )

        self.assertNotIn("netted_against_positions", res["Success"])
        mock_breeze.margin_calculator.assert_called_once()


class TestBreezeApiNetting(unittest.TestCase):
    def _mock_breeze_two_calls(self, standalone: float, combined: float):
        mock_breeze = MagicMock()

        def _side_effect(margin_list, exchange_code="", **kwargs):
            if len(margin_list) == 1:
                return {"Status": 200, "Success": {"span_margin_required": standalone}}
            return {"Status": 200, "Success": {"span_margin_required": combined}}

        mock_breeze.margin_calculator.side_effect = _side_effect
        return mock_breeze

    def test_incremental_and_benefit_computed_from_two_calls(self):
        proc = processor()
        mock_breeze = self._mock_breeze_two_calls(standalone=40_000.0, combined=55_000.0)
        existing_rows = [_existing_leg_row()]

        with patch.object(proc, "get_session_breeze", return_value=mock_breeze), patch.object(
            proc, "get_strategy_builder_margin_source", return_value="breeze_api"
        ):
            res = proc.strategy_builder_margin(
                "u1",
                "NFO",
                _candidate_legs(),
                existing_legs=existing_rows,
                existing_span_value=30_000.0,
            )

        success = res["Success"]
        # incremental = combined(55000) - existing(30000) = 25000
        self.assertEqual(success["span_margin_required"], 25_000.0)
        self.assertEqual(success["standalone_span_margin"], 40_000.0)
        self.assertEqual(success["existing_span_margin"], 30_000.0)
        self.assertEqual(success["combined_span_margin"], 55_000.0)
        # benefit = standalone(40000) - incremental(25000) = 15000
        self.assertEqual(success["positions_margin_benefit"], 15_000.0)
        self.assertTrue(success["netted_against_positions"])
        self.assertEqual(success["netted_position_count"], 1)
        self.assertEqual(mock_breeze.margin_calculator.call_count, 2)

    def test_negative_incremental_when_structure_caps_existing_short(self):
        """A hedging structure can release more margin than it costs standalone --
        incremental must be allowed to go negative, never floored (D8)."""
        proc = processor()
        # existing=90000 (large naked short), combined=20000 (capped by the hedge).
        mock_breeze = self._mock_breeze_two_calls(standalone=15_000.0, combined=20_000.0)
        existing_rows = [_existing_leg_row()]

        with patch.object(proc, "get_session_breeze", return_value=mock_breeze), patch.object(
            proc, "get_strategy_builder_margin_source", return_value="breeze_api"
        ):
            res = proc.strategy_builder_margin(
                "u1",
                "NFO",
                _candidate_legs(),
                existing_legs=existing_rows,
                existing_span_value=90_000.0,
            )

        success = res["Success"]
        self.assertEqual(success["span_margin_required"], 20_000.0 - 90_000.0)
        self.assertLess(success["span_margin_required"], 0)
        # benefit = standalone(15000) - incremental(-70000) = 85000, unfloored input
        # but the max(0, ...) floor never engages here since it's already positive.
        self.assertEqual(success["positions_margin_benefit"], 85_000.0)

    def test_netting_position_count_defaults_to_existing_legs_length(self):
        proc = processor()
        mock_breeze = self._mock_breeze_two_calls(standalone=40_000.0, combined=55_000.0)
        existing_rows = [_existing_leg_row(strike="24000"), _existing_leg_row(strike="24500")]

        with patch.object(proc, "get_session_breeze", return_value=mock_breeze), patch.object(
            proc, "get_strategy_builder_margin_source", return_value="breeze_api"
        ):
            res = proc.strategy_builder_margin(
                "u1",
                "NFO",
                _candidate_legs(),
                existing_legs=existing_rows,
                existing_span_value=30_000.0,
            )

        self.assertEqual(res["Success"]["netted_position_count"], 2)

    def test_combined_call_failure_falls_back_to_standalone_only(self):
        """D7 at the single-call granularity: if the netted call fails, the
        response must stay exactly the standalone figures -- never partially
        netted or crashed."""
        proc = processor()
        mock_breeze = MagicMock()

        def _side_effect(margin_list, exchange_code="", **kwargs):
            if len(margin_list) == 1:
                return {"Status": 200, "Success": {"span_margin_required": 40_000.0}}
            raise RuntimeError("rate limited")

        mock_breeze.margin_calculator.side_effect = _side_effect
        existing_rows = [_existing_leg_row()]

        with patch.object(proc, "get_session_breeze", return_value=mock_breeze), patch.object(
            proc, "get_strategy_builder_margin_source", return_value="breeze_api"
        ):
            res = proc.strategy_builder_margin(
                "u1",
                "NFO",
                _candidate_legs(),
                existing_legs=existing_rows,
                existing_span_value=30_000.0,
            )

        success = res["Success"]
        self.assertEqual(success["span_margin_required"], 40_000.0)
        self.assertNotIn("netted_against_positions", success)
        self.assertNotIn("standalone_span_margin", success)

    def test_netting_skipped_when_existing_span_value_is_none(self):
        """existing_span_value=None signals D7 (positions netting unavailable) --
        must behave exactly like existing_legs=None."""
        proc = processor()
        mock_breeze = MagicMock()
        mock_breeze.margin_calculator.return_value = {
            "Status": 200,
            "Success": {"span_margin_required": 40_000.0},
        }
        existing_rows = [_existing_leg_row()]

        with patch.object(proc, "get_session_breeze", return_value=mock_breeze), patch.object(
            proc, "get_strategy_builder_margin_source", return_value="breeze_api"
        ):
            res = proc.strategy_builder_margin(
                "u1",
                "NFO",
                _candidate_legs(),
                existing_legs=existing_rows,
                existing_span_value=None,
            )

        self.assertNotIn("netted_against_positions", res["Success"])
        mock_breeze.margin_calculator.assert_called_once()

    def test_netting_unavailable_reason_echoed_through(self):
        proc = processor()
        mock_breeze = MagicMock()
        mock_breeze.margin_calculator.return_value = {
            "Status": 200,
            "Success": {"span_margin_required": 40_000.0},
        }
        with patch.object(proc, "get_session_breeze", return_value=mock_breeze), patch.object(
            proc, "get_strategy_builder_margin_source", return_value="breeze_api"
        ):
            res = proc.strategy_builder_margin(
                "u1",
                "NFO",
                _candidate_legs(),
                netting_unavailable_reason="Unable to load open positions.",
            )

        self.assertEqual(
            res["Success"]["netting_unavailable_reason"], "Unable to load open positions."
        )


class TestExchangeBaselineNetting(unittest.TestCase):
    """D6: baseline path nets same-expiry positions only, stays fully offline,
    and warns about positions in other expiries rather than dropping them
    silently."""

    def _fake_baseline_span(self, *, candidate=40_000.0, existing=30_000.0, combined=55_000.0):
        def _fake(exchange_code, legs, *, spot=None, iv=None, time_years=None):
            strikes = sorted(str(l.get("strike_price")) for l in legs)
            if strikes == ["23500"]:
                return {
                    "found": True,
                    "span_margin_required": candidate,
                    "scanning_risk": candidate,
                    "net_option_value": 0.0,
                    "margin_benefit": 500.0,
                }
            if strikes == ["24000"]:
                return {
                    "found": True,
                    "span_margin_required": existing,
                    "scanning_risk": existing,
                    "net_option_value": 0.0,
                    "margin_benefit": None,
                }
            if strikes == ["23500", "24000"]:
                return {
                    "found": True,
                    "span_margin_required": combined,
                    "scanning_risk": combined,
                    "net_option_value": 0.0,
                    "margin_benefit": None,
                }
            return {"found": False}

        return _fake

    def test_same_expiry_position_is_netted_offline(self):
        proc = processor()
        existing_rows = [_existing_leg_row(strike="24000", expiry="09-Jun-2099")]
        mock_breeze = MagicMock()

        with patch.object(proc, "get_session_breeze", return_value=mock_breeze), patch.object(
            proc, "get_strategy_builder_margin_source", return_value="exchange_baseline"
        ), patch(
            "icici_breeze_backend.app.services.processor.resolve_exchange_baseline_margin",
            return_value={"found": True, "span_margin_required": 1_000.0},
        ), patch.object(
            proc, "_portfolio_baseline_span_margin", side_effect=self._fake_baseline_span()
        ) as fake_portfolio:
            res = proc.strategy_builder_margin(
                "u1",
                "NFO",
                _candidate_legs(expiry="09-Jun-2099"),
                existing_legs=existing_rows,
            )

        success = res["Success"]
        self.assertEqual(success["span_margin_required"], 25_000.0)  # 55000 - 30000
        self.assertEqual(success["standalone_span_margin"], 40_000.0)
        self.assertEqual(success["existing_span_margin"], 30_000.0)
        self.assertEqual(success["combined_span_margin"], 55_000.0)
        self.assertEqual(success["positions_margin_benefit"], 15_000.0)
        self.assertTrue(success["netted_against_positions"])
        self.assertEqual(success["netted_position_count"], 1)
        # Intra-structure benefit (from the candidate-alone baseline call) survives untouched.
        self.assertEqual(success["margin_benefit"], 500.0)
        # Fully offline: no live margin_calculator call anywhere in this path.
        mock_breeze.margin_calculator.assert_not_called()
        self.assertEqual(fake_portfolio.call_count, 3)  # candidate, existing-alone, combined

    def test_other_expiry_position_is_not_netted_but_warned(self):
        proc = processor()
        existing_rows = [_existing_leg_row(strike="24000", expiry="18-Sep-2099")]

        with patch.object(proc, "get_session_breeze", return_value=MagicMock()), patch.object(
            proc, "get_strategy_builder_margin_source", return_value="exchange_baseline"
        ), patch(
            "icici_breeze_backend.app.services.processor.resolve_exchange_baseline_margin",
            return_value={"found": True, "span_margin_required": 1_000.0},
        ), patch.object(
            proc, "_portfolio_baseline_span_margin", side_effect=self._fake_baseline_span()
        ):
            res = proc.strategy_builder_margin(
                "u1",
                "NFO",
                _candidate_legs(expiry="09-Jun-2099"),
                existing_legs=existing_rows,
            )

        success = res["Success"]
        # Standalone figure unchanged -- nothing was netted.
        self.assertEqual(success["span_margin_required"], 40_000.0)
        self.assertNotIn("netted_against_positions", success)
        warnings = success.get("warnings") or []
        types = [w.get("type") for w in warnings if isinstance(w, dict)]
        self.assertIn("positions_not_netted_other_expiry", types)


if __name__ == "__main__":
    unittest.main()
