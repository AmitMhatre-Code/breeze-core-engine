"""Portfolio-aware (incremental) margin netting for the covered/uncovered
shorts scan -- Phase 5.2. See docs/strategy-builder-portfolio-margin-plan.md
(D1-D10).

_resolve_leg_margin_with_source is the isolated unit carrying the actual
netting math (mirrors the two-call standalone+combined pattern from Phase 2's
strategy_builder_margin, applied to a single candidate leg). uncovered_shorts
resolves the position set once and threads it through both the CE and PE
get_options() scans -- that wiring is tested by mocking get_options itself,
since fully simulating an option chain is unrelated to what's being tested.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from icici_breeze_backend.app.services.nsccl_baseline import (
    MARGIN_SOURCE_BREEZE,
    MARGIN_SOURCE_EXCHANGE,
)
from icici_breeze_backend.app.services.processor import processor


class TestResolveLegMarginWithSourceNetting(unittest.TestCase):
    def _mock_breeze_two_calls(self, standalone: float, combined: float):
        mock_breeze = MagicMock()

        def _side_effect(margin_list, exchange_code="", **kwargs):
            if len(margin_list) == 1:
                return {"Status": 200, "Success": {"span_margin_required": standalone}}
            return {"Status": 200, "Success": {"span_margin_required": combined}}

        mock_breeze.margin_calculator.side_effect = _side_effect
        return mock_breeze

    def test_no_existing_legs_is_byte_identical_to_before(self):
        proc = processor()
        mock_breeze = MagicMock()
        mock_breeze.margin_calculator.return_value = {
            "Status": 200,
            "Success": {"span_margin_required": 40_000.0},
        }
        with patch.object(proc, "get_session_breeze", return_value=mock_breeze):
            out, warnings = proc._resolve_leg_margin_with_source(
                user_id="u1",
                exchange_code="NFO",
                stock_code="NIFTY",
                expiry_display="09-Jun-2099",
                strike_price=23500,
                right="Call",
                quantity=75,
                margin_source=MARGIN_SOURCE_BREEZE,
            )
        self.assertEqual(out["Success"]["span_margin_required"], 40_000.0)
        self.assertNotIn("netted_against_positions", out["Success"])
        mock_breeze.margin_calculator.assert_called_once()
        self.assertEqual(warnings, [])

    def test_netting_computes_incremental_from_two_calls(self):
        proc = processor()
        mock_breeze = self._mock_breeze_two_calls(standalone=40_000.0, combined=55_000.0)
        existing_rows = [
            {
                "stock_code": "NIFTY",
                "exchange_code": "NFO",
                "expiry_date": "09-Jun-2099T06:00:00.000Z",
                "product_type": "Options",
                "right": "Put",
                "strike_price": "24000",
                "quantity": "50",
                "action": "Sell",
            }
        ]
        with patch.object(proc, "get_session_breeze", return_value=mock_breeze):
            out, _warnings = proc._resolve_leg_margin_with_source(
                user_id="u1",
                exchange_code="NFO",
                stock_code="NIFTY",
                expiry_display="09-Jun-2099",
                strike_price=23500,
                right="Call",
                quantity=75,
                margin_source=MARGIN_SOURCE_BREEZE,
                existing_legs=existing_rows,
                existing_span_value=30_000.0,
                netting_position_count=1,
            )
        success = out["Success"]
        self.assertEqual(success["span_margin_required"], 25_000.0)  # 55000-30000
        self.assertEqual(success["standalone_span_margin"], 40_000.0)
        self.assertEqual(success["positions_margin_benefit"], 15_000.0)
        self.assertTrue(success["netted_against_positions"])
        self.assertEqual(success["netted_position_count"], 1)
        self.assertEqual(mock_breeze.margin_calculator.call_count, 2)

    def test_netted_incremental_can_be_negative_not_floored(self):
        proc = processor()
        mock_breeze = self._mock_breeze_two_calls(standalone=15_000.0, combined=20_000.0)
        existing_rows = [{"stock_code": "NIFTY", "quantity": "50"}]
        with patch.object(proc, "get_session_breeze", return_value=mock_breeze):
            out, _ = proc._resolve_leg_margin_with_source(
                user_id="u1",
                exchange_code="NFO",
                stock_code="NIFTY",
                expiry_display="09-Jun-2099",
                strike_price=23500,
                right="Call",
                quantity=75,
                margin_source=MARGIN_SOURCE_BREEZE,
                existing_legs=existing_rows,
                existing_span_value=90_000.0,
            )
        self.assertEqual(out["Success"]["span_margin_required"], 20_000.0 - 90_000.0)
        self.assertLess(out["Success"]["span_margin_required"], 0)

    def test_combined_call_failure_falls_back_to_standalone(self):
        proc = processor()
        mock_breeze = MagicMock()

        def _side_effect(margin_list, exchange_code="", **kwargs):
            if len(margin_list) == 1:
                return {"Status": 200, "Success": {"span_margin_required": 40_000.0}}
            raise RuntimeError("rate limited")

        mock_breeze.margin_calculator.side_effect = _side_effect
        existing_rows = [{"stock_code": "NIFTY", "quantity": "50"}]
        with patch.object(proc, "get_session_breeze", return_value=mock_breeze):
            out, _ = proc._resolve_leg_margin_with_source(
                user_id="u1",
                exchange_code="NFO",
                stock_code="NIFTY",
                expiry_display="09-Jun-2099",
                strike_price=23500,
                right="Call",
                quantity=75,
                margin_source=MARGIN_SOURCE_BREEZE,
                existing_legs=existing_rows,
                existing_span_value=30_000.0,
            )
        self.assertEqual(out["Success"]["span_margin_required"], 40_000.0)
        self.assertNotIn("netted_against_positions", out["Success"])

    def test_baseline_found_never_attempts_netting(self):
        """D6-adjacent: when the exchange baseline resolves this contract, the
        function returns before ever reaching the live/netting path."""
        proc = processor()
        mock_breeze = MagicMock()
        existing_rows = [{"stock_code": "NIFTY", "quantity": "50"}]
        with patch.object(proc, "get_session_breeze", return_value=mock_breeze), patch(
            "icici_breeze_backend.app.services.processor.resolve_exchange_baseline_margin",
            return_value={"found": True, "span_margin_required": 5_000.0},
        ):
            out, warnings = proc._resolve_leg_margin_with_source(
                user_id="u1",
                exchange_code="NFO",
                stock_code="NIFTY",
                expiry_display="09-Jun-2099",
                strike_price=23500,
                right="Call",
                quantity=75,
                margin_source=MARGIN_SOURCE_EXCHANGE,
                existing_legs=existing_rows,
                existing_span_value=30_000.0,
            )
        self.assertEqual(out["Success"]["span_margin_required"], 5_000.0)
        mock_breeze.margin_calculator.assert_not_called()
        self.assertEqual(warnings, [])


class TestUncoveredShortsNettingWiring(unittest.TestCase):
    def test_breeze_api_source_resolves_positions_once_for_both_scans(self):
        proc = processor()
        position_rows = [
            {
                "stock_code": "NIFTY",
                "exchange_code": "NFO",
                "expiry_date": "09-Jun-2099T06:00:00.000Z",
                "product_type": "Options",
                "right": "Put",
                "strike_price": "24000",
                "quantity": "50",
                "action": "Sell",
            }
        ]
        mock_breeze = MagicMock()
        with patch.object(proc, "fetch_lot_size", return_value=75), patch.object(
            proc, "get_positions", return_value={"Status": 200, "Success": position_rows, "Error": None}
        ), patch.object(proc, "get_session_breeze", return_value=mock_breeze), patch.object(
            proc, "_netted_span_for_legs", return_value=30_000.0
        ) as fake_existing_span, patch.object(
            proc, "get_options", return_value={"Status": 200, "Success": []}
        ) as fake_get_options:
            proc.uncovered_shorts(
                "u1",
                stock_code="NIFTY",
                expiry_date="09-Jun-2099",
                limits=5,
                exchange_code="NFO",
                margin_source=MARGIN_SOURCE_BREEZE,
            )

        fake_existing_span.assert_called_once()
        self.assertEqual(fake_get_options.call_count, 2)
        for call in fake_get_options.call_args_list:
            self.assertEqual(len(call.kwargs["existing_legs"]), 1)
            self.assertEqual(call.kwargs["existing_span_value"], 30_000.0)
            self.assertEqual(call.kwargs["netting_position_count"], 1)

    def test_exchange_baseline_source_never_nets(self):
        """D6-adjacent scope decision for this surface: only breeze_api is
        netted (see _resolve_leg_margin_with_source's docstring)."""
        proc = processor()
        with patch.object(proc, "fetch_lot_size", return_value=75), patch.object(
            proc, "get_positions"
        ) as fake_get_positions, patch.object(
            proc, "get_options", return_value={"Status": 200, "Success": []}
        ) as fake_get_options:
            proc.uncovered_shorts(
                "u1",
                stock_code="NIFTY",
                expiry_date="09-Jun-2099",
                limits=5,
                exchange_code="NFO",
                margin_source=MARGIN_SOURCE_EXCHANGE,
            )

        fake_get_positions.assert_not_called()
        for call in fake_get_options.call_args_list:
            self.assertIsNone(call.kwargs["existing_legs"])
            self.assertIsNone(call.kwargs["existing_span_value"])
            self.assertEqual(call.kwargs["netting_position_count"], 0)

    def test_no_open_positions_nets_nothing_no_error(self):
        proc = processor()
        with patch.object(proc, "fetch_lot_size", return_value=75), patch.object(
            proc, "get_positions", return_value={"Status": 200, "Success": [], "Error": None}
        ), patch.object(
            proc, "get_options", return_value={"Status": 200, "Success": []}
        ) as fake_get_options:
            proc.uncovered_shorts(
                "u1",
                stock_code="NIFTY",
                expiry_date="09-Jun-2099",
                limits=5,
                exchange_code="NFO",
                margin_source=MARGIN_SOURCE_BREEZE,
            )

        for call in fake_get_options.call_args_list:
            self.assertIsNone(call.kwargs["existing_legs"])

    def test_positions_fetch_failure_falls_back_to_no_netting(self):
        proc = processor()
        with patch.object(proc, "fetch_lot_size", return_value=75), patch.object(
            proc,
            "get_positions",
            return_value={"Status": 400, "Error": "Unable to connect to broker.", "Success": None},
        ), patch.object(
            proc, "get_options", return_value={"Status": 200, "Success": []}
        ) as fake_get_options:
            proc.uncovered_shorts(
                "u1",
                stock_code="NIFTY",
                expiry_date="09-Jun-2099",
                limits=5,
                exchange_code="NFO",
                margin_source=MARGIN_SOURCE_BREEZE,
            )

        for call in fake_get_options.call_args_list:
            self.assertIsNone(call.kwargs["existing_legs"])


if __name__ == "__main__":
    unittest.main()
