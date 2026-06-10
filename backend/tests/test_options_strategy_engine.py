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
    _strategy_boundary_strikes,
    _strike_window,
    calc_bull_call_spread,
    calc_naked_ce_short,
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
            search_interval=50,
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

    def test_search_interval_modal_near_spot(self):
        strikes = [23000, 23050, 23100, 24000, 25000, 26000]
        self.assertEqual(processor.search_interval(strikes, 23100), 50)

    def test_search_interval_wider_padding_window(self):
        strikes = list(range(22000, 26200, 50))
        si = processor.search_interval(strikes, 23310)
        window_min_gap = _strike_window(strikes, 22500, 24500, 23300, 50, pad_intervals=3)
        window_search = _strike_window(strikes, 22500, 24500, 23300, si, pad_intervals=3)
        self.assertGreaterEqual(max(window_search), max(window_min_gap))


class TestStrategyBoundaryStrikes(unittest.TestCase):
    def test_includes_first_ce_above_range_upper(self):
        strikes = list(range(22000, 24600, 50)) + list(range(25000, 27000, 500))
        boundaries = _strategy_boundary_strikes(strikes, 22500, 24500, 23310, 23300)
        self.assertIn(25000, boundaries)

    def test_includes_last_pe_below_range_lower(self):
        strikes = list(range(21000, 24600, 50))
        boundaries = _strategy_boundary_strikes(strikes, 22500, 24500, 23310, 23300)
        self.assertIn(22450, boundaries)


class TestNakedCeAboveRangeUpper(unittest.TestCase):
    def test_selects_liquid_ce_above_range_when_only_boundary_quoted(self):
        strikes = list(range(22000, 24600, 50)) + [25000, 25500, 26000]
        cache: dict = {}
        for s in range(22000, 24600, 50):
            cache[(s, "Call")] = QuoteRow(
                strike=s,
                right="Call",
                ltp=50.0,
                best_bid_price=49.0,
                best_offer_price=51.0,
                total_buy_qty=0,
                total_sell_qty=0,
                buy_sell_ratio=0.0,
                spot_price=23310.0,
            )
        cache[(25000, "Call")] = QuoteRow(
            strike=25000,
            right="Call",
            ltp=30.0,
            best_bid_price=29.0,
            best_offer_price=31.0,
            total_buy_qty=100,
            total_sell_qty=100,
            buy_sell_ratio=1.0,
            spot_price=23310.0,
        )
        mock_processor = MagicMock()
        mock_processor.strategy_builder_margin.return_value = {
            "Status": 200,
            "Success": {"span_margin_required": 200_000},
        }
        ctx = EngineContext(
            processor=mock_processor,
            user_id="u1",
            stock_code="NIFTY",
            exchange_code="NFO",
            expiry_display="20-Jun-2026",
            range_lower=22500,
            range_upper=24500,
            margin_rupees=1_000_000,
            max_loss_rupees=500_000,
            provision_elm=False,
            lot_size=75,
            strikes=strikes,
            strike_step=50,
            search_interval=50,
            spot=23310,
            atm_strike=23300,
            cache=cache,
        )
        res = calc_naked_ce_short(ctx)
        self.assertEqual(res.status, "ok")
        self.assertEqual(len(res.legs), 1)
        self.assertEqual(res.legs[0].strike, 25000)
        self.assertEqual(res.legs[0].side, "Sell")


if __name__ == "__main__":
    unittest.main()
