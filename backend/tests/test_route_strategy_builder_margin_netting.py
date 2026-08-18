"""Build-Your-Own margin route wiring for portfolio-aware netting (Phase 5.1).

See docs/strategy-builder-portfolio-margin-plan.md (D1-D10). Calls the FastAPI
handler directly (it's a plain async function) rather than standing up a full
TestClient -- the netting math itself is already covered by
test_strategy_builder_margin_netting.py; these tests are about the route
correctly resolving positions and threading them into strategy_builder_margin.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import MagicMock, patch

from icici_breeze_backend.app.api.v1 import route_strategy_builder as rsb
from icici_breeze_backend.app.auth.context import RequestContext
from icici_breeze_backend.app.domain.strategy_builder import (
    StrategyBuilderLegIn,
    StrategyBuilderMarginRequest,
)


def _ctx(user_id="u1"):
    return RequestContext(
        user_id=user_id,
        username="u1",
        roles=[],
        is_authenticated=True,
        broker_token="tok",
    )


def _leg():
    return StrategyBuilderLegIn(
        stock_code="NIFTY",
        exchange_code="NFO",
        expiry_date="09-Jun-2099",
        product_type="Options",
        right="Call",
        strike_price="23500",
        quantity="75",
        action="Sell",
    )


def _run(coro):
    return asyncio.run(coro)


class TestPostMarginNettingWiring(unittest.TestCase):
    def test_net_against_positions_true_resolves_positions_and_nets(self):
        body = StrategyBuilderMarginRequest(legs=[_leg()], margin_source="breeze_api")
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

        with patch.object(
            rsb.breeze, "get_strategy_builder_margin_source", return_value="breeze_api"
        ), patch.object(
            rsb.breeze, "get_positions", return_value={"Status": 200, "Success": position_rows, "Error": None}
        ), patch.object(
            rsb.breeze, "get_session_breeze", return_value=mock_breeze
        ), patch.object(
            rsb.breeze, "_netted_span_for_legs", return_value=30_000.0
        ) as fake_existing_span, patch.object(
            rsb.breeze,
            "strategy_builder_margin",
            return_value={
                "Status": 200,
                "Error": None,
                "Success": {"span_margin_required": 25_000.0},
            },
        ) as fake_margin_call:
            _run(rsb.post_margin(body, _ctx()))

        fake_existing_span.assert_called_once()
        self.assertEqual(fake_margin_call.call_count, 1)
        _, kwargs = fake_margin_call.call_args
        self.assertIsNotNone(kwargs["existing_legs"])
        self.assertEqual(len(kwargs["existing_legs"]), 1)
        self.assertEqual(kwargs["existing_span_value"], 30_000.0)
        self.assertEqual(kwargs["netting_position_count"], 1)
        self.assertIsNone(kwargs["netting_unavailable_reason"])

    def test_net_against_positions_false_skips_positions_fetch_entirely(self):
        body = StrategyBuilderMarginRequest(
            legs=[_leg()], margin_source="breeze_api", net_against_positions=False
        )

        with patch.object(
            rsb.breeze, "get_strategy_builder_margin_source", return_value="breeze_api"
        ), patch.object(rsb.breeze, "get_positions") as fake_get_positions, patch.object(
            rsb.breeze,
            "strategy_builder_margin",
            return_value={"Status": 200, "Error": None, "Success": {"span_margin_required": 40_000.0}},
        ) as fake_margin_call:
            _run(rsb.post_margin(body, _ctx()))

        fake_get_positions.assert_not_called()
        _, kwargs = fake_margin_call.call_args
        self.assertIsNone(kwargs["existing_legs"])
        self.assertIsNone(kwargs["existing_span_value"])
        self.assertEqual(kwargs["netting_position_count"], 0)

    def test_no_open_positions_nets_nothing_no_error(self):
        body = StrategyBuilderMarginRequest(legs=[_leg()], margin_source="breeze_api")

        with patch.object(
            rsb.breeze, "get_strategy_builder_margin_source", return_value="breeze_api"
        ), patch.object(
            rsb.breeze, "get_positions", return_value={"Status": 200, "Success": [], "Error": None}
        ), patch.object(
            rsb.breeze,
            "strategy_builder_margin",
            return_value={"Status": 200, "Error": None, "Success": {"span_margin_required": 40_000.0}},
        ) as fake_margin_call:
            _run(rsb.post_margin(body, _ctx()))

        _, kwargs = fake_margin_call.call_args
        self.assertIsNone(kwargs["existing_legs"])
        self.assertIsNone(kwargs["existing_span_value"])
        self.assertIsNone(kwargs["netting_unavailable_reason"])

    def test_positions_fetch_failure_sets_unavailable_reason_falls_back(self):
        body = StrategyBuilderMarginRequest(legs=[_leg()], margin_source="breeze_api")

        with patch.object(
            rsb.breeze, "get_strategy_builder_margin_source", return_value="breeze_api"
        ), patch.object(
            rsb.breeze,
            "get_positions",
            return_value={"Status": 400, "Error": "Unable to connect to broker.", "Success": None},
        ), patch.object(
            rsb.breeze,
            "strategy_builder_margin",
            return_value={"Status": 200, "Error": None, "Success": {"span_margin_required": 40_000.0}},
        ) as fake_margin_call:
            _run(rsb.post_margin(body, _ctx()))

        _, kwargs = fake_margin_call.call_args
        self.assertIsNone(kwargs["existing_legs"])
        self.assertIsNone(kwargs["existing_span_value"])
        self.assertIsNotNone(kwargs["netting_unavailable_reason"])

    def test_exchange_baseline_source_never_calls_netted_span_for_legs(self):
        """D6: the baseline source must stay fully offline -- no live M(P) call."""
        body = StrategyBuilderMarginRequest(legs=[_leg()], margin_source="exchange_baseline")
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

        with patch.object(
            rsb.breeze, "get_strategy_builder_margin_source", return_value="exchange_baseline"
        ), patch.object(
            rsb.breeze, "get_positions", return_value={"Status": 200, "Success": position_rows, "Error": None}
        ), patch.object(
            rsb.breeze, "_netted_span_for_legs"
        ) as fake_existing_span, patch.object(
            rsb.breeze, "get_session_breeze"
        ) as fake_get_session, patch.object(
            rsb.breeze,
            "strategy_builder_margin",
            return_value={"Status": 200, "Error": None, "Success": {"span_margin_required": 40_000.0}},
        ) as fake_margin_call:
            _run(rsb.post_margin(body, _ctx()))

        fake_existing_span.assert_not_called()
        fake_get_session.assert_not_called()
        _, kwargs = fake_margin_call.call_args
        # existing_legs still passed through (baseline path nets locally); no live M(P).
        self.assertIsNotNone(kwargs["existing_legs"])
        self.assertIsNone(kwargs["existing_span_value"])


if __name__ == "__main__":
    unittest.main()
