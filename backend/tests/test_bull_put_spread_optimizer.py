"""Regression tests for bull put spread optimizer."""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import MagicMock

from icici_breeze_backend.app.services.options_strategy_engine.strategies.income._common import (
    BADGE_INCOME,
    SPAN_SHORTLIST_N,
    pop_band,
    select_objective_champions,
)
from icici_breeze_backend.app.services.options_strategy_engine.strategies.income.bull_put_spread import (
    BPS_SPAN_SHORTLIST_N,
    BullPutSpreadCandidate,
    BullPutSpreadRejectionStats,
    _bps_pop_bucket,
    _collect_candidates,
    _pick_top_candidates,
    _unit_span_margin,
    bull_put_spread_short_strikes,
    calc_bull_put_spread,
    enumerate_bull_put_spreads,
    score_bull_put_spread_candidate,
    score_bull_put_spread_ror,
)
from icici_breeze_backend.app.services.options_strategy_engine.types import (
    EngineContext,
    QuoteRow,
    TradeLeg,
)


def _quote(
    strike: int,
    right: str,
    *,
    bid: float,
    ask: float,
    delta: float | None = 0.20,
    spot: float = 23623.0,
    liquidity_score: float = 0.9,
) -> QuoteRow:
    d = delta if right == "Call" else (-delta if delta is not None else None)
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
        liquidity_score=liquidity_score,
        iv=0.15,
    )


def _ctx_from_cache(
    cache: dict,
    *,
    spot: float = 23623.0,
    min_pop_pct: float = 65.0,
    min_ann_return_pct: float = 5.0,
    margin_rupees: float = 50_000_000,
    max_loss_rupees: float = 4_000_000,
    processor: MagicMock | None = None,
) -> EngineContext:
    strikes = sorted({s for s, _ in cache})
    return EngineContext(
        processor=processor or MagicMock(),
        user_id="u1",
        stock_code="NIFTY",
        exchange_code="NFO",
        expiry_display="16-Jun-2026",
        margin_rupees=margin_rupees,
        max_loss_rupees=max_loss_rupees,
        min_pop_pct=min_pop_pct,
        provision_elm=False,
        strategy_category="income",
        lot_size=65,
        strikes=strikes,
        strike_step=50,
        search_interval=50,
        spot=spot,
        atm_strike=23600,
        atm_iv=0.15,
        min_ann_return_pct=min_ann_return_pct,
        cache=cache,
    )


def _fill_pe_strikes(
    strikes: list[int],
    spot: float,
    *,
    bid_fn,
    ask_fn,
    delta_fn,
) -> dict:
    cache: dict = {}
    for s in strikes:
        cache[(s, "Put")] = _quote(
            s,
            "Put",
            bid=bid_fn(s),
            ask=ask_fn(s),
            delta=delta_fn(s),
            spot=spot,
        )
    return cache


class TestBullPutSpreadShortStrikes(unittest.TestCase):
    def test_short_strikes_pop_aware(self):
        strikes = list(range(22500, 23700, 50))
        cache = _fill_pe_strikes(
            strikes,
            23622.9,
            bid_fn=lambda s: 8.0,
            ask_fn=lambda s: 8.1,
            delta_fn=lambda s: 0.08,
        )
        ctx = _ctx_from_cache(cache, spot=23622.9, min_pop_pct=65.0)
        short_strikes = bull_put_spread_short_strikes(ctx)
        self.assertGreater(len(short_strikes), 0)
        for s in short_strikes:
            self.assertLessEqual(s, ctx.atm_strike)

    def test_pop_bucket_labels(self):
        self.assertEqual(_bps_pop_bucket(64.0, 65.0), "<65")
        self.assertEqual(_bps_pop_bucket(65.5, 65.0), "65-66")


class TestEnumerateBullPutSpread(unittest.TestCase):
    def test_rejects_pop_floor(self):
        cache = {
            (23600, "Put"): _quote(23600, "Put", bid=50.0, ask=50.5, delta=0.45),
            (23550, "Put"): _quote(23550, "Put", bid=5.0, ask=5.5, delta=0.35),
        }
        ctx = _ctx_from_cache(cache, spot=23623.0, min_pop_pct=90.0)
        stats = BullPutSpreadRejectionStats()
        variants = enumerate_bull_put_spreads(ctx, 23600, stats=stats)
        self.assertEqual(len(variants), 0)
        self.assertGreater(stats.counts.get("pop_floor", 0), 0)

    def test_accepts_valid_spread(self):
        strikes = list(range(23000, 23700, 50))
        cache = _fill_pe_strikes(
            strikes,
            23623.0,
            bid_fn=lambda s: 20.0 if s == 23600 else 5.0,
            ask_fn=lambda s: 20.5 if s == 23600 else 5.5,
            delta_fn=lambda s: 0.10 if s == 23600 else 0.05,
        )
        ctx = _ctx_from_cache(cache, min_pop_pct=50.0)
        variants = enumerate_bull_put_spreads(ctx, 23600)
        self.assertGreater(len(variants), 0)
        self.assertGreater(variants[0].net_collected, 0)
        self.assertGreaterEqual(variants[0].pop, 50.0)


