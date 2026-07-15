"""Regression tests for bear call spread optimizer."""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import MagicMock

from icici_breeze_backend.app.services.options_strategy_engine.strategies.income.bear_call_spread import (
    BCS_SPAN_SHORTLIST_N,
    BearCallSpreadCandidate,
    BearCallSpreadRejectionStats,
    _bcs_pop_bucket,
    _pick_top_candidates,
    _unit_span_margin,
    bear_call_spread_short_strikes,
    calc_bear_call_spread,
    enumerate_bear_call_spreads,
    score_bear_call_spread_candidate,
    score_bear_call_spread_ror,
)
from icici_breeze_backend.app.services.options_strategy_engine.types import (
    EngineContext,
    QuoteRow,
    TradeLeg,
)


def _quote(strike: int, right: str, *, bid: float, ask: float, delta: float = 0.20, spot: float = 23623.0) -> QuoteRow:
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


def _ctx(cache: dict, *, min_pop_pct: float = 50.0, min_ann_return_pct: float = 5.0, proc: MagicMock | None = None) -> EngineContext:
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


class TestBearCallSpread(unittest.TestCase):
    def test_rejects_pop_floor(self):
        cache = {
            (23600, "Call"): _quote(23600, "Call", bid=50.0, ask=50.5, delta=0.45),
            (23650, "Call"): _quote(23650, "Call", bid=5.0, ask=5.5, delta=0.35),
        }
        stats = BearCallSpreadRejectionStats()
        out = enumerate_bear_call_spreads(_ctx(cache, min_pop_pct=90.0), 23600, stats=stats)
        self.assertEqual(len(out), 0)
        self.assertGreater(stats.counts.get("pop_floor", 0), 0)

    def test_calc_returns_badges(self):
        # Genuine OTM call ladder above spot (23623): premiums decrease with strike so every
        # bear call spread collects a real credit, and each short strike's P(OTM) clears the
        # 50% floor. (A short strike below spot would be ITM with a sub-50% P(OTM), correctly
        # rejected — see test_rejects_pop_floor.)
        strikes = list(range(23700, 24300, 50))
        bids = {s: max(3.0, 28.0 - (s - 23700) / 12.0) for s in strikes}
        cache = {
            (s, "Call"): _quote(
                s,
                "Call",
                bid=bids[s],
                ask=bids[s] + 0.5,
                delta=max(0.03, 0.10 - (s - 23700) / 20000),
            )
            for s in strikes
        }
        proc = MagicMock()
        proc.strategy_builder_margin.return_value = {"Success": {"span_margin_required": 50_000.0}}
        results = asyncio.run(calc_bear_call_spread(_ctx(cache, min_ann_return_pct=0.0, proc=proc)))
        ok = [r for r in results if r.status == "ok"]
        self.assertGreater(len(ok), 0)
        self.assertTrue(ok[0].badges)

    def test_span_shortlist_bounded(self):
        proc = MagicMock()
        proc.strategy_builder_margin.return_value = {"Success": {"span_margin_required": 50_000.0}}
        ctx = _ctx({}, proc=proc)

        def _cand(short_strike: int, premium: float) -> BearCallSpreadCandidate:
            long_strike = short_strike + 50
            legs = [
                TradeLeg("Call", "Sell", short_strike, 65, 20.0),
                TradeLeg("Call", "Buy", long_strike, 65, 5.0),
            ]
            return BearCallSpreadCandidate(
                short_strike=short_strike,
                long_strike=long_strike,
                wing_width=50,
                credit=15.0,
                max_loss_u=35.0,
                qty=65,
                pop=90.0,
                legs=legs,
                net_collected=premium,
            )

        candidates = [_cand(23600 + i * 50, float(20_000 - i * 500)) for i in range(12)]
        asyncio.run(_pick_top_candidates(ctx, candidates, strategy_id="bear_call_spread"))
        self.assertEqual(proc.strategy_builder_margin.call_count, min(BCS_SPAN_SHORTLIST_N, len(candidates)))


if __name__ == "__main__":
    unittest.main()
