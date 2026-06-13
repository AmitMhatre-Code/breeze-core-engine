"""Regression tests for short strangle optimizer."""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import MagicMock, patch

from icici_breeze_backend.app.services.options_strategy_engine.strategies.income.short_strangle import (
    SS_RETURN_TOP_N,
    SS_SPAN_SHORTLIST_N,
    SS_SHORT_STRIKES_MAX_ATM,
    SS_SHORT_STRIKES_MAX_PER_WING,
    ShortStrangleCandidate,
    ShortStrangleRejectionStats,
    _collect_candidates,
    _pick_top_candidates,
    _ss_pop_bucket,
    _unit_span_margin,
    build_ranking_summary,
    calc_short_strangle,
    enumerate_short_strangles,
    score_short_strangle_candidate,
    score_short_strangle_ror,
    short_strangle_pairs,
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


def _fill_strikes(
    strikes: list[int],
    spot: float,
    *,
    bid_fn,
    ask_fn,
    delta_fn,
) -> dict:
    cache: dict = {}
    for s in strikes:
        for right in ("Call", "Put"):
            cache[(s, right)] = _quote(
                s,
                right,
                bid=bid_fn(s, right),
                ask=ask_fn(s, right),
                delta=delta_fn(s, right),
                spot=spot,
            )
    return cache


class TestShortStranglePairs(unittest.TestCase):
    def test_short_strangle_pairs_bounded(self):
        strikes = list(range(22500, 24700, 50))
        cache = _fill_strikes(
            strikes,
            23622.9,
            bid_fn=lambda s, r: 4.0,
            ask_fn=lambda s, r: 4.1,
            delta_fn=lambda s, r: 0.08,
        )
        ctx = _ctx_from_cache(cache, spot=23622.9, min_pop_pct=95.0)
        pairs = short_strangle_pairs(ctx)
        self.assertGreater(len(pairs), 0)
        self.assertLessEqual(len(pairs), SS_SHORT_STRIKES_MAX_PER_WING * SS_SHORT_STRIKES_MAX_PER_WING)

    def test_pop_bucket_labels(self):
        self.assertEqual(_ss_pop_bucket(94.0, 95.0), "<95")
        self.assertEqual(_ss_pop_bucket(95.5, 95.0), "95-96")
        self.assertEqual(_ss_pop_bucket(97.5, 95.0), "97-98")


class TestEnumerateShortStrangle(unittest.TestCase):
    def test_rejects_pop_floor(self):
        cache = {
            (22850, "Put"): _quote(22850, "Put", bid=6.80, ask=6.95, delta=0.02),
            (24400, "Call"): _quote(24400, "Call", bid=5.40, ask=5.55, delta=0.02),
        }
        ctx = _ctx_from_cache(cache, spot=23622.9, min_pop_pct=99.0)
        stats = ShortStrangleRejectionStats()
        variants = enumerate_short_strangles(ctx, 22850, 24400, stats=stats)
        self.assertEqual(len(variants), 0)
        self.assertGreater(stats.counts.get("pop_floor", 0), 0)

    def test_accepts_valid_pair(self):
        strikes = list(range(22600, 24700, 50))
        cache = _fill_strikes(
            strikes,
            23623.0,
            bid_fn=lambda s, r: 8.0,
            ask_fn=lambda s, r: 8.1,
            delta_fn=lambda s, r: 0.05,
        )
        ctx = _ctx_from_cache(cache, min_pop_pct=50.0)
        variants = enumerate_short_strangles(ctx, 22800, 24400)
        self.assertEqual(len(variants), 1)
        self.assertGreater(variants[0].net_collected, 0)
        self.assertGreaterEqual(variants[0].pop, 50.0)


class TestScoreShortStrangle(unittest.TestCase):
    def test_span_refinement_orders_by_annualized_return(self):
        low_span = score_short_strangle_candidate(95.0, 10_000.0, 50_000.0, 4)
        high_span = score_short_strangle_candidate(95.0, 10_000.0, 100_000.0, 4)
        self.assertGreater(low_span, high_span)

    def test_ror_tiebreaks_via_liquidity_and_spread(self):
        tight = [
            _quote(22800, "Put", bid=10.0, ask=10.01, liquidity_score=0.95),
            _quote(24400, "Call", bid=10.0, ask=10.01, liquidity_score=0.95),
        ]
        wide = [
            _quote(22800, "Put", bid=10.0, ask=11.0, liquidity_score=0.4),
            _quote(24400, "Call", bid=10.0, ask=11.0, liquidity_score=0.4),
        ]
        tight_score, _ = score_short_strangle_ror(95.0, 5_000.0, 95.0, tight)
        wide_score, _ = score_short_strangle_ror(95.0, 5_000.0, 95.0, wide)
        self.assertGreater(tight_score, wide_score)


class TestRankingSummary(unittest.TestCase):
    def test_ranking_summary_mentions_span_yield(self):
        summary = build_ranking_summary(
            higher_rank=1,
            lower_rank=2,
            viewing_rank=1,
            higher_ann_return=219.0,
            higher_credit=12_000.0,
            higher_pop=95.0,
            lower_ann_return=137.0,
            lower_credit=15_000.0,
            lower_pop=95.0,
        )
        self.assertIn("Ranked #1 over #2:", summary)
        self.assertIn("annualized return on SPAN", summary)
        self.assertIn("net credit per lot", summary)


class TestCalcShortStrangle(unittest.TestCase):
    def test_returns_top_variants_with_ranks(self):
        strikes = list(range(22600, 24700, 50))
        cache = _fill_strikes(
            strikes,
            23623.0,
            bid_fn=lambda s, r: 8.0,
            ask_fn=lambda s, r: 8.1,
            delta_fn=lambda s, r: 0.05,
        )
        proc = MagicMock()
        proc.strategy_builder_margin.return_value = {"Success": {"span_margin_required": 50_000.0}}
        ctx = _ctx_from_cache(cache, min_pop_pct=50.0, processor=proc)

        with patch(
            "icici_breeze_backend.app.services.options_strategy_engine.strategies.income.short_strangle.MIN_SS_ANNUALIZED_RETURN_PCT",
            0.0,
        ):
            results = asyncio.run(calc_short_strangle(ctx))

        ok = [r for r in results if r.status == "ok"]
        self.assertGreater(len(ok), 1)
        self.assertEqual(ok[0].variant_rank, 1)
        self.assertIsNotNone(ok[0].engine_score)
        if len(ok) > 1:
            self.assertEqual(ok[1].variant_rank, 2)
            self.assertIsNotNone(ok[1].ranking_summary)

    def test_skips_when_annualized_return_below_minimum(self):
        strikes = list(range(22700, 24700, 50))
        cache = _fill_strikes(
            strikes,
            23623.0,
            bid_fn=lambda s, r: 8.0,
            ask_fn=lambda s, r: 8.1,
            delta_fn=lambda s, r: 0.20,
        )
        proc = MagicMock()
        proc.strategy_builder_margin.return_value = {
            "Success": {"span_margin_required": 500_000.0}
        }
        ctx = _ctx_from_cache(cache, min_pop_pct=30.0, processor=proc)
        with patch(
            "icici_breeze_backend.app.services.options_strategy_engine.strategies.income.short_strangle.MIN_SS_ANNUALIZED_RETURN_PCT",
            500.0,
        ):
            results = asyncio.run(calc_short_strangle(ctx))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, "skipped")
        self.assertIn("annualized return", (results[0].skip_reason or "").lower())

    def test_span_refinement_picks_lower_margin_structure(self):
        proc = MagicMock()

        def margin(user_id, exchange, legs, audit=None, audit_context=None):
            short_put = next(
                leg["strike_price"] for leg in legs if leg["right"] == "Put" and leg["action"] == "Sell"
            )
            span = 40_000.0 if short_put == "22900" else 90_000.0
            return {"Success": {"span_margin_required": span}}

        proc.strategy_builder_margin.side_effect = margin
        ctx = _ctx_from_cache({}, spot=23623.0, processor=proc)
        ctx.strikes = [22800, 22900, 24400]

        def _cand(short_put: int, final: float, premium: float) -> ShortStrangleCandidate:
            trade_legs = [
                TradeLeg("Put", "Sell", short_put, 65, 8.0),
                TradeLeg("Call", "Sell", 24400, 65, 8.0),
            ]
            return ShortStrangleCandidate(
                short_put=short_put,
                short_call=24400,
                credit=16.0,
                qty=65,
                pop=95.0,
                legs=trade_legs,
                net_collected=premium,
                final_score=final,
                score_factors={"ror": 1.0, "liquidity_weight": 0.9, "spread_weight": 0.9},
            )

        high_score = _cand(23250, final=100.0, premium=10_000.0)
        low_span = _cand(22900, final=50.0, premium=10_000.0)

        winners, scores = asyncio.run(
            _pick_top_candidates(
            ctx,
            [high_score, low_span],
            strategy_id="short_strangle",
            span_shortlist_n=2,
            return_top_n=2,
        ))
        self.assertEqual(len(winners), 2)
        winner, best_return = winners[0]
        self.assertEqual(winner.short_put, 22900)
        self.assertGreater(best_return, 0)
        self.assertEqual(len(scores), 2)

    def test_final_rank_prefers_span_yield_over_higher_credit(self):
        proc = MagicMock()

        def margin(user_id, exchange, legs, audit=None, audit_context=None):
            short_put = next(
                leg["strike_price"] for leg in legs if leg["right"] == "Put" and leg["action"] == "Sell"
            )
            span = 50_000.0 if short_put == "22900" else 100_000.0
            return {"Success": {"span_margin_required": span}}

        proc.strategy_builder_margin.side_effect = margin
        ctx = _ctx_from_cache({}, spot=23623.0, processor=proc)
        ctx.strikes = [22800, 22900, 24400]

        def _cand(short_put: int, premium: float) -> ShortStrangleCandidate:
            trade_legs = [
                TradeLeg("Put", "Sell", short_put, 65, 8.0),
                TradeLeg("Call", "Sell", 24400, 65, 8.0),
            ]
            return ShortStrangleCandidate(
                short_put=short_put,
                short_call=24400,
                credit=16.0,
                qty=65,
                pop=95.0,
                legs=trade_legs,
                net_collected=premium,
                final_score=1.0,
                score_factors={"ror": 1.0, "liquidity_weight": 0.9, "spread_weight": 0.9},
            )

        high_credit = _cand(23250, premium=15_000.0)
        better_yield = _cand(22900, premium=12_000.0)

        winners, _ = asyncio.run(
            _pick_top_candidates(
            ctx,
            [high_credit, better_yield],
            strategy_id="short_strangle",
            span_shortlist_n=2,
            return_top_n=2,
        ))
        winner, best_return = winners[0]
        self.assertEqual(winner.short_put, 22900)
        self.assertGreater(best_return, score_short_strangle_candidate(95.0, 15_000.0, 100_000.0, 4))

    def test_credit_shortlist_ignores_proxy_return(self):
        proc = MagicMock()
        margin_calls: list[str] = []

        def margin(user_id, exchange, legs, audit=None, audit_context=None):
            short_put = next(
                leg["strike_price"] for leg in legs if leg["right"] == "Put" and leg["action"] == "Sell"
            )
            margin_calls.append(short_put)
            span = 40_000.0 if short_put == "22900" else 200_000.0
            return {"Success": {"span_margin_required": span}}

        proc.strategy_builder_margin.side_effect = margin
        ctx = _ctx_from_cache({}, spot=23623.0, processor=proc)

        def _cand(short_put: int, premium: float) -> ShortStrangleCandidate:
            trade_legs = [
                TradeLeg("Put", "Sell", short_put, 65, 8.0),
                TradeLeg("Call", "Sell", 24400, 65, 8.0),
            ]
            return ShortStrangleCandidate(
                short_put=short_put,
                short_call=24400,
                credit=16.0,
                qty=65,
                pop=95.0,
                legs=trade_legs,
                net_collected=premium,
                final_score=1.0,
                score_factors={"ror": 1.0, "liquidity_weight": 0.9, "spread_weight": 0.9},
            )

        # 12 candidates: credit decreases with strike; 22900 has best yield but mid credit.
        candidates = [_cand(22800 + i * 50, float(20_000 - i * 500)) for i in range(12)]
        asyncio.run(_pick_top_candidates(ctx, candidates, strategy_id="short_strangle"))

        self.assertEqual(len(margin_calls), SS_SPAN_SHORTLIST_N)
        self.assertNotIn("23300", margin_calls)
        self.assertNotIn("23350", margin_calls)
        self.assertIn("22900", margin_calls)

    def test_pick_top_candidates_margins_only_shortlist(self):
        proc = MagicMock()
        proc.strategy_builder_margin.return_value = {
            "Success": {"span_margin_required": 50_000.0}
        }
        ctx = _ctx_from_cache({}, processor=proc)

        def _cand(short_put: int, final: float) -> ShortStrangleCandidate:
            trade_legs = [
                TradeLeg("Put", "Sell", short_put, 65, 8.0),
                TradeLeg("Call", "Sell", 24400, 65, 8.0),
            ]
            return ShortStrangleCandidate(
                short_put=short_put,
                short_call=24400,
                credit=16.0,
                qty=65,
                pop=95.0,
                legs=trade_legs,
                net_collected=float(10_000 - (short_put - 22800) // 50 * 500),
                final_score=final,
                score_factors={"ror": 1.0, "liquidity_weight": 0.9, "spread_weight": 0.9},
            )

        candidates = [_cand(22800 + i * 50, float(100 - i)) for i in range(12)]
        asyncio.run(_pick_top_candidates(ctx, candidates, strategy_id="short_strangle"))
        self.assertEqual(
            proc.strategy_builder_margin.call_count,
            min(SS_SPAN_SHORTLIST_N, len(candidates)),
        )

    def test_span_shortlist_10_returns_top_3_after_rerank(self):
        proc = MagicMock()

        def margin(user_id, exchange, legs, audit=None, audit_context=None):
            short_put = int(
                next(
                    leg["strike_price"]
                    for leg in legs
                    if leg["right"] == "Put" and leg["action"] == "Sell"
                )
            )
            # Lower SPAN for mid-credit strike; highest credit has heavy margin.
            if short_put == 22900:
                span = 45_000.0
            elif short_put == 22800:
                span = 120_000.0
            else:
                span = 80_000.0
            return {"Success": {"span_margin_required": span}}

        proc.strategy_builder_margin.side_effect = margin
        ctx = _ctx_from_cache({}, spot=23623.0, processor=proc)

        def _cand(short_put: int, premium: float) -> ShortStrangleCandidate:
            trade_legs = [
                TradeLeg("Put", "Sell", short_put, 65, 8.0),
                TradeLeg("Call", "Sell", 24400, 65, 8.0),
            ]
            return ShortStrangleCandidate(
                short_put=short_put,
                short_call=24400,
                credit=16.0,
                qty=65,
                pop=95.0,
                legs=trade_legs,
                net_collected=premium,
                final_score=1.0,
                score_factors={"ror": 1.0, "liquidity_weight": 0.9, "spread_weight": 0.9},
            )

        # 12 candidates: 22800 has top credit but worst yield; 22900 wins on SPAN yield.
        candidates = [_cand(22800 + i * 50, float(20_000 - i * 500)) for i in range(12)]
        winners, span_scores = asyncio.run(
            _pick_top_candidates(ctx, candidates, strategy_id="short_strangle")
        )

        self.assertEqual(len(span_scores), SS_SPAN_SHORTLIST_N)
        self.assertEqual(len(winners), SS_RETURN_TOP_N)
        winner, best_return = winners[0]
        self.assertEqual(winner.short_put, 22900)
        self.assertGreater(
            best_return,
            score_short_strangle_candidate(95.0, 20_000.0, 120_000.0, 4),
        )

    def test_unit_span_margin_uses_session_cache(self):
        proc = MagicMock()
        proc.strategy_builder_margin.return_value = {
            "Success": {"span_margin_required": 42_000.0}
        }
        ctx = _ctx_from_cache({}, processor=proc)
        legs = [
            TradeLeg("Put", "Sell", 22800, 65, 8.0),
            TradeLeg("Call", "Sell", 24400, 65, 8.0),
        ]
        span_a = _unit_span_margin(ctx, legs, strategy_id="short_strangle")
        span_b = _unit_span_margin(ctx, legs, strategy_id="short_strangle")
        self.assertEqual(span_a, 42_000.0)
        self.assertEqual(span_b, 42_000.0)
        self.assertEqual(proc.strategy_builder_margin.call_count, 1)


class TestShortStrangleAudit(unittest.TestCase):
    def test_enumeration_logs_evaluations(self):
        cache = {
            (22850, "Put"): _quote(22850, "Put", bid=6.80, ask=6.95, delta=0.02),
            (24400, "Call"): _quote(24400, "Call", bid=5.40, ask=5.55, delta=0.02),
        }
        ctx = _ctx_from_cache(cache, spot=23622.9, min_pop_pct=50.0)
        stats = ShortStrangleRejectionStats()
        enumerate_short_strangles(ctx, 22850, 24400, stats=stats)
        self.assertGreater(len(stats.evaluations), 0)
        for ev in stats.evaluations:
            if ev["outcome"] == "accepted":
                self.assertEqual(ev["pop_basis"], "breakevens_from_short_strikes")
                self.assertIsNotNone(ev["pop_pct"])

    def test_collect_candidates_populates_stats(self):
        strikes = list(range(22600, 24700, 50))
        cache = _fill_strikes(
            strikes,
            23623.0,
            bid_fn=lambda s, r: 8.0,
            ask_fn=lambda s, r: 8.1,
            delta_fn=lambda s, r: 0.05,
        )
        ctx = _ctx_from_cache(cache, min_pop_pct=50.0)
        stats = ShortStrangleRejectionStats()
        pairs = short_strangle_pairs(ctx)
        candidates = _collect_candidates(ctx, pairs, stats=stats)
        self.assertGreater(len(candidates), 0)
        self.assertGreater(len(stats.evaluations), 0)
        self.assertGreater(len(stats.survivors_by_pop_bucket), 0)


if __name__ == "__main__":
    unittest.main()
