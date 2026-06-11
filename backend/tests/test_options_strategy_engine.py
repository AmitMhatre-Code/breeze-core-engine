"""Unit tests for options strategy engine helpers."""
import unittest
from unittest.mock import MagicMock, call, patch

from icici_breeze_backend.app.services.options_strategy_engine import (
    EngineContext,
    QuoteRow,
    StrategyResult,
    TradeLeg,
    _attach_margins_and_returns,
    _build_liquidity_cache,
    _expand_chain_to_liquidity_boundary,
    _fetch_full_chain_side,
    _floor_lots,
    _strategy_boundary_strikes,
    _strike_window,
    _tail_strikes_needed,
    calc_bull_call_spread,
    calc_naked_ce_short,
)
from icici_breeze_backend.app.services.processor import OptionChainBackoff, processor


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
            min_pop_pct=1.0,
            provision_elm=False,
            strategy_category="directional",
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
        margin_ctx = EngineContext(
            processor=processor,
            user_id="u1",
            stock_code="NIFTY",
            exchange_code="NFO",
            expiry_display="09-Jun-2025",
            range_lower=23400,
            range_upper=23600,
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
        _attach_margins_and_returns(
            processor, "u1", "NFO", "NIFTY", "09-Jun-2025", results, margin_ctx
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


class TestTailStrikesNeeded(unittest.TestCase):
    def test_returns_all_when_chain_empty(self):
        needed = [23000, 23100, 23200]
        self.assertEqual(_tail_strikes_needed(needed, set()), needed)

    def test_ignores_in_range_gaps(self):
        needed = [23000, 23100, 23200, 23300]
        chain = {23100, 23300}
        self.assertEqual(_tail_strikes_needed(needed, chain), [23000, 23200])

    def test_includes_below_min_and_above_max(self):
        needed = [22800, 23000, 23600, 23800]
        chain = {23000, 23100, 23200, 23300, 23400, 23500, 23600}
        self.assertEqual(_tail_strikes_needed(needed, chain), [22800, 23800])


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


class TestBuildLiquidityCache(unittest.TestCase):
    def _ctx(self, proc: MagicMock) -> EngineContext:
        strikes = list(range(23000, 24100, 50))
        return EngineContext(
            processor=proc,
            user_id="u1",
            stock_code="NIFTY",
            exchange_code="NFO",
            expiry_display="09-Jun-2025",
            range_lower=23400,
            range_upper=23600,
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

    def test_two_full_chain_calls_when_range_within_chain(self):
        proc = MagicMock()
        proc.fetch_option_chain_quotes_sb.side_effect = _mock_fetch_chain_sb
        ctx = self._ctx(proc)
        _build_liquidity_cache(ctx)
        full_chain_calls = [
            c
            for c in proc.fetch_option_chain_quotes_sb.call_args_list
            if c.kwargs.get("strike_price") is None
        ]
        tail_calls = [
            c
            for c in proc.fetch_option_chain_quotes_sb.call_args_list
            if c.kwargs.get("strike_price") is not None
        ]
        self.assertEqual(len(full_chain_calls), 2)
        self.assertEqual(len(tail_calls), 0)
        self.assertFalse(ctx.halted)
        self.assertIn((23500, "Call"), ctx.cache)

    def test_tail_calls_when_window_exceeds_chain(self):
        proc = MagicMock()

        def narrow_chain(*_args, **kwargs):
            strike_price = kwargs.get("strike_price")
            right = kwargs.get("right", "Call")
            if strike_price is None:
                strikes = [23400, 23500, 23600]
                return {"Status": 200, "Success": [_chain_row(s, right) for s in strikes]}
            return {"Status": 200, "Success": [_chain_row(int(strike_price), right)]}

        proc.fetch_option_chain_quotes_sb.side_effect = narrow_chain
        ctx = self._ctx(proc)
        _build_liquidity_cache(ctx)
        tail_calls = [
            c
            for c in proc.fetch_option_chain_quotes_sb.call_args_list
            if c.kwargs.get("strike_price") is not None
        ]
        self.assertGreater(len(tail_calls), 0)
        tail_strikes = {int(c.kwargs["strike_price"]) for c in tail_calls}
        self.assertTrue(any(s < 23400 for s in tail_strikes) or any(s > 23600 for s in tail_strikes))


class TestExpandChainToLiquidityBoundary(unittest.TestCase):
    def test_steps_out_one_strike_at_a_time_beyond_initial_chain(self):
        proc = MagicMock()
        strikes = list(range(23000, 26200, 50))

        def fetch_chain(*_args, **kwargs):
            strike_price = kwargs.get("strike_price")
            right = kwargs.get("right", "Call")
            if strike_price is None:
                return {
                    "Status": 200,
                    "Success": [_chain_row(s, right) for s in range(23000, 24700, 50)],
                }
            sp = int(strike_price)
            if sp > 26100:
                return {
                    "Status": 200,
                    "Success": [
                        {
                            **_chain_row(sp, right),
                            "total_buy_qty": 0,
                            "total_sell_qty": 0,
                        }
                    ],
                }
            return {"Status": 200, "Success": [_chain_row(sp, right)]}

        proc.fetch_option_chain_quotes_sb.side_effect = fetch_chain
        ctx = EngineContext(
            processor=proc,
            user_id="u1",
            stock_code="NIFTY",
            exchange_code="NFO",
            expiry_display="09-Jun-2025",
            range_lower=23400,
            range_upper=23600,
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
        ctx.chain_backoff = OptionChainBackoff()
        _fetch_full_chain_side(ctx, "Call", fetch_reason="test CE")
        _fetch_full_chain_side(ctx, "Put", fetch_reason="test PE")
        _expand_chain_to_liquidity_boundary(ctx)
        per_strike_calls = [
            c
            for c in proc.fetch_option_chain_quotes_sb.call_args_list
            if c.kwargs.get("strike_price") is not None
        ]
        self.assertGreater(len(per_strike_calls), 0)
        self.assertIn((26100, "Call"), ctx.cache)
        self.assertTrue(ctx.cache[(26100, "Call")].liquid)
        self.assertNotIn((26150, "Call"), ctx.cache)


class TestFetchOptionChainBackoff(unittest.TestCase):
    def test_503_backoff_escalates_then_succeeds(self):
        proc = processor()
        backoff = OptionChainBackoff()
        mock_breeze = MagicMock()
        mock_breeze.get_option_chain_quotes.side_effect = [
            {"Status": 503, "Error": "busy"},
            {"Status": 503, "Error": "busy"},
            {
                "Status": 200,
                "Success": [
                    {
                        "strike_price": 23500,
                        "total_buy_qty": 10,
                        "total_sell_qty": 10,
                        "ltp": 1,
                        "best_bid_price": 1,
                        "best_offer_price": 1,
                    }
                ],
            },
        ]
        with patch.object(proc, "get_session_breeze", return_value=mock_breeze), patch(
            "icici_breeze_backend.app.services.processor.time.sleep"
        ) as mock_sleep:
            res = proc.fetch_option_chain_quotes_sb(
                "u1",
                "NIFTY",
                "NFO",
                "2025-06-09T06:00:00.000Z",
                "Call",
                backoff=backoff,
            )
        self.assertEqual(res["Status"], 200)
        self.assertEqual(mock_sleep.call_args_list, [call(0.5), call(1.0)])
        self.assertEqual(mock_breeze.get_option_chain_quotes.call_count, 3)
        self.assertEqual(backoff.consecutive_503, 0)

    def test_three_consecutive_503_returns_last(self):
        proc = processor()
        backoff = OptionChainBackoff()
        mock_breeze = MagicMock()
        mock_breeze.get_option_chain_quotes.return_value = {"Status": 503, "Error": "busy"}
        with patch.object(proc, "get_session_breeze", return_value=mock_breeze), patch(
            "icici_breeze_backend.app.services.processor.time.sleep"
        ) as mock_sleep:
            res = proc.fetch_option_chain_quotes_sb(
                "u1",
                "NIFTY",
                "NFO",
                "2025-06-09T06:00:00.000Z",
                "Put",
                backoff=backoff,
            )
        self.assertEqual(res["Status"], 503)
        self.assertEqual(mock_breeze.get_option_chain_quotes.call_count, 3)
        self.assertEqual(mock_sleep.call_args_list, [call(0.5), call(1.0)])


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
            min_pop_pct=1.0,
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


if __name__ == "__main__":
    unittest.main()
