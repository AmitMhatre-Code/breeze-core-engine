"""Regression tests for bear call spread optimizer."""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import MagicMock, patch

from icici_breeze_backend.app.services.options_strategy_engine.strategies.income.bear_call_spread import (
    BCS_RETURN_TOP_N,
    BCS_SHORT_STRIKES_MAX,
    BCS_SPAN_SHORTLIST_N,
    BearCallSpreadCandidate,
    BearCallSpreadRejectionStats,
    _bcs_pop_bucket,
    _collect_candidates,
    _pick_top_candidates,
    _unit_span_margin,
    bear_call_spread_short_strikes,
    build_ranking_summary,
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
        cache=cache,
    )


def _fill_ce_strikes(
    strikes: list[int],
    spot: float,
    *,
    bid_fn,
    ask_fn,
    delta_fn,
) -> dict:
    cache: dict = {}
    for s in strikes:
        cache[(s, "Call")] = _quote(
            s,
            "Call",
            bid=bid_fn(s),
            ask=ask_fn(s),
            delta=delta_fn(s),
            spot=spot,
        )
    return cache


class TestBearCallSpreadShortStrikes(unittest.TestCase):
    def test_short_strikes_bounded(self):
        strikes = list(range(23600, 24700, 50))
        cache = _fill_ce_strikes(
            strikes,
            23622.9,
            bid_fn=lambda s: 8.0,
            ask_fn=lambda s: 8.1,
            delta_fn=lambda s: 0.08,
        )
        ctx = _ctx_from_cache(cache, spot=23622.9, min_pop_pct=65.0)
        short_strikes = bear_call_spread_short_strikes(ctx)
        self.assertGreater(len(short_strikes), 0)
        self.assertLessEqual(len(short_strikes), BCS_SHORT_STRIKES_MAX)
        for s in short_strikes:
            self.assertGreaterEqual(s, ctx.atm_strike)

    def test_pop_bucket_labels(self):
        self.assertEqual(_bcs_pop_bucket(64.0, 65.0), "<65")
        self.assertEqual(_bcs_pop_bucket(65.5, 65.0), "65-66")
        self.assertEqual(_bcs_pop_bucket(67.5, 65.0), "67-68")


class TestEnumerateBearCallSpread(unittest.TestCase):
    def test_rejects_pop_floor(self):
        cache = {
            (23600, "Call"): _quote(23600, "Call", bid=50.0, ask=50.5, delta=0.45),
            (23650, "Call"): _quote(23650, "Call", bid=5.0, ask=5.5, delta=0.35),
        }
        ctx = _ctx_from_cache(cache, spot=23623.0, min_pop_pct=90.0)
        stats = BearCallSpreadRejectionStats()
        variants = enumerate_bear_call_spreads(ctx, 23600, stats=stats)
        self.assertEqual(len(variants), 0)
        self.assertGreater(stats.counts.get("pop_floor", 0), 0)

    def test_accepts_valid_spread(self):
        strikes = list(range(23600, 24200, 50))
        cache = _fill_ce_strikes(
            strikes,
            23623.0,
            bid_fn=lambda s: 20.0 if s == 23600 else 5.0,
            ask_fn=lambda s: 20.5 if s == 23600 else 5.5,
            delta_fn=lambda s: 0.10 if s == 23600 else 0.05,
        )
        ctx = _ctx_from_cache(cache, min_pop_pct=50.0)
        variants = enumerate_bear_call_spreads(ctx, 23600)
        self.assertGreater(len(variants), 0)
        self.assertGreater(variants[0].net_collected, 0)
        self.assertGreaterEqual(variants[0].pop, 50.0)

    def test_multiple_wings_per_short(self):
        strikes = list(range(23600, 24200, 50))
        cache = _fill_ce_strikes(
            strikes,
            23623.0,
            bid_fn=lambda s: 25.0 if s == 23600 else 8.0,
            ask_fn=lambda s: 25.5 if s == 23600 else 8.5,
            delta_fn=lambda s: 0.08 if s == 23600 else 0.04,
        )
        ctx = _ctx_from_cache(cache, min_pop_pct=50.0)
        variants = enumerate_bear_call_spreads(ctx, 23600)
        self.assertGreater(len(variants), 1)


class TestScoreBearCallSpread(unittest.TestCase):
    def test_span_refinement_orders_by_annualized_return(self):
        low_span = score_bear_call_spread_candidate(90.0, 5_000.0, 50_000.0, 4)
        high_span = score_bear_call_spread_candidate(90.0, 5_000.0, 100_000.0, 4)
        self.assertGreater(low_span, high_span)

    def test_ror_tiebreaks_via_liquidity_and_spread(self):
        tight = [
            _quote(23600, "Call", bid=20.0, ask=20.01, liquidity_score=0.95),
            _quote(23650, "Call", bid=5.0, ask=5.01, liquidity_score=0.95),
        ]
        wide = [
            _quote(23600, "Call", bid=20.0, ask=22.0, liquidity_score=0.4),
            _quote(23650, "Call", bid=5.0, ask=7.0, liquidity_score=0.4),
        ]
        tight_score, _ = score_bear_call_spread_ror(90.0, 5_000.0, 10_000.0, 90.0, tight)
        wide_score, _ = score_bear_call_spread_ror(90.0, 5_000.0, 10_000.0, 90.0, wide)
        self.assertGreater(tight_score, wide_score)


