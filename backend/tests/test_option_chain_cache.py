"""Tests for option chain TTL cache (UI + strategy engine)."""
import time
import unittest
from unittest.mock import MagicMock, patch

from icici_breeze_backend.app.services.option_chain_cache import (
    _CACHE_TTL_SECONDS,
    _RAW_CHAIN_CACHE,
    chain_metadata,
    clear_chain_cache,
    get_cached_raw_chain,
    make_chain_cache_key,
    set_cached_raw_chain,
)
from icici_breeze_backend.app.services.options_strategy_engine.types import EngineContext
from icici_breeze_backend.app.services.options_strategy_engine.universe import (
    build_bulk_chain_cache,
    hydrate_bulk_cache_from_raw_chain,
)


def _sample_ce_row(strike: int = 23500) -> dict:
    return {
        "strike_price": strike,
        "ltp": "120.5",
        "open_interest": 1000,
        "total_buy_qty": 50,
        "total_sell_qty": 40,
        "best_bid_price": "120",
        "best_offer_price": "121",
        "spot_price": "23622.9",
    }


def _sample_pe_row(strike: int = 23500) -> dict:
    return {
        "strike_price": strike,
        "ltp": "115.0",
        "open_interest": 900,
        "total_buy_qty": 30,
        "total_sell_qty": 35,
        "best_bid_price": "114",
        "best_offer_price": "116",
        "spot_price": "23622.9",
    }


class TestOptionChainCacheModule(unittest.TestCase):
    def setUp(self):
        clear_chain_cache()

    def tearDown(self):
        clear_chain_cache()

    def test_cache_hit_and_miss(self):
        key = make_chain_cache_key("u1", "NFO", "NIFTY", "16-Jun-2026")
        self.assertIsNone(get_cached_raw_chain(key))
        set_cached_raw_chain(key, [_sample_ce_row()], [_sample_pe_row()])
        hit = get_cached_raw_chain(key)
        self.assertIsNotNone(hit)
        ce, pe, ts = hit
        self.assertEqual(len(ce), 1)
        self.assertEqual(len(pe), 1)
        self.assertGreater(ts, 0)

    def test_cache_expires_after_ttl(self):
        key = make_chain_cache_key("u1", "NFO", "NIFTY", "16-Jun-2026")
        past = time.time() - _CACHE_TTL_SECONDS - 1
        _RAW_CHAIN_CACHE[key] = (past, {"ce_rows": [_sample_ce_row()], "pe_rows": [_sample_pe_row()]})
        self.assertIsNone(get_cached_raw_chain(key))

    def test_chain_metadata(self):
        ts = time.time()
        meta = chain_metadata(ts, True)
        self.assertTrue(meta["from_cache"])
        self.assertIn("chain_fetched_at", meta)


class TestBuildBulkChainCacheReuse(unittest.TestCase):
    def setUp(self):
        clear_chain_cache()

    def tearDown(self):
        clear_chain_cache()

    def _engine_ctx(self) -> EngineContext:
        strikes = list(range(23400, 23850, 50))
        return EngineContext(
            processor=MagicMock(),
            user_id="u1",
            stock_code="NIFTY",
            exchange_code="NFO",
            expiry_display="16-Jun-2026",
            margin_rupees=500_000,
            max_loss_rupees=200_000,
            min_pop_pct=65.0,
            provision_elm=False,
            strategy_category="income",
            risk_reward_profile="moderate",
            lot_size=75,
            strikes=strikes,
            strike_step=50,
            search_interval=50,
            spot=0.0,
            atm_strike=23500,
            cache={},
        )

    def test_hydrate_from_raw_chain_populates_liquid_quotes(self):
        ctx = self._engine_ctx()
        hydrate_bulk_cache_from_raw_chain(
            ctx,
            [_sample_ce_row(23450), _sample_ce_row(23500)],
            [_sample_pe_row(23450), _sample_pe_row(23500)],
        )
        self.assertGreater(len(ctx.cache), 0)
        self.assertGreater(ctx.spot, 0)
        self.assertEqual(ctx.atm_strike, 23500)

    @patch(
        "icici_breeze_backend.app.services.options_strategy_engine.universe.fetch_full_chain_side"
    )
    def test_build_bulk_chain_cache_skips_icici_on_cache_hit(self, mock_fetch_side):
        ctx = self._engine_ctx()
        key = make_chain_cache_key(ctx.user_id, ctx.exchange_code, ctx.stock_code, ctx.expiry_display)
        set_cached_raw_chain(key, [_sample_ce_row()], [_sample_pe_row()])

        build_bulk_chain_cache(ctx, force_refresh=False)

        mock_fetch_side.assert_not_called()
        self.assertGreater(len(ctx.cache), 0)

    @patch(
        "icici_breeze_backend.app.services.options_strategy_engine.universe.fetch_full_chain_side"
    )
    def test_build_bulk_chain_cache_fetches_when_force_refresh(self, mock_fetch_side):
        ctx = self._engine_ctx()
        key = make_chain_cache_key(ctx.user_id, ctx.exchange_code, ctx.stock_code, ctx.expiry_display)
        set_cached_raw_chain(key, [_sample_ce_row()], [_sample_pe_row()])

        build_bulk_chain_cache(ctx, force_refresh=True)

        self.assertEqual(mock_fetch_side.call_count, 2)


class TestGetFullOptionChainCache(unittest.TestCase):
    def setUp(self):
        clear_chain_cache()

    def tearDown(self):
        clear_chain_cache()

    @patch("icici_breeze_backend.app.services.processor.processor.get_session_breeze")
    def test_get_full_option_chain_serves_from_cache(self, mock_session):
        from icici_breeze_backend.app.services.processor import processor as proc

        key = make_chain_cache_key("u1", "NFO", "NIFTY", "16-Jun-2026")
        set_cached_raw_chain(key, [_sample_ce_row()], [_sample_pe_row()])

        out = proc.get_full_option_chain("u1", "NIFTY", "NFO", "16-Jun-2026")

        self.assertEqual(out["Status"], 200)
        self.assertTrue(out["Success"]["from_cache"])
        self.assertIn("chain_fetched_at", out["Success"])
        mock_session.assert_not_called()

    @patch("icici_breeze_backend.app.services.processor.processor._fetch_icici_chain_side_raw")
    def test_get_full_option_chain_force_refresh_bypasses_cache(self, mock_fetch_raw):
        from icici_breeze_backend.app.services.processor import processor as proc

        key = make_chain_cache_key("u1", "NFO", "NIFTY", "16-Jun-2026")
        set_cached_raw_chain(key, [_sample_ce_row()], [_sample_pe_row()])

        mock_fetch_raw.side_effect = [
            {"Status": 200, "Success": [_sample_ce_row(23550)]},
            {"Status": 200, "Success": [_sample_pe_row(23550)]},
        ]

        out = proc.get_full_option_chain(
            "u1", "NIFTY", "NFO", "16-Jun-2026", force_refresh=True
        )

        self.assertEqual(out["Status"], 200)
        self.assertFalse(out["Success"]["from_cache"])
        self.assertEqual(mock_fetch_raw.call_count, 2)


if __name__ == "__main__":
    unittest.main()