class TestScoreBullPutSpread(unittest.TestCase):
    def test_span_refinement_orders_by_annualized_return(self):
        low_span = score_bull_put_spread_candidate(90.0, 5_000.0, 50_000.0, 4)
        high_span = score_bull_put_spread_candidate(90.0, 5_000.0, 100_000.0, 4)
        self.assertGreater(low_span, high_span)

    def test_ror_prefers_liquidity_and_spread(self):
        tight = [
            _quote(23600, "Put", bid=20.0, ask=20.01, liquidity_score=0.95),
            _quote(23550, "Put", bid=5.0, ask=5.01, liquidity_score=0.95),
        ]
        wide = [
            _quote(23600, "Put", bid=20.0, ask=22.0, liquidity_score=0.4),
            _quote(23550, "Put", bid=5.0, ask=7.0, liquidity_score=0.4),
        ]
        tight_score, _ = score_bull_put_spread_ror(5_000.0, 10_000.0, tight)
        wide_score, _ = score_bull_put_spread_ror(5_000.0, 10_000.0, wide)
        self.assertGreater(tight_score, wide_score)


class TestCalcBullPutSpread(unittest.TestCase):
    def test_returns_champions_with_badges(self):
        strikes = list(range(22500, 23700, 50))
        cache = _fill_pe_strikes(
            strikes,
            23623.0,
            bid_fn=lambda s: 20.0 if s >= 23500 else 8.0,
            ask_fn=lambda s: 20.5 if s >= 23500 else 8.5,
            delta_fn=lambda s: 0.08 if s >= 23500 else 0.04,
        )
        proc = MagicMock()
        proc.strategy_builder_margin.return_value = {"Success": {"span_margin_required": 50_000.0}}
        ctx = _ctx_from_cache(cache, min_pop_pct=50.0, min_ann_return_pct=0.0, processor=proc)

        results = asyncio.run(calc_bull_put_spread(ctx))

        ok = [r for r in results if r.status == "ok"]
        self.assertGreater(len(ok), 0)
        self.assertEqual(ok[0].variant_rank, 1)
        self.assertTrue(ok[0].badges)

    def test_skips_when_annualized_return_below_minimum(self):
        strikes = list(range(22500, 23700, 50))
        cache = _fill_pe_strikes(
            strikes,
            23623.0,
            bid_fn=lambda s: 20.0 if s >= 23500 else 8.0,
            ask_fn=lambda s: 20.5 if s >= 23500 else 8.5,
            delta_fn=lambda s: 0.20 if s >= 23500 else 0.10,
        )
        proc = MagicMock()
        proc.strategy_builder_margin.return_value = {
            "Success": {"span_margin_required": 500_000.0}
        }
        ctx = _ctx_from_cache(
            cache, min_pop_pct=30.0, min_ann_return_pct=500.0, processor=proc
        )
        results = asyncio.run(calc_bull_put_spread(ctx))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, "skipped")
        self.assertIn("annualized return", (results[0].skip_reason or "").lower())

    def test_span_shortlist_margins_only_top_n(self):
        proc = MagicMock()
        proc.strategy_builder_margin.return_value = {
            "Success": {"span_margin_required": 50_000.0}
        }
        ctx = _ctx_from_cache({}, processor=proc)

        def _cand(short_strike: int, premium: float) -> BullPutSpreadCandidate:
            long_strike = short_strike - 50
            trade_legs = [
                TradeLeg("Put", "Sell", short_strike, 65, 20.0),
                TradeLeg("Put", "Buy", long_strike, 65, 5.0),
            ]
            return BullPutSpreadCandidate(
                short_strike=short_strike,
                long_strike=long_strike,
                wing_width=50,
                credit=15.0,
                max_loss_u=35.0,
                qty=65,
                pop=90.0,
                legs=trade_legs,
                net_collected=premium,
            )

        candidates = [_cand(23600 - i * 50, float(20_000 - i * 500)) for i in range(12)]
        asyncio.run(_pick_top_candidates(ctx, candidates, strategy_id="bull_put_spread"))
        self.assertEqual(
            proc.strategy_builder_margin.call_count,
            min(BPS_SPAN_SHORTLIST_N, len(candidates)),
        )

    def test_unit_span_margin_uses_session_cache(self):
        proc = MagicMock()
        proc.strategy_builder_margin.return_value = {
            "Success": {"span_margin_required": 42_000.0}
        }
        ctx = _ctx_from_cache({}, processor=proc)
        legs = [
            TradeLeg("Put", "Sell", 23600, 65, 20.0),
            TradeLeg("Put", "Buy", 23550, 65, 5.0),
        ]
        span_a = _unit_span_margin(ctx, legs, strategy_id="bull_put_spread")
        span_b = _unit_span_margin(ctx, legs, strategy_id="bull_put_spread")
        self.assertEqual(span_a, 42_000.0)
        self.assertEqual(span_b, 42_000.0)
        self.assertEqual(proc.strategy_builder_margin.call_count, 1)


class TestObjectiveChampions(unittest.TestCase):
    def test_pop_band_adaptive_width(self):
        self.assertEqual(pop_band(25.0), 10.0)
        self.assertEqual(pop_band(50.0), 5.0)
        self.assertEqual(pop_band(95.0), 2.0)


if __name__ == "__main__":
    unittest.main()
