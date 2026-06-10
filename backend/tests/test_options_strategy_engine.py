"""Unit tests for options strategy engine helpers."""
import unittest
from unittest.mock import MagicMock

from icici_breeze_backend.app.services.options_strategy_engine import (
    EngineContext,
    QuoteRow,
    StrategyResult,
    TradeLeg,
    _attach_margins_and_returns,
    _floor_lots,
    _strike_window,
    calc_bull_call_spread,
)
from icici_breeze_backend.app.services.processor import processor


class TestStrikeWindow(unittest.TestCase):
    def test_includes_atm_and_padding(self):
        strikes = list(range(23000, 24100, 50))
        atm = 23500
        window = _strike_window(strikes, 23400, 23600, atm, 50, pad_intervals=3)
        self.assertIn(23500, window)
        self.assertIn(23250, window)  # 23400 - 3*50
        self.assertIn(23750, window)  # 23600 + 3*50
        self.assertNotIn(23000, window)


class TestFloorLots(unittest.TestCase):
    def test_snaps_to_lot_multiples(self):
        self.assertEqual(_floor_lots(500_000, 120_000, 75), 300)
        self.assertEqual(_floor_lots(50_000, 120_000, 75), 0)


class TestBullCallSpreadSizing(unittest.TestCase):
    def _ctx(self) -> EngineContext:
        strikes = list(range(23000, 24100, 50))
        cache = {}
        for s in strikes:
            cache[(s, "Call")] = QuoteRow(
                strike=s,
                right="Call",
                ltp=100.0,
                best_bid_price=99.0,
                best_offer_price=101.0,
                total_buy_qty=100,
                total_sell_qty=100,
                buy_sell_ratio=1.0,
                spot_price=23500.0,
            )
        return EngineContext(
            processor=MagicMock(),
            user_id="u1",
            stock_code="NIFTY",
            exchange_code="NFO",
            expiry_display="09-Jun-2025",
            range_lower=23400,
            range_upper=23600,
            margin_rupees=500_000,
            max_loss_rupees=200_000,
            provision_elm=False,
            lot_size=75,
            strikes=strikes,
            strike_step=50,
            spot=23500,
            atm_strike=23500,
            cache=cache,
        )

    def test_produces_legs_when_viable(self):
        ctx = self._ctx()
        res = calc_bull_call_spread(ctx)
        self.assertEqual(res.status, "ok")
        self.assertEqual(len(res.legs), 2)
        self.assertGreaterEqual(res.legs[0].quantity, 75)

    def test_skips_when_insufficient_risk(self):
        ctx = self._ctx()
        ctx.max_loss_rupees = 100
        res = calc_bull_call_spread(ctx)
        self.assertEqual(res.status, "skipped")


class TestMarginBatching(unittest.TestCase):
    def test_unique_structures_only(self):
        processor = MagicMock()
        processor.strategy_builder_margin.return_value = {
            "Status": 200,
            "Success": {"span_margin_required": 100_000},
        }
        legs_a = [
            TradeLeg("Call", "Buy", 23500, 75, 100.0),
            TradeLeg("Call", "Sell", 23600, 75, 50.0),
        ]
        legs_b = [
            TradeLeg("Call", "Buy", 23500, 150, 100.0),
            TradeLeg("Call", "Sell", 23600, 150, 50.0),
        ]
        results = [
            StrategyResult("a", "A", "ok", legs=legs_a),
            StrategyResult("b", "B", "ok", legs=legs_b),
        ]
        _attach_margins_and_returns(
            processor, "u1", "NFO", "NIFTY", "09-Jun-2025", results
        )
        self.assertEqual(processor.strategy_builder_margin.call_count, 2)


class TestProcessorStrikeInterval(unittest.TestCase):
    def test_min_gap(self):
        self.assertEqual(processor.strike_interval([23000, 23050, 23100]), 50)
        self.assertEqual(processor.strike_interval([23000]), 50)


if __name__ == "__main__":
    unittest.main()
