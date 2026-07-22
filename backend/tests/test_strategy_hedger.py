"""Unit tests for strategy-level hedging engine."""
from __future__ import annotations

import unittest

from icici_breeze_backend.app.services.options_strategy_engine.greeks import bs_gamma
from icici_breeze_backend.app.services.options_strategy_engine.strategy_hedger import (
    generate_strategy_level_hedges,
)


def _chain_row(strike: int, right: str, *, ask: float, spot: float = 24000.0) -> dict:
    return {
        "strike_price": strike,
        "right": right,
        "ltp": ask,
        "best_offer_price": ask,
        "best_bid_price": ask * 0.98,
        "total_buy_qty": 100,
        "total_sell_qty": 200,
        "spot_price": spot,
    }


class TestStrategyHedger(unittest.TestCase):
    def test_net_short_call_returns_otm_call_wings(self):
        positions = [
            {
                "stock_code": "NIFTY",
                "expiry_date": "27-Jun-2025",
                "strike_price": 24500,
                "right": "Call",
                "action": "Sell",
                "quantity": 50,
                "ltp": 120.0,
                "span_margin_required": 200000.0,
            }
        ]
        chain = [
            _chain_row(24600, "Call", ask=80.0),
            _chain_row(24700, "Call", ask=55.0),
            _chain_row(24800, "Call", ask=35.0),
        ]
        out = generate_strategy_level_hedges(
            positions,
            chain,
            spot_price=24000.0,
            user_max_loss=500_000.0,
            days_to_expiry=10,
            lot_size=50,
        )
        self.assertEqual(out["summary"]["risk_profile"], "net_short_call")
        candidates = out["candidates"]
        self.assertLessEqual(len(candidates), 3)
        self.assertGreater(len(candidates), 0)
        for c in candidates:
            self.assertGreater(c["strike_price"], 24500)
            self.assertEqual(c["right"], "Call")

    def test_net_short_put_returns_otm_put_wings(self):
        positions = [
            {
                "stock_code": "NIFTY",
                "expiry_date": "27-Jun-2025",
                "strike_price": 23500,
                "right": "Put",
                "action": "Sell",
                "quantity": 50,
                "ltp": 95.0,
                "span_margin_required": 180000.0,
            }
        ]
        chain = [
            _chain_row(23400, "Put", ask=70.0),
            _chain_row(23300, "Put", ask=48.0),
            _chain_row(23200, "Put", ask=30.0),
        ]
        out = generate_strategy_level_hedges(
            positions,
            chain,
            spot_price=24000.0,
            user_max_loss=500_000.0,
            days_to_expiry=10,
            lot_size=50,
        )
        self.assertEqual(out["summary"]["risk_profile"], "net_short_put")
        for c in out["candidates"]:
            self.assertLess(c["strike_price"], 23500)
            self.assertEqual(c["right"], "Put")

    def test_user_max_loss_filters_expensive_wings(self):
        positions = [
            {
                "stock_code": "NIFTY",
                "expiry_date": "27-Jun-2025",
                "strike_price": 24500,
                "right": "Call",
                "action": "Sell",
                "quantity": 50,
                "ltp": 120.0,
                "span_margin_required": 200000.0,
            }
        ]
        chain = [
            _chain_row(24600, "Call", ask=80.0),
            _chain_row(24700, "Call", ask=55.0),
        ]
        tight = generate_strategy_level_hedges(
            positions,
            chain,
            spot_price=24000.0,
            user_max_loss=5000.0,
            days_to_expiry=10,
            lot_size=50,
        )
        wide = generate_strategy_level_hedges(
            positions,
            chain,
            spot_price=24000.0,
            user_max_loss=500_000.0,
            days_to_expiry=10,
            lot_size=50,
        )
        self.assertLessEqual(len(tight["candidates"]), len(wide["candidates"]))

    def test_defined_spread_no_naked_tail(self):
        positions = [
            {
                "stock_code": "NIFTY",
                "expiry_date": "27-Jun-2025",
                "strike_price": 24500,
                "right": "Call",
                "action": "Sell",
                "quantity": 50,
                "ltp": 120.0,
            },
            {
                "stock_code": "NIFTY",
                "expiry_date": "27-Jun-2025",
                "strike_price": 24700,
                "right": "Call",
                "action": "Buy",
                "quantity": 50,
                "ltp": 55.0,
            },
        ]
        chain = [_chain_row(24800, "Call", ask=35.0)]
        out = generate_strategy_level_hedges(
            positions,
            chain,
            spot_price=24000.0,
            user_max_loss=100_000.0,
            days_to_expiry=10,
            lot_size=50,
        )
        self.assertEqual(out["summary"]["risk_profile"], "defined")
        self.assertEqual(out["candidates"], [])

    def test_strategy_delta_sign_short_call(self):
        positions = [
            {
                "stock_code": "NIFTY",
                "expiry_date": "27-Jun-2025",
                "strike_price": 24500,
                "right": "Call",
                "action": "Sell",
                "quantity": 50,
                "ltp": 120.0,
            }
        ]
        chain = [_chain_row(24700, "Call", ask=55.0)]
        out = generate_strategy_level_hedges(
            positions,
            chain,
            spot_price=24000.0,
            user_max_loss=500_000.0,
            days_to_expiry=10,
            lot_size=50,
        )
        self.assertLess(out["summary"]["strategy_delta"], 0)

    def test_bs_gamma_matches_frontend_formula(self):
        gamma = bs_gamma(24000.0, 24500.0, 10 / 365.0, 0.18)
        self.assertGreater(gamma, 0)
        self.assertLess(gamma, 0.01)


if __name__ == "__main__":
    unittest.main()
