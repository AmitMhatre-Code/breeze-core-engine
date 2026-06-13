"""Regression tests for short strangle optimizer."""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import MagicMock

from icici_breeze_backend.app.services.options_strategy_engine.strategies.income._common import (
    NAKED_ANCHOR_TOP_K,
    pop_band,
)
from icici_breeze_backend.app.services.options_strategy_engine.strategies.income.short_strangle import (
    SS_SPAN_SHORTLIST_N,
    ShortStrangleCandidate,
    ShortStrangleRejectionStats,
    _pick_top_candidates,
    calc_short_strangle,
    enumerate_short_strangles,
    short_strangle_pairs,
)
from icici_breeze_backend.app.services.options_strategy_engine.types import (
    EngineContext,
    QuoteRow,
    TradeLeg,
)


def _quote(strike: int, right: str, *, bid: float, ask: float, delta: float = 0.15, spot: float = 23623.0) -> QuoteRow:
    d = delta if right == "Call" else -delta
    return QuoteRow(
        strike=strike,
        right=right,
        ltp=(bid + ask) / 2,
        best_bid_price=bid,
        best_offer_price=ask,
        total_buy_qty=100,
        total_sell_qty=100,
        buy_sell_ratio=1.0,
        spot_price=spot,
        delta=d,
        liquidity_score=0.9,
        iv=0.15,
    )


def _ctx(cache: dict, *, min_pop_pct: float = 50.0, min_ann_return_pct: float = 0.0, proc: MagicMock | None = None) -> EngineContext:
    return EngineContext(
        processor=proc or MagicMock(),
        user_id="u1",
        stock_code="NIFTY",
        exchange_code="NFO",
        expiry_display="16-Jun-2026",
        margin_rupees=50_000_000,
        max_loss_rupees=4_000_000,
        min_pop_pct=min_pop_pct,
        provision_elm=False,
        strategy_category="income",
        lot_size=65,
        strikes=sorted({s for s, _ in cache}),
        strike_step=50,
        search_interval=50,
        spot=23623.0,
        atm_strike=23600,
        atm_iv=0.15,
        min_ann_return_pct=min_ann_return_pct,
        cache=cache,
    )


class TestShortStrangle(unittest.TestCase):
    def test_rejects_pop_floor(self):
        cache = {
            (23500, "Put"): _quote(23500, "Put", bid=20.0, ask=20.5, delta=0.35),
            (23700, "Call"): _quote(23700, "Call", bid=20.0, ask=20.5, delta=0.35),
        }
        stats = ShortStrangleRejectionStats()
        out = enumerate_short_strangles(_ctx(cache, min_pop_pct=95.0), 23500, 23700, stats=stats)
        self.assertEqual(len(out), 0)

    def test_pairs_use_adaptive_search(self):
        pe = list(range(22800, 23600, 50))
        ce = list(range(23700, 24500, 50))
        cache = {}
        for s in pe:
            cache[(s, "Put")] = _quote(s, "Put", bid=10.0, ask=10.5, delta=0.05)
        for s in ce:
            cache[(s, "Call")] = _quote(s, "Call", bid=10.0, ask=10.5, delta=0.05)
        pairs = short_strangle_pairs(_ctx(cache, min_pop_pct=50.0))
        self.assertGreater(len(pairs), 0)

    def test_calc_returns_objective_badges(self):
        pe = list(range(22800, 23600, 50))
        ce = list(range(23700, 24500, 50))
        cache = {}
        for s in pe:
            cache[(s, "Put")] = _quote(s, "Put", bid=12.0, ask=12.5, delta=0.06)
        for s in ce:
            cache[(s, "Call")] = _quote(s, "Call", bid=12.0, ask=12.5, delta=0.06)
        proc = MagicMock()
        proc.strategy_builder_margin.return_value = {"Success": {"span_margin_required": 80_000.0}}
        results = asyncio.run(calc_short_strangle(_ctx(cache, min_pop_pct=40.0, proc=proc)))
        ok = [r for r in results if r.status == "ok"]
        self.assertGreater(len(ok), 0)
        self.assertTrue(ok[0].badges)

    def test_naked_anchor_constant(self):
        self.assertEqual(NAKED_ANCHOR_TOP_K, 10)


if __name__ == "__main__":
    unittest.main()
