"""Unit tests for options strategy engine helpers."""
import asyncio
import unittest
from unittest.mock import MagicMock

from icici_breeze_backend.app.services.options_strategy_engine import (
    EngineContext,
    QuoteRow,
    StrategyResult,
    TradeLeg,
    _attach_margins_and_returns,
    _build_liquidity_cache,
    _floor_lots,
    _strike_window,
    calc_bull_call_spread,
    calc_naked_ce_short,
    run_propose_trades,
)
from icici_breeze_backend.app.services.options_strategy_engine.strategies.income.short_straddle import (
    calc_short_straddle,
)
from icici_breeze_backend.app.services.options_strategy_engine.greeks import (
    norm_ppf,
    snap_strike,
    strike_for_abs_delta,
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
            delta = 0.50 if s == 23500 else (0.30 if s == 23600 else 0.20)
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
                delta=delta,
                liquidity_score=0.85,
                iv=0.18,
            )
        return EngineContext(
            processor=MagicMock(),
            user_id="u1",
            stock_code="NIFTY",
            exchange_code="NFO",
            expiry_display="09-Jun-2025",
            margin_rupees=500_000,
            max_loss_rupees=200_000,
            min_pop_pct=1.0,
            provision_elm=False,
            strategy_category="bullish",
            risk_reward_profile="moderate",
            lot_size=75,
            strikes=strikes,
            strike_step=50,
            search_interval=50,
            spot=23500,
            atm_strike=23500,
            atm_iv=0.18,
            cache=cache,
        )

    def test_produces_legs_when_viable(self):
        ctx = self._ctx()
        results = calc_bull_call_spread(ctx)
        self.assertIsInstance(results, list)
        ok = [r for r in results if r.status == "ok"]
        self.assertGreaterEqual(len(ok), 1)
        res = ok[0]
        self.assertEqual(len(res.legs), 2)
        self.assertGreaterEqual(res.legs[0].quantity, 75)
        self.assertIsNotNone(res.conviction_profile)

    def test_skips_when_insufficient_risk(self):
        ctx = self._ctx()
        ctx.max_loss_rupees = 100
        results = calc_bull_call_spread(ctx)
        if len(results) == 1:
            self.assertEqual(results[0].status, "skipped")
        else:
            self.assertEqual(len([r for r in results if r.status == "ok"]), 0)


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
        margin_ctx = EngineContext(
            processor=processor,
            user_id="u1",
            stock_code="NIFTY",
            exchange_code="NFO",
            expiry_display="09-Jun-2025",
            margin_rupees=500_000,
            max_loss_rupees=200_000,
            min_pop_pct=65.0,
            provision_elm=False,
            strategy_category="income",
            lot_size=75,
            strikes=list(range(23000, 24100, 50)),
            strike_step=50,
            search_interval=50,
            spot=23500,
            atm_strike=23500,
        )
        asyncio.run(
            _attach_margins_and_returns(
            processor, "u1", "NFO", "NIFTY", "09-Jun-2025", results, margin_ctx
        )
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


def _chain_row(strike: int, right: str) -> dict:
    return {
        "strike_price": strike,
        "ltp": 50.0,
        "best_bid_price": 49.0,
        "best_offer_price": 51.0,
        "total_buy_qty": 100,
        "total_sell_qty": 100,
        "spot_price": 23500.0,
        "right": right,
    }


def _mock_fetch_chain_sb(*_args, **kwargs):
    strike_price = kwargs.get("strike_price")
    right = kwargs.get("right", "Call")
    if strike_price is None:
        strikes = list(range(23000, 24100, 50))
        return {"Status": 200, "Success": [_chain_row(s, right) for s in strikes]}
    return {"Status": 200, "Success": [_chain_row(int(strike_price), right)]}


def _mock_full_option_chain(*_args, **_kwargs):
    strikes = list(range(23000, 24100, 50))
    chain_rows = [
        {
            "strike_price": s,
            "call": _chain_row(s, "Call"),
            "put": _chain_row(s, "Put"),
        }
        for s in strikes
    ]
    return {
        "Status": 200,
        "Success": {
            "chain_rows": chain_rows,
            "spot_price": 23500.0,
            "atm_strike": 23500,
            "quote_source": "websocket",
        },
    }


class TestBuildLiquidityCache(unittest.TestCase):
    def _ctx(self, proc: MagicMock) -> EngineContext:
        strikes = list(range(23000, 24100, 50))
        return EngineContext(
            processor=proc,
            user_id="u1",
            stock_code="NIFTY",
            exchange_code="NFO",
            expiry_display="09-Jun-2025",
            margin_rupees=500_000,
            max_loss_rupees=200_000,
            min_pop_pct=65.0,
            provision_elm=False,
            strategy_category="income",
            lot_size=75,
            strikes=strikes,
            strike_step=50,
            search_interval=50,
            spot=23500,
            atm_strike=23500,
        )

    def test_single_routed_chain_call_populates_bulk_cache(self):
        proc = MagicMock()
        proc.get_full_option_chain.return_value = _mock_full_option_chain()
        ctx = self._ctx(proc)
        _build_liquidity_cache(ctx)
        self.assertEqual(proc.get_full_option_chain.call_count, 1)
        proc.fetch_option_chain_quotes_sb.assert_not_called()
        self.assertFalse(ctx.halted)
        self.assertIn((23500, "Call"), ctx.cache)


class TestNormPpf(unittest.TestCase):
    def test_median_is_zero(self):
        self.assertAlmostEqual(norm_ppf(0.5), 0.0, places=10)

    def test_symmetric_quantiles(self):
        self.assertAlmostEqual(norm_ppf(0.841344746), 1.0, places=5)
        self.assertAlmostEqual(norm_ppf(0.158655254), -1.0, places=5)


class TestStrikeForAbsDelta(unittest.TestCase):
    def test_snaps_otm_call_above_spot(self):
        strikes = list(range(23000, 24100, 50))
        k = strike_for_abs_delta(23500.0, 30 / 365.0, 0.18, "Call", 0.15)
        snapped = snap_strike(strikes, k, prefer="ceil")
        self.assertIsNotNone(snapped)
        self.assertGreater(snapped, 23500)

    def test_snaps_otm_put_below_spot(self):
        strikes = list(range(23000, 24100, 50))
        k = strike_for_abs_delta(23500.0, 30 / 365.0, 0.18, "Put", 0.15)
        snapped = snap_strike(strikes, k, prefer="floor")
        self.assertIsNotNone(snapped)
        self.assertLess(snapped, 23500)


class TestNakedCeDeltaAnchor(unittest.TestCase):
    def test_selects_liquid_otm_ce_near_target_delta(self):
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
            delta=0.15,
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
            margin_rupees=1_000_000,
            max_loss_rupees=500_000,
            min_pop_pct=85.0,
            provision_elm=False,
            strategy_category="income",
            lot_size=75,
            strikes=strikes,
            strike_step=50,
            search_interval=50,
            spot=23310,
            atm_strike=23300,
            atm_iv=0.18,
            cache=cache,
        )
        res = calc_naked_ce_short(ctx)
        self.assertEqual(res.status, "ok")
        self.assertEqual(len(res.legs), 1)
        self.assertEqual(res.legs[0].strike, 25000)
        self.assertEqual(res.legs[0].side, "Sell")


