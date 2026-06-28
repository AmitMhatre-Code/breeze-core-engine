"""Smoke tests for Strategy Builder Pydantic schemas (run: cd backend && PYTHONPATH=src python -m pytest tests/test_strategy_builder_domain.py)."""
import unittest

from icici_breeze_backend.app.domain.strategy_builder import (
    StrategyBuilderExecuteLeg,
    StrategyBuilderExecuteRequest,
    StrategyBuilderLegIn,
    StrategyBuilderMarginRequest,
)


class TestStrategyBuilderDomain(unittest.TestCase):
    def test_margin_request_min_legs(self):
        body = StrategyBuilderMarginRequest(
            legs=[
                StrategyBuilderLegIn(
                    stock_code="NIFTY",
                    expiry_date="27-Mar-2025",
                    right="Call",
                    strike_price="22000",
                    quantity="75",
                    action="Buy",
                )
            ]
        )
        self.assertEqual(len(body.legs), 1)

    def test_margin_request_baseline_only_fields(self):
        body = StrategyBuilderMarginRequest(
            legs=[
                StrategyBuilderLegIn(
                    stock_code="NIFTY",
                    expiry_date="27-Mar-2025",
                    right="Call",
                    strike_price="22000",
                    quantity="75",
                    action="Buy",
                )
            ],
            margin_source="exchange_baseline",
            baseline_only=True,
        )
        self.assertEqual(body.margin_source, "exchange_baseline")
        self.assertTrue(body.baseline_only)

    def test_execute_request_with_idempotency(self):
        body = StrategyBuilderExecuteRequest(
            legs=[
                StrategyBuilderExecuteLeg(
                    stock_code="NIFTY",
                    expiry_date="27-Mar-2025",
                    right="Put",
                    strike_price="21500",
                    quantity="75",
                    action="Sell",
                    idempotency_key="k1",
                )
            ]
        )
        self.assertEqual(body.legs[0].idempotency_key, "k1")


if __name__ == "__main__":
    unittest.main()
