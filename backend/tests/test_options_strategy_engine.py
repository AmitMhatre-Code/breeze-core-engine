"""Unit tests for options strategy engine helpers."""
import asyncio
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
    run_propose_trades,
)
from icici_breeze_backend.app.services.options_strategy_engine.greeks import (
    snap_strike,
    strike_for_abs_delta,
)
from icici_breeze_backend.app.services.options_strategy_engine.icici_async_fetch import IciciCallBudget
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

    def test_targeted_fetches_bounded_when_bulk_chain_narrow(self):
        proc = MagicMock()
        strikes = list(range(23000, 24100, 50))

        def narrow_chain(*_args, **kwargs):
            strike_price = kwargs.get("strike_price")
            right = kwargs.get("right", "Call")
            if strike_price is None:
                narrow = [23400, 23500, 23600]
                return {"Status": 200, "Success": [_chain_row(s, right) for s in narrow]}
            return {"Status": 200, "Success": [_chain_row(int(strike_price), right)]}

        proc.fetch_lot_size.return_value = 75
        proc.list_option_strikes.return_value = strikes
        proc.strike_interval.return_value = 50
        proc.search_interval.return_value = 50
        proc.fetch_option_chain_quotes_sb.side_effect = narrow_chain
        proc.strategy_builder_margin.return_value = {
            "Status": 200,
            "Success": {"span_margin_required": 100_000},
        }

        out = asyncio.run(
            run_propose_trades(
                proc,
                "u1",
                exchange_code="NFO",
                stock_code="NIFTY",
                expiry_date="09-Jun-2025",
                margin_lacs=5.0,
                max_loss_lacs=2.0,
                min_pop_pct=85.0,
                provision_elm=False,
                strategy_category="income",
                enable_audit=False,
            )
        )
        self.assertEqual(out["Status"], 200)
        targeted_calls = [
            c
            for c in proc.fetch_option_chain_quotes_sb.call_args_list
            if c.kwargs.get("strike_price") is not None
        ]
        bulk_calls = [
            c
            for c in proc.fetch_option_chain_quotes_sb.call_args_list
            if c.kwargs.get("strike_price") is None
        ]
        self.assertEqual(len(bulk_calls), 2)
        self.assertGreater(len(targeted_calls), 0)
        self.assertLessEqual(len(targeted_calls), 20)


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
    def setUp(self) -> None:
        from icici_breeze_backend.app.services.icici_api_pacing import GlobalIciciApiPacer

        GlobalIciciApiPacer.reset_user("u1")

    def test_503_backoff_waits_user_pause_then_succeeds(self):
        proc = processor()
        backoff = OptionChainBackoff(pause_seconds=2)
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
        self.assertEqual(mock_sleep.call_args_list, [call(2.0), call(3.0)])
        self.assertEqual(mock_breeze.get_option_chain_quotes.call_count, 3)
        self.assertEqual(backoff.consecutive_rate_limited, 0)

    def test_three_consecutive_503_returns_last(self):
        proc = processor()
        backoff = OptionChainBackoff(pause_seconds=1)
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
        self.assertEqual(mock_sleep.call_args_list, [call(1.0), call(2.0)])

    def test_429_uses_same_user_pause(self):
        proc = processor()
        backoff = OptionChainBackoff(pause_seconds=3)
        mock_breeze = MagicMock()
        mock_breeze.get_option_chain_quotes.side_effect = [
            {"Status": 429, "Error": "too many"},
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
        self.assertEqual(mock_sleep.call_args_list, [call(3.0)])


class TestBuildLiquidityCacheUserBackoff(unittest.TestCase):
    def test_chain_backoff_uses_settings_pause_seconds(self):
        strikes = list(range(23400, 23700, 50))
        proc = MagicMock()
        proc.fetch_option_chain_quotes_sb.return_value = {
            "Status": 200,
            "Success": [
                {
                    "strike_price": 23500,
                    "total_buy_qty": 10,
                    "total_sell_qty": 10,
                    "ltp": 1,
                    "best_bid_price": 1,
                    "best_offer_price": 1,
                    "spot_price": 23500,
                }
            ],
        }
        ctx = EngineContext(
            processor=proc,
            user_id="VIKRAMMH",
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
        with patch(
            "icici_breeze_backend.app.services.options_strategy_engine.universe.get_icici_rate_limit_pause_seconds",
            return_value=3,
        ):
            _build_liquidity_cache(ctx)
        self.assertIsNotNone(ctx.chain_backoff)
        self.assertEqual(ctx.chain_backoff.pause_seconds, 3)


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


class TestIciciCallBudget(unittest.IsolatedAsyncioTestCase):
    async def test_blocks_when_minute_budget_exhausted(self):
        clock = [0.0]

        def fake_monotonic() -> float:
            return clock[0]

        async def advance(sec: float) -> None:
            clock[0] += sec

        budget = IciciCallBudget(max_per_minute=2, max_concurrent=2)
        with patch(
            "icici_breeze_backend.app.services.options_strategy_engine.icici_async_fetch.time.monotonic",
            side_effect=fake_monotonic,
        ), patch(
            "icici_breeze_backend.app.services.options_strategy_engine.icici_async_fetch.asyncio.sleep",
            new=advance,
        ):
            await budget.acquire()
            budget.release()
            await budget.acquire()
            budget.release()

            task = asyncio.create_task(budget.acquire())
            await advance(0.01)
            self.assertFalse(task.done())
            await advance(60.0)
            await task
            budget.release()


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


if __name__ == "__main__":
    unittest.main()