class TestIncomePopGate(unittest.TestCase):
    def test_short_straddle_flags_relaxed_when_pop_below_minimum(self):
        stp = 23300
        strikes = list(range(23000, 23600, 50))
        cache = {
            (stp, "Call"): QuoteRow(
                strike=stp,
                right="Call",
                ltp=100.0,
                best_bid_price=99.0,
                best_offer_price=101.0,
                total_buy_qty=100,
                total_sell_qty=100,
                buy_sell_ratio=1.0,
                spot_price=23310.0,
            ),
            (stp, "Put"): QuoteRow(
                strike=stp,
                right="Put",
                ltp=95.0,
                best_bid_price=94.0,
                best_offer_price=96.0,
                total_buy_qty=100,
                total_sell_qty=100,
                buy_sell_ratio=1.0,
                spot_price=23310.0,
            ),
        }
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
            margin_rupees=1_000_000,
            max_loss_rupees=500_000,
            min_pop_pct=95.0,
            provision_elm=False,
            strategy_category="income",
            lot_size=75,
            strikes=strikes,
            strike_step=50,
            search_interval=50,
            spot=23310,
            atm_strike=stp,
            atm_iv=0.18,
            cache=cache,
        )
        res = calc_short_straddle(ctx)
        self.assertEqual(res.status, "ok")
        self.assertEqual(res.compliance, "relaxed")
        self.assertIn("pop_floor", res.constraint_violations)


if __name__ == "__main__":
    unittest.main()