class TestRankingSummary(unittest.TestCase):
    def test_ranking_summary_mentions_span_yield(self):
        summary = build_ranking_summary(
            higher_rank=1,
            lower_rank=2,
            viewing_rank=1,
            higher_ann_return=219.0,
            higher_credit=5_000.0,
            higher_pop=90.0,
            lower_ann_return=137.0,
            lower_credit=6_000.0,
            lower_pop=90.0,
        )
        self.assertIn("Ranked #1 over #2:", summary)
        self.assertIn("annualized return on SPAN", summary)


class TestCalcBearCallSpread(unittest.TestCase):
    def test_returns_top_variants_with_ranks(self):
        strikes = list(range(23600, 24700, 50))
        cache = _fill_ce_strikes(
            strikes,
            23623.0,
            bid_fn=lambda s: 20.0 if s <= 23700 else 8.0,
            ask_fn=lambda s: 20.5 if s <= 23700 else 8.5,
            delta_fn=lambda s: 0.08 if s <= 23700 else 0.04,
        )
        proc = MagicMock()
        proc.strategy_builder_margin.return_value = {"Success": {"span_margin_required": 50_000.0}}
        ctx = _ctx_from_cache(cache, min_pop_pct=50.0, processor=proc)

        with patch(
            "icici_breeze_backend.app.services.options_strategy_engine.strategies.income.bear_call_spread.MIN_BCS_ANNUALIZED_RETURN_PCT",
            0.0,
        ):
            results = asyncio.run(calc_bear_call_spread(ctx))

        ok = [r for r in results if r.status == "ok"]
        self.assertGreater(len(ok), 0)
        self.assertEqual(ok[0].variant_rank, 1)
        self.assertIsNotNone(ok[0].engine_score)

    def test_skips_when_annualized_return_below_minimum(self):
        strikes = list(range(23600, 24700, 50))
        cache = _fill_ce_strikes(
            strikes,
            23623.0,
            bid_fn=lambda s: 20.0 if s <= 23700 else 8.0,
            ask_fn=lambda s: 20.5 if s <= 23700 else 8.5,
            delta_fn=lambda s: 0.20 if s <= 23700 else 0.10,
        )
        proc = MagicMock()
        proc.strategy_builder_margin.return_value = {
            "Success": {"span_margin_required": 500_000.0}
        }
        ctx = _ctx_from_cache(cache, min_pop_pct=30.0, processor=proc)
        with patch(
            "icici_breeze_backend.app.services.options_strategy_engine.strategies.income.bear_call_spread.MIN_BCS_ANNUALIZED_RETURN_PCT",
            500.0,
        ):
            results = asyncio.run(calc_bear_call_spread(ctx))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, "skipped")
        self.assertIn("annualized return", (results[0].skip_reason or "").lower())

    def test_span_refinement_picks_lower_margin_structure(self):
        proc = MagicMock()

        def margin(user_id, exchange, legs, audit=None, audit_context=None):
            short_strike = int(
                next(
                    leg["strike_price"]
                    for leg in legs
                    if leg["right"] == "Call" and leg["action"] == "Sell"
                )
            )
            span = 40_000.0 if short_strike == 23600 else 90_000.0
            return {"Success": {"span_margin_required": span}}

        proc.strategy_builder_margin.side_effect = margin
        ctx = _ctx_from_cache({}, spot=23623.0, processor=proc)
        ctx.strikes = [23600, 23650, 23700, 23750]

        def _cand(short_strike: int, long_strike: int, premium: float) -> BearCallSpreadCandidate:
            trade_legs = [
                TradeLeg("Call", "Sell", short_strike, 65, 20.0),
                TradeLeg("Call", "Buy", long_strike, 65, 5.0),
            ]
            return BearCallSpreadCandidate(
                short_strike=short_strike,
                long_strike=long_strike,
                wing_width=long_strike - short_strike,
                credit=15.0,
                max_loss_u=35.0,
                qty=65,
                pop=90.0,
                legs=trade_legs,
                net_collected=premium,
                final_score=1.0,
                score_factors={"ror": 1.0, "liquidity_weight": 0.9, "spread_weight": 0.9},
            )

        high_credit = _cand(23700, 23750, 10_000.0)
        low_span = _cand(23600, 23650, 10_000.0)

        winners, scores = asyncio.run(
            _pick_top_candidates(
                ctx,
                [high_credit, low_span],
                strategy_id="bear_call_spread",
                span_shortlist_n=2,
                return_top_n=2,
            )
        )
        self.assertEqual(len(winners), 2)
        winner, best_return = winners[0]
        self.assertEqual(winner.short_strike, 23600)
        self.assertGreater(best_return, 0)
        self.assertEqual(len(scores), 2)

    def test_credit_shortlist_margins_only_top_n(self):
        proc = MagicMock()
        proc.strategy_builder_margin.return_value = {
            "Success": {"span_margin_required": 50_000.0}
        }
        ctx = _ctx_from_cache({}, processor=proc)

        def _cand(short_strike: int, premium: float) -> BearCallSpreadCandidate:
            long_strike = short_strike + 50
            trade_legs = [
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
                legs=trade_legs,
                net_collected=premium,
                final_score=1.0,
                score_factors={"ror": 1.0, "liquidity_weight": 0.9, "spread_weight": 0.9},
            )

        candidates = [_cand(23600 + i * 50, float(20_000 - i * 500)) for i in range(12)]
        asyncio.run(_pick_top_candidates(ctx, candidates, strategy_id="bear_call_spread"))
        self.assertEqual(
            proc.strategy_builder_margin.call_count,
            min(BCS_SPAN_SHORTLIST_N, len(candidates)),
        )

    def test_span_shortlist_returns_top_3_after_rerank(self):
        proc = MagicMock()
        proc.strategy_builder_margin.return_value = {
            "Success": {"span_margin_required": 50_000.0}
        }
        ctx = _ctx_from_cache({}, processor=proc)

        def _cand(short_strike: int, premium: float) -> BearCallSpreadCandidate:
            long_strike = short_strike + 50
            trade_legs = [
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
                legs=trade_legs,
                net_collected=premium,
                final_score=1.0,
                score_factors={"ror": 1.0, "liquidity_weight": 0.9, "spread_weight": 0.9},
            )

        candidates = [_cand(23600 + i * 50, float(20_000 - i * 500)) for i in range(12)]
        winners, span_scores = asyncio.run(
            _pick_top_candidates(ctx, candidates, strategy_id="bear_call_spread")
        )
        self.assertEqual(len(span_scores), BCS_SPAN_SHORTLIST_N)
        self.assertEqual(len(winners), BCS_RETURN_TOP_N)

    def test_unit_span_margin_uses_session_cache(self):
        proc = MagicMock()
        proc.strategy_builder_margin.return_value = {
            "Success": {"span_margin_required": 42_000.0}
        }
        ctx = _ctx_from_cache({}, processor=proc)
        legs = [
            TradeLeg("Call", "Sell", 23600, 65, 20.0),
            TradeLeg("Call", "Buy", 23650, 65, 5.0),
        ]
        span_a = _unit_span_margin(ctx, legs, strategy_id="bear_call_spread")
        span_b = _unit_span_margin(ctx, legs, strategy_id="bear_call_spread")
        self.assertEqual(span_a, 42_000.0)
        self.assertEqual(span_b, 42_000.0)
        self.assertEqual(proc.strategy_builder_margin.call_count, 1)


class TestBearCallSpreadAudit(unittest.TestCase):
    def test_enumeration_logs_evaluations(self):
        cache = {
            (23600, "Call"): _quote(23600, "Call", bid=50.0, ask=50.5, delta=0.45),
            (23650, "Call"): _quote(23650, "Call", bid=5.0, ask=5.5, delta=0.35),
        }
        ctx = _ctx_from_cache(cache, spot=23623.0, min_pop_pct=50.0)
        stats = BearCallSpreadRejectionStats()
        enumerate_bear_call_spreads(ctx, 23600, stats=stats)
        self.assertGreater(len(stats.evaluations), 0)

    def test_collect_candidates_populates_stats(self):
        strikes = list(range(23600, 24200, 50))
        cache = _fill_ce_strikes(
            strikes,
            23623.0,
            bid_fn=lambda s: 20.0 if s == 23600 else 8.0,
            ask_fn=lambda s: 20.5 if s == 23600 else 8.5,
            delta_fn=lambda s: 0.08 if s == 23600 else 0.04,
        )
        ctx = _ctx_from_cache(cache, min_pop_pct=50.0)
        stats = BearCallSpreadRejectionStats()
        short_strikes = bear_call_spread_short_strikes(ctx)
        candidates = _collect_candidates(ctx, short_strikes, stats=stats)
        self.assertGreater(len(candidates), 0)
        self.assertGreater(len(stats.evaluations), 0)
        self.assertGreater(len(stats.survivors_by_pop_bucket), 0)


if __name__ == "__main__":
    unittest.main()
