"""Regression tests for iron condor optimizer revamp."""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import MagicMock, patch

from icici_breeze_backend.app.services.options_strategy_engine.budget_resize import resize_results_to_budgets
from icici_breeze_backend.app.services.options_strategy_engine.delta_anchor import strikes_ranked_by_delta
from icici_breeze_backend.app.services.options_strategy_engine.pop import pop_detail_for_legs, pop_for_legs
from icici_breeze_backend.app.services.options_strategy_engine.strategies.income.iron_condor import (
    IC_CREDIT_PCT_RELAXATION_SCHEDULE,
    IC_POP_BAND_WIDTH_PCT,
    IC_RETURN_TOP_N,
    IC_SHORT_STRIKES_INWARD,
    IC_SHORT_STRIKES_OUTWARD,
    IC_SHORT_STRIKES_MAX_ATM,
    IC_SHORT_STRIKES_MAX_PER_WING,
    IC_TOP_K_SHORT_STRIKES,
    WING_WIDTH_MULTIPLIERS,
    IronCondorCandidate,
    IronCondorRejectionStats,
    _build_pop_audit_summary,
    _collect_candidates,
    _ic_pop_bucket,
    _ic_short_strikes_around_target,
    _ic_short_strikes_for_pop_band,
    _pick_top_candidates,
    _pop_band_covered,
    _unit_span_margin,
    build_ranking_summary,
    calc_iron_condor,
    enumerate_symmetric_iron_condors,
    evaluate_symmetric_iron_condor,
    iron_condor_short_delta_window,
    iron_condor_short_pairs,
    passes_hard_wing_credit,
    passes_ic_wing_credit,
    score_iron_condor_candidate,
    score_iron_condor_ror,
)
from icici_breeze_backend.app.services.options_strategy_engine.types import (
    EngineContext,
    QuoteRow,
    StrategyResult,
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
        liquidity_score=0.9,
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


class TestEnumerateSymmetricIronCondor(unittest.TestCase):
    def test_rejects_debit_put_wing(self):
        cache = {
            (22200, "Put"): _quote(22200, "Put", bid=3.35, ask=3.40, delta=0.05),
            (22150, "Put"): _quote(22150, "Put", bid=3.40, ask=3.45, delta=0.04),
            (24550, "Call"): _quote(24550, "Call", bid=3.20, ask=3.25, delta=0.05),
            (24600, "Call"): _quote(24600, "Call", bid=2.70, ask=2.75, delta=0.04),
        }
        ctx = _ctx_from_cache(cache, min_pop_pct=50.0)
        result = evaluate_symmetric_iron_condor(ctx, 22200, 24550)
        self.assertIsNone(result)

    def test_all_wing_widths_enumerated(self):
        strikes = list(range(22500, 24700, 50))
        cache = _fill_strikes(
            strikes,
            23623.0,
            bid_fn=lambda s, r: 12.0 if s in (22800, 24400) else 3.0,
            ask_fn=lambda s, r: 12.1 if s in (22800, 24400) else 3.1,
            delta_fn=lambda s, r: 0.20,
        )
        for s in (22750, 22700, 22650, 22600, 24450, 24500, 24550, 24600):
            right = "Put" if s < 23623 else "Call"
            cache[(s, right)] = _quote(s, right, bid=3.0, ask=3.1, delta=0.18)
        ctx = _ctx_from_cache(cache, min_pop_pct=30.0)
        variants = enumerate_symmetric_iron_condors(ctx, 22800, 24400)
        self.assertGreaterEqual(len(variants), 2)
        widths = {v.wing_width for v in variants}
        self.assertIn(50, widths)
        self.assertIn(100, widths)

    def test_min_wing_credit_rejects_tiny_spreads(self):
        cache = {
            (22800, "Put"): _quote(22800, "Put", bid=0.06, ask=0.02, delta=0.05),
            (22750, "Put"): _quote(22750, "Put", bid=0.02, ask=0.01, delta=0.04),
            (24400, "Call"): _quote(24400, "Call", bid=0.06, ask=0.02, delta=0.05),
            (24450, "Call"): _quote(24450, "Call", bid=0.02, ask=0.01, delta=0.04),
        }
        ctx = _ctx_from_cache(cache, min_pop_pct=50.0)
        stats = IronCondorRejectionStats()
        variants = enumerate_symmetric_iron_condors(ctx, 22800, 24400, stats=stats)
        self.assertEqual(variants, [])
        self.assertGreater(stats.counts.get("min_wing_credit", 0), 0)

    def test_min_credit_pct_accepts_adequate_premium(self):
        cache = {
            (22800, "Put"): _quote(22800, "Put", bid=4.0, ask=4.1, delta=0.05),
            (22750, "Put"): _quote(22750, "Put", bid=1.0, ask=1.1, delta=0.04),
            (24400, "Call"): _quote(24400, "Call", bid=4.0, ask=4.1, delta=0.05),
            (24450, "Call"): _quote(24450, "Call", bid=1.0, ask=1.1, delta=0.04),
        }
        ctx = _ctx_from_cache(cache, min_pop_pct=50.0)
        variants = enumerate_symmetric_iron_condors(ctx, 22800, 24400)
        self.assertEqual(len(variants), 1)
        self.assertGreaterEqual(variants[0].credit, 5.0)


class TestRorScoring(unittest.TestCase):
    def test_ror_prefers_higher_credit_at_lower_pop(self):
        quotes = [_quote(22800, "Put", bid=5, ask=5.1, delta=0.05)] * 4
        score_a, factors_a = score_iron_condor_ror(95.1, 2_000.0, 50_000.0, 95.0, quotes)
        score_b, factors_b = score_iron_condor_ror(93.8, 11_000.0, 50_000.0, 95.0, quotes)
        self.assertGreater(score_b, score_a)
        self.assertGreaterEqual(93.8, 95.0 - 5)
        self.assertIn("ror", factors_a)
        self.assertNotIn("pop_tiebreak", factors_a)

    def test_ranking_summary_mentions_credit(self):
        summary = build_ranking_summary(
            higher_rank=1,
            lower_rank=2,
            viewing_rank=1,
            higher_credit=11_000.0,
            higher_pop=93.8,
            higher_ror=0.22,
            lower_credit=2_000.0,
            lower_pop=95.1,
            lower_ror=0.04,
        )
        self.assertIn("Ranked #1 over #2:", summary)
        self.assertIn("net credit per lot", summary)
        self.assertIn("ROR #1", summary)

    def test_ranking_summary_for_lower_rank_uses_above_variant_lead(self):
        summary = build_ranking_summary(
            higher_rank=3,
            lower_rank=4,
            viewing_rank=4,
            higher_credit=10_050.0,
            higher_pop=93.3,
            higher_ror=0.053,
            lower_credit=10_000.0,
            lower_pop=93.8,
            lower_ror=0.043,
        )
        self.assertIn("#3 ranks above this variant:", summary)
        self.assertIn("PoP #3 93.3% vs #4 93.8%", summary)
        self.assertIn("ROR #3", summary)


class TestScoreIronCondorCandidate(unittest.TestCase):
    def test_span_refinement_orders_by_annualized_return(self):
        low_span = score_iron_condor_candidate(95.0, 10_000.0, 50_000.0, 50_000.0, 4)
        high_span = score_iron_condor_candidate(95.0, 10_000.0, 50_000.0, 100_000.0, 4)
        self.assertGreater(low_span, high_span)


class TestCalcIronCondor(unittest.TestCase):
    def _margin_mock(self, span_by_structure: dict[tuple, float]) -> MagicMock:
        proc = MagicMock()

        def margin(user_id, exchange, legs, audit=None, audit_context=None):
            key = tuple(
                (leg["strike_price"], leg["right"], leg["action"])
                for leg in sorted(legs, key=lambda x: (x["strike_price"], x["right"]))
            )
            span = span_by_structure.get(key, 65_000.0)
            return {"Success": {"span_margin_required": span}}

        proc.strategy_builder_margin.side_effect = margin
        return proc

    def test_near_floor_pop_reaches_finalists_at_90_pop(self):
        """At 90% PoP floor, rank #1 should be near-floor high-credit, not 94%+ OTM."""
        spot = 23622.9
        strikes = list(range(22600, 24700, 50))
        deltas = {
            (22950, "Put"): 0.08,
            (22900, "Put"): 0.07,
            (22850, "Put"): 0.06,
            (22800, "Put"): 0.055,
            (22750, "Put"): 0.05,
            (22700, "Put"): 0.045,
            (22650, "Put"): 0.04,
            (22600, "Put"): 0.035,
            (24350, "Call"): 0.08,
            (24400, "Call"): 0.07,
            (24450, "Call"): 0.06,
            (24500, "Call"): 0.055,
            (24550, "Call"): 0.05,
            (24600, "Call"): 0.045,
            (24650, "Call"): 0.04,
        }
        cache = _fill_strikes(
            strikes,
            spot,
            bid_fn=lambda s, r: 3.0,
            ask_fn=lambda s, r: 3.15,
            delta_fn=lambda s, r: deltas.get((s, r), 0.03),
        )
        cache[(22950, "Put")] = _quote(22950, "Put", bid=12.0, ask=12.15, delta=0.08, spot=spot)
        cache[(24350, "Call")] = _quote(24350, "Call", bid=12.0, ask=12.15, delta=0.08, spot=spot)
        cache[(22750, "Put")] = _quote(22750, "Put", bid=5.65, ask=5.80, delta=0.05, spot=spot)
        cache[(24550, "Call")] = _quote(24550, "Call", bid=3.85, ask=3.95, delta=0.05, spot=spot)

        proc = MagicMock()
        proc.strategy_builder_margin.return_value = {"Success": {"span_margin_required": 50_000.0}}
        ctx = _ctx_from_cache(
            cache,
            spot=spot,
            min_pop_pct=90.0,
            min_ann_return_pct=0.0,
            max_loss_rupees=5_500_000,
            processor=proc,
        )
        candidates, _ = _collect_candidates(ctx, iron_condor_short_pairs(ctx))
        self.assertGreater(len(candidates), 0)

        with patch(
            "icici_breeze_backend.app.services.options_strategy_engine.strategies.income.iron_condor.MIN_IC_CREDIT_PCT_OF_WIDTH",
            0.01,
        ):
            results = asyncio.run(calc_iron_condor(ctx))

        ok = [r for r in results if r.status == "ok"]
        self.assertGreaterEqual(len(ok), 1)
        self.assertGreaterEqual(ok[0].pop_pct or 0, 90.0)
        self.assertTrue(ok[0].badges)

    def test_does_not_pick_audit_bad_debit_put_condor(self):
        strikes = list(range(22150, 25150, 50))
        cache: dict = {}
        for s in strikes:
            dist = abs(s - 23623)
            prem = max(1.0, 12.0 - dist / 200.0)
            delta = min(0.35, max(0.05, dist / 4000.0))
            cache[(s, "Put")] = _quote(s, "Put", bid=prem, ask=prem + 0.1, delta=delta)
            cache[(s, "Call")] = _quote(s, "Call", bid=prem, ask=prem + 0.1, delta=delta)

        cache[(22200, "Put")] = _quote(22200, "Put", bid=3.35, ask=3.40, delta=0.05)
        cache[(22150, "Put")] = _quote(22150, "Put", bid=3.40, ask=3.45, delta=0.04)
        cache[(24550, "Call")] = _quote(24550, "Call", bid=3.20, ask=3.25, delta=0.05)
        cache[(24600, "Call")] = _quote(24600, "Call", bid=2.70, ask=2.75, delta=0.04)

        proc = self._margin_mock({})
        ctx = _ctx_from_cache(cache, min_pop_pct=50.0, min_ann_return_pct=0.0, processor=proc)

        with patch(
            "icici_breeze_backend.app.services.options_strategy_engine.strategies.income.iron_condor.MIN_IC_CREDIT_PCT_OF_WIDTH",
            0.01,
        ):
            results = asyncio.run(calc_iron_condor(ctx))

        ok = [r for r in results if r.status == "ok"]
        self.assertGreater(len(ok), 0)
        result = ok[0]
        put_sell = next(leg for leg in result.legs if leg.right == "Put" and leg.side == "Sell")
        put_buy = next(leg for leg in result.legs if leg.right == "Put" and leg.side == "Buy")
        self.assertFalse(put_sell.strike == 22200 and put_buy.strike == 22150)
        self.assertGreater(put_sell.premium_per_unit - put_buy.premium_per_unit, 0)

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
        ctx.strikes = [22800, 22900, 23250, 24400, 24500]

        def _cand(short_put: int, final: float, premium: float) -> IronCondorCandidate:
            trade_legs = [
                TradeLeg("Put", "Sell", short_put, 65, 8.0),
                TradeLeg("Put", "Buy", short_put - 100, 65, 5.0),
                TradeLeg("Call", "Sell", 24400, 65, 8.0),
                TradeLeg("Call", "Buy", 24500, 65, 5.0),
            ]
            return IronCondorCandidate(
                short_put=short_put,
                short_call=24400,
                long_put=short_put - 100,
                long_call=24500,
                credit=6.0,
                put_credit=3.0,
                call_credit=3.0,
                max_loss_u=44.0,
                qty=65,
                pop=95.0,
                wing_width=100,
                legs=trade_legs,
                proxy_score=final,
                net_collected=premium,
                final_score=final,
                score_factors={"ror": 0.1, "liquidity_weight": 0.9, "spread_weight": 0.9},
            )

        low_span = _cand(22900, final=50.0, premium=15_000.0)
        high_score = _cand(23250, final=100.0, premium=10_000.0)

        winners, scores = asyncio.run(
            _pick_top_candidates(
            ctx, [high_score, low_span], strategy_id="iron_condor", top_n=2
        ))
        self.assertEqual(len(winners), 2)
        winner, best_return = winners[0]
        self.assertEqual(winner.short_put, 22900)
        self.assertGreater(best_return, 0)
        self.assertEqual(len(scores), 2)

    def test_flags_relaxed_when_annualized_return_below_minimum(self):
        strikes = list(range(22700, 24700, 50))
        cache = _fill_strikes(
            strikes,
            23623.0,
            bid_fn=lambda s, r: 8.0,
            ask_fn=lambda s, r: 5.0,
            delta_fn=lambda s, r: 0.20,
        )
        proc = MagicMock()
        proc.strategy_builder_margin.return_value = {
            "Success": {"span_margin_required": 500_000.0}
        }
        ctx = _ctx_from_cache(
            cache, min_pop_pct=30.0, min_ann_return_pct=500.0, processor=proc
        )
        results = asyncio.run(calc_iron_condor(ctx))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, "ok")
        self.assertEqual(results[0].compliance, "relaxed")
        self.assertIn("min_ann_return", results[0].constraint_violations)

    def test_returns_top_variants_with_ranks(self):
        strikes = list(range(22600, 24700, 50))
        cache = _fill_strikes(
            strikes,
            23623.0,
            bid_fn=lambda s, r: 8.0,
            ask_fn=lambda s, r: 5.0,
            delta_fn=lambda s, r: 0.05,
        )
        proc = MagicMock()
        proc.strategy_builder_margin.return_value = {"Success": {"span_margin_required": 50_000.0}}
        ctx = _ctx_from_cache(
            cache, min_pop_pct=50.0, min_ann_return_pct=0.0, processor=proc
        )

        results = asyncio.run(calc_iron_condor(ctx))

        ok = [r for r in results if r.status == "ok"]
        self.assertGreaterEqual(len(ok), 1)
        self.assertEqual(ok[0].variant_rank, 1)
        self.assertTrue(ok[0].badges)

    def test_pick_top_candidates_margins_only_shortlist(self):
        proc = MagicMock()
        proc.strategy_builder_margin.return_value = {
            "Success": {"span_margin_required": 50_000.0}
        }
        ctx = _ctx_from_cache({}, processor=proc)

        def _cand(short_put: int, final: float) -> IronCondorCandidate:
            trade_legs = [
                TradeLeg("Put", "Sell", short_put, 65, 8.0),
                TradeLeg("Put", "Buy", short_put - 100, 65, 5.0),
                TradeLeg("Call", "Sell", 24400, 65, 8.0),
                TradeLeg("Call", "Buy", 24500, 65, 5.0),
            ]
            return IronCondorCandidate(
                short_put=short_put,
                short_call=24400,
                long_put=short_put - 100,
                long_call=24500,
                credit=6.0,
                put_credit=3.0,
                call_credit=3.0,
                max_loss_u=44.0,
                qty=65,
                pop=95.0,
                wing_width=100,
                legs=trade_legs,
                proxy_score=final,
                net_collected=float(10_000 - (short_put - 22800) // 50 * 500),
                final_score=final,
                score_factors={"ror": 0.1, "liquidity_weight": 0.9, "spread_weight": 0.9},
            )

        candidates = [_cand(22800 + i * 50, float(100 - i)) for i in range(8)]
        asyncio.run(
            _pick_top_candidates(ctx, candidates, strategy_id="iron_condor", top_n=IC_RETURN_TOP_N)
        )
        self.assertEqual(proc.strategy_builder_margin.call_count, IC_RETURN_TOP_N)

    def test_pick_top_candidates_isolates_margin_failures(self):
        proc = MagicMock()
        call_count = {"n": 0}

        def margin(user_id, exchange, legs, audit=None, audit_context=None, audit_rationale=None):
            call_count["n"] += 1
            short_put = next(
                leg["strike_price"] for leg in legs if leg["right"] == "Put" and leg["action"] == "Sell"
            )
            if short_put == "22900":
                return {"Status": 400, "Error": "broker down"}
            return {"Status": 200, "Success": {"span_margin_required": 50_000.0}}

        proc.strategy_builder_margin.side_effect = margin
        ctx = _ctx_from_cache({}, processor=proc)

        def _cand(short_put: int, final: float) -> IronCondorCandidate:
            trade_legs = [
                TradeLeg("Put", "Sell", short_put, 65, 8.0),
                TradeLeg("Put", "Buy", short_put - 100, 65, 5.0),
                TradeLeg("Call", "Sell", 24400, 65, 8.0),
                TradeLeg("Call", "Buy", 24500, 65, 5.0),
            ]
            return IronCondorCandidate(
                short_put=short_put,
                short_call=24400,
                long_put=short_put - 100,
                long_call=24500,
                credit=6.0,
                put_credit=3.0,
                call_credit=3.0,
                max_loss_u=44.0,
                qty=65,
                pop=95.0,
                wing_width=100,
                legs=trade_legs,
                proxy_score=final,
                net_collected=float(10_000),
                final_score=final,
                score_factors={"ror": 0.1, "liquidity_weight": 0.9, "spread_weight": 0.9},
            )

        candidates = [_cand(22900, 100.0), _cand(22800, 90.0), _cand(22850, 80.0)]
        winners, scores = asyncio.run(
            _pick_top_candidates(ctx, candidates, strategy_id="iron_condor", top_n=IC_RETURN_TOP_N)
        )
        self.assertEqual(call_count["n"], IC_RETURN_TOP_N)
        self.assertEqual(len(winners), IC_RETURN_TOP_N)
        failed = next(s for s in scores if s["short_put"] == 22900)
        self.assertEqual(failed["unit_span"], 0.0)
        self.assertGreater(
            next(s for s in scores if s["short_put"] == 22800)["unit_span"],
            0.0,
        )

    def test_unit_span_margin_uses_session_cache(self):
        proc = MagicMock()
        proc.strategy_builder_margin.return_value = {
            "Success": {"span_margin_required": 42_000.0}
        }
        ctx = _ctx_from_cache({}, processor=proc)
        legs = [
            TradeLeg("Put", "Sell", 22800, 65, 8.0),
            TradeLeg("Put", "Buy", 22700, 65, 5.0),
            TradeLeg("Call", "Sell", 24400, 65, 8.0),
            TradeLeg("Call", "Buy", 24500, 65, 5.0),
        ]
        span_a = _unit_span_margin(ctx, legs, strategy_id="iron_condor")
        span_b = _unit_span_margin(ctx, legs, strategy_id="iron_condor")
        self.assertEqual(span_a, 42_000.0)
        self.assertEqual(span_b, 42_000.0)
        self.assertEqual(proc.strategy_builder_margin.call_count, 1)

    def test_resize_reuses_ic_unit_span_cache(self):
        proc = MagicMock()
        proc.strategy_builder_margin.return_value = {
            "Success": {"span_margin_required": 55_000.0}
        }
        ctx = _ctx_from_cache(
            {},
            margin_rupees=50_000_000,
            max_loss_rupees=4_000_000,
            processor=proc,
        )
        legs = [
            TradeLeg("Put", "Sell", 22800, 65, 8.0),
            TradeLeg("Put", "Buy", 22700, 65, 5.0),
            TradeLeg("Call", "Sell", 24400, 65, 8.0),
            TradeLeg("Call", "Buy", 24500, 65, 5.0),
        ]
        _unit_span_margin(ctx, legs, strategy_id="iron_condor")
        results = [
            StrategyResult(
                "iron_condor",
                "Iron Condor",
                "ok",
                net_premium=6.0 * 65,
                max_loss=44.0 * 65,
                legs=legs,
            )
        ]
        asyncio.run(
            resize_results_to_budgets(
            proc, "u1", "NFO", "NIFTY", "16-Jun-2026", results, ctx
        )
        )
        self.assertEqual(proc.strategy_builder_margin.call_count, 1)
        self.assertEqual(results[0].status, "ok")


class TestAuditQuoteRegression(unittest.TestCase):
    def test_far_otm_condor_survives_relaxed_credit_gate(self):
        """Audit-cache style 22850/24400 condor at ~94% PoP / 55L max loss."""
        strikes = list(range(22600, 24650, 50))
        cache = _fill_strikes(
            strikes,
            23622.9,
            bid_fn=lambda s, r: 3.0,
            ask_fn=lambda s, r: 3.1,
            delta_fn=lambda s, r: 0.025,
        )
        cache[(22850, "Put")] = _quote(22850, "Put", bid=6.80, ask=6.95, delta=0.02)
        cache[(22750, "Put")] = _quote(22750, "Put", bid=5.65, ask=5.80, delta=0.018)
        cache[(24400, "Call")] = _quote(24400, "Call", bid=5.40, ask=5.55, delta=0.02)
        cache[(24500, "Call")] = _quote(24500, "Call", bid=3.85, ask=3.95, delta=0.018)
        ctx = _ctx_from_cache(
            cache,
            spot=23622.9,
            min_pop_pct=93.0,
            max_loss_rupees=5_500_000,
        )
        variants = enumerate_symmetric_iron_condors(ctx, 22850, 24400)
        self.assertGreater(len(variants), 0)
        widths = {v.wing_width for v in variants}
        self.assertIn(50, widths)

    def test_strangle_seed_pair_included_at_95_pop(self):
        strikes = list(range(22500, 24700, 50))

        def delta_fn(s, r):
            if s == 22650 and r == "Put":
                return 0.025
            if s == 24500 and r == "Call":
                return 0.025
            return 0.08

        cache = _fill_strikes(
            strikes,
            23622.9,
            bid_fn=lambda s, r: 4.0 if s in (22650, 24500) else 2.0,
            ask_fn=lambda s, r: 4.1 if s in (22650, 24500) else 2.1,
            delta_fn=delta_fn,
        )
        ctx = _ctx_from_cache(cache, spot=23622.9, min_pop_pct=95.0)
        pairs = iron_condor_short_pairs(ctx)
        self.assertGreater(len(pairs), 0)
        self.assertLessEqual(len(pairs), IC_SHORT_STRIKES_MAX_PER_WING * IC_SHORT_STRIKES_MAX_PER_WING)


class TestIronCondorEvalAudit(unittest.TestCase):
    def test_enumeration_logs_every_candidate_with_pop_basis(self):
        cache = {
            (22850, "Put"): _quote(22850, "Put", bid=6.80, ask=6.95, delta=0.02),
            (22750, "Put"): _quote(22750, "Put", bid=5.65, ask=5.80, delta=0.018),
            (24400, "Call"): _quote(24400, "Call", bid=5.40, ask=5.55, delta=0.02),
            (24500, "Call"): _quote(24500, "Call", bid=3.85, ask=3.95, delta=0.018),
        }
        ctx = _ctx_from_cache(cache, spot=23622.9, min_pop_pct=50.0)
        stats = IronCondorRejectionStats()
        enumerate_symmetric_iron_condors(ctx, 22850, 24400, stats=stats)
        self.assertEqual(len(stats.pair_wing_plans), 1)
        self.assertEqual(stats.pair_wing_plans[0]["wing_widths_attempted"], [50, 100, 150, 200])
        self.assertGreater(len(stats.evaluations), 0)
        for ev in stats.evaluations:
            self.assertIn(ev["wing_width"], [50, 100, 150, 200])
            if ev["outcome"] == "accepted":
                self.assertEqual(ev["pop_basis"], "breakevens_from_short_strikes")
                self.assertIsNotNone(ev["pop_pct"])
                self.assertIsNotNone(ev["lower_breakeven"])
                self.assertIsNotNone(ev["upper_breakeven"])


class TestPopBreakevenParity(unittest.TestCase):
    def test_ic_pop_uses_short_strike_breakevens(self):
        from icici_breeze_backend.app.services.options_strategy_engine.helpers import sigma_for_pop
        from icici_breeze_backend.app.services.options_strategy_engine.pop import pop_between_breakevens

        cache = {
            (22850, "Put"): _quote(22850, "Put", bid=6.80, ask=6.95, delta=0.02),
            (22750, "Put"): _quote(22750, "Put", bid=5.65, ask=5.80, delta=0.018),
            (24400, "Call"): _quote(24400, "Call", bid=5.40, ask=5.55, delta=0.02),
            (24500, "Call"): _quote(24500, "Call", bid=3.85, ask=3.95, delta=0.018),
        }
        ctx = _ctx_from_cache(cache, spot=23622.9, min_pop_pct=95.0)
        qty = 65
        ic_legs = [
            TradeLeg("Put", "Sell", 22850, qty, 6.80),
            TradeLeg("Put", "Buy", 22750, qty, 5.80),
            TradeLeg("Call", "Sell", 24400, qty, 5.40),
            TradeLeg("Call", "Buy", 24500, qty, 3.95),
        ]
        net_credit = 6.80 - 5.80 + 5.40 - 3.95
        expected = pop_between_breakevens(
            ctx.spot,
            22850 - net_credit,
            24400 + net_credit,
            ctx.t_years,
            sigma_for_pop(ctx),
        )
        detail = pop_detail_for_legs(ctx, ic_legs)
        ic_pop = detail.pop_pct
        self.assertEqual(detail.basis, "breakevens_from_short_strikes")
        self.assertEqual(detail.short_put, 22850)
        self.assertEqual(detail.short_call, 24400)
        self.assertAlmostEqual(ic_pop, expected, places=2)
        self.assertAlmostEqual(ic_pop, pop_for_legs(ctx, ic_legs), places=4)
        long_wing_pop = pop_between_breakevens(
            ctx.spot,
            22750 - net_credit,
            24500 + net_credit,
            ctx.t_years,
            sigma_for_pop(ctx),
        )
        self.assertLess(ic_pop, long_wing_pop)


class TestSkipMessage(unittest.TestCase):
    def test_skip_message_reports_dominant_filter(self):
        stats = IronCondorRejectionStats()
        stats.record("min_credit", wing_width=50)
        stats.record("min_credit", wing_width=100)
        stats.record("pop_floor", wing_width=50)
        msg = stats.skip_message()
        self.assertIn("min_credit", msg)
        self.assertIn("rejected", msg.lower())


class TestWingCreditGate(unittest.TestCase):
    def test_hard_wing_credit_blocks_tiny_spreads(self):
        self.assertFalse(passes_hard_wing_credit(0.04, 0.06))

    def test_per_spread_floor_blocks_tiny_wings(self):
        self.assertFalse(passes_ic_wing_credit(0.04, 0.06, 50))

    def test_audit_style_credit_passes_50pt_wing(self):
        self.assertTrue(passes_ic_wing_credit(1.0, 1.45, 50))

    def test_passes_ic_wing_credit_respects_override(self):
        self.assertFalse(passes_ic_wing_credit(0.6, 0.6, 50))
        self.assertFalse(passes_ic_wing_credit(0.6, 0.6, 50, min_credit_pct_of_width=0.025))
        self.assertTrue(passes_ic_wing_credit(0.6, 0.6, 50, min_credit_pct_of_width=0.02))


class TestSoftPreferredCredit(unittest.TestCase):
    def test_soft_preferred_credit_survives_with_annotation(self):
        cache = {
            (22800, "Put"): _quote(22800, "Put", bid=1.0, ask=1.05, delta=0.05),
            (22750, "Put"): _quote(22750, "Put", bid=0.35, ask=0.40, delta=0.04),
            (24400, "Call"): _quote(24400, "Call", bid=1.0, ask=1.05, delta=0.05),
            (24450, "Call"): _quote(24450, "Call", bid=0.35, ask=0.40, delta=0.04),
        }
        ctx = _ctx_from_cache(cache, min_pop_pct=50.0)
        variants = enumerate_symmetric_iron_condors(ctx, 22800, 24400)
        self.assertEqual(len(variants), 1)
        self.assertAlmostEqual(variants[0].credit, 1.2, places=2)
        self.assertTrue(variants[0].below_preferred_credit)
        self.assertEqual(variants[0].score_factors.get("preferred_credit_met"), 0.0)

        candidates, pct = _collect_candidates(ctx, [(22800, 24400)])
        self.assertGreater(len(candidates), 0)
        self.assertEqual(pct, 0.03)
        self.assertTrue(candidates[0].below_preferred_credit)

    def test_preferred_credit_met_when_adequate_premium(self):
        cache = {
            (22800, "Put"): _quote(22800, "Put", bid=4.0, ask=4.1, delta=0.05),
            (22750, "Put"): _quote(22750, "Put", bid=1.0, ask=1.1, delta=0.04),
            (24400, "Call"): _quote(24400, "Call", bid=4.0, ask=4.1, delta=0.05),
            (24450, "Call"): _quote(24450, "Call", bid=1.0, ask=1.1, delta=0.04),
        }
        ctx = _ctx_from_cache(cache, min_pop_pct=50.0)
        variants = enumerate_symmetric_iron_condors(ctx, 22800, 24400)
        self.assertEqual(len(variants), 1)
        self.assertFalse(variants[0].below_preferred_credit)

    def test_calc_iron_condor_includes_below_preferred_survivor(self):
        cache = {
            (22800, "Put"): _quote(22800, "Put", bid=1.0, ask=1.05, delta=0.05),
            (22750, "Put"): _quote(22750, "Put", bid=0.35, ask=0.40, delta=0.04),
            (24400, "Call"): _quote(24400, "Call", bid=1.0, ask=1.05, delta=0.05),
            (24450, "Call"): _quote(24450, "Call", bid=0.35, ask=0.40, delta=0.04),
        }
        proc = MagicMock()
        proc.strategy_builder_margin.return_value = {"Success": {"span_margin_required": 50_000.0}}
        ctx = _ctx_from_cache(cache, min_pop_pct=50.0, min_ann_return_pct=0.0, processor=proc)
        with patch(
            "icici_breeze_backend.app.services.options_strategy_engine.strategies.income.iron_condor.iron_condor_short_pairs",
            return_value=[(22800, 24400)],
        ), patch(
            "icici_breeze_backend.app.services.options_strategy_engine.strategies.income.iron_condor._best_strangle_short_pair",
            return_value=None,
        ):
            results = asyncio.run(calc_iron_condor(ctx))
        ok = [r for r in results if r.status == "ok"]
        self.assertGreater(len(ok), 0)
        self.assertAlmostEqual(ok[0].net_premium or 0, 1.2 * 65, places=0)
        self.assertEqual(ok[0].score_breakdown.get("below_preferred_credit"), 1.0)


class TestPopBandStrikeSelection(unittest.TestCase):
    def test_pop_band_strike_selection_covers_floor_to_floor_plus_two(self):
        spot = 23622.9
        strikes = list(range(22600, 24700, 50))
        deltas = {
            (22950, "Put"): 0.08,
            (22900, "Put"): 0.07,
            (22850, "Put"): 0.06,
            (22800, "Put"): 0.055,
            (22750, "Put"): 0.05,
            (22700, "Put"): 0.045,
            (22650, "Put"): 0.04,
            (24350, "Call"): 0.08,
            (24400, "Call"): 0.07,
            (24450, "Call"): 0.06,
            (24500, "Call"): 0.055,
            (24550, "Call"): 0.05,
            (24600, "Call"): 0.045,
        }

        def bid_fn(s: int, r: str) -> float:
            if (s, r) == (22950, "Put"):
                return 1.0
            if (s, r) in ((24350, "Call"), (24400, "Call")):
                return 1.0
            return 3.0

        cache = _fill_strikes(
            strikes,
            spot,
            bid_fn=bid_fn,
            ask_fn=lambda s, r: bid_fn(s, r) + 0.15,
            delta_fn=lambda s, r: deltas.get((s, r), 0.03),
        )
        ctx = _ctx_from_cache(cache, spot=spot, min_pop_pct=90.0)
        puts = _ic_short_strikes_for_pop_band(
            ctx,
            [s for s in ctx.liquid_pe_strikes if s < ctx.spot],
            "Put",
            90.0,
        )
        calls = _ic_short_strikes_for_pop_band(
            ctx,
            [s for s in ctx.liquid_ce_strikes if s > ctx.spot],
            "Call",
            90.0,
            opposite_strikes=puts,
        )
        self.assertTrue(_pop_band_covered(ctx, puts, calls, 90.0))

    def test_pop_band_covered_uses_real_pop_not_delta(self):
        """Coverage must use breakeven PoP; naive delta sum can mis-rank vs breakeven model."""
        spot = 23623.0
        short_put, short_call = 23000, 24200
        put_delta, call_delta = 0.044, 0.046
        bid = 20.0
        cache = {
            (short_put, "Put"): _quote(
                short_put, "Put", bid=bid, ask=bid + 0.1, delta=put_delta, spot=spot
            ),
            (short_call, "Call"): _quote(
                short_call, "Call", bid=bid, ask=bid + 0.1, delta=call_delta, spot=spot
            ),
        }
        ctx = _ctx_from_cache(cache, spot=spot, min_pop_pct=90.0)
        legs = [
            TradeLeg("Put", "Sell", short_put, 1, bid),
            TradeLeg("Call", "Sell", short_call, 1, bid),
        ]
        real_pop = pop_for_legs(ctx, legs)
        delta_est = (1.0 - put_delta - call_delta) * 100.0
        self.assertGreaterEqual(delta_est, 90.0)
        self.assertGreater(real_pop, 90.0)
        self.assertFalse(_pop_band_covered(ctx, [short_put], [short_call], 90.0))

    def test_ic_pop_bucket_labels(self):
        self.assertEqual(_ic_pop_bucket(90.4, 90.0), "90-91")
        self.assertEqual(_ic_pop_bucket(91.9, 90.0), "91-92")
        self.assertEqual(_ic_pop_bucket(95.0, 90.0), ">=93")


class TestPopAuditBuckets(unittest.TestCase):
    def test_pop_audit_buckets_min_credit_and_survivors(self):
        cache = {
            (22800, "Put"): _quote(22800, "Put", bid=1.0, ask=1.05, delta=0.05),
            (22750, "Put"): _quote(22750, "Put", bid=0.35, ask=0.40, delta=0.04),
            (24400, "Call"): _quote(24400, "Call", bid=1.0, ask=1.05, delta=0.05),
            (24450, "Call"): _quote(24450, "Call", bid=0.35, ask=0.40, delta=0.04),
        }
        ctx = _ctx_from_cache(cache, min_pop_pct=50.0)
        stats = IronCondorRejectionStats()
        variants = enumerate_symmetric_iron_condors(ctx, 22800, 24400, stats=stats)
        summary = _build_pop_audit_summary(stats, variants, 50.0)
        self.assertGreater(len(summary["pop_distribution"]), 0)
        self.assertGreater(summary["below_preferred_credit_total"], 0)
        self.assertGreater(
            len(summary["below_preferred_credit_by_pop_bucket"]),
            0,
        )
        self.assertEqual(summary["pop_band_target"], [50.0, 50.0 + IC_POP_BAND_WIDTH_PCT])


class TestCreditRelaxationSchedule(unittest.TestCase):
    def test_relaxation_schedule_matches_spec(self):
        self.assertEqual(
            IC_CREDIT_PCT_RELAXATION_SCHEDULE,
            (0.03, 0.025, 0.02, 0.015, 0.01),
        )


class TestSearchBounds(unittest.TestCase):
    def test_delta_window_for_95_pop(self):
        lo, hi = iron_condor_short_delta_window(95.0)
        self.assertAlmostEqual(lo, 0.02, places=2)
        self.assertLessEqual(hi, 0.12)
        self.assertLess(lo, hi)

    def test_adaptive_pairs_generated(self):
        strikes = list(range(22000, 25100, 50))
        cache = _fill_strikes(
            strikes,
            23623.0,
            bid_fn=lambda s, r: 5.0,
            ask_fn=lambda s, r: 5.2,
            delta_fn=lambda s, r: 0.05,
        )
        ctx = _ctx_from_cache(cache, min_pop_pct=93.0)
        pairs = iron_condor_short_pairs(ctx)
        self.assertGreater(len(pairs), 0)
        for sp, sc in pairs:
            self.assertLess(sp, sc)

    @patch(
        "icici_breeze_backend.app.services.options_strategy_engine.strategies.income.iron_condor.pop_detail_for_legs"
    )
    def test_hard_pop_floor_rejects_below_min(self, mock_pop_detail):
        from icici_breeze_backend.app.services.options_strategy_engine.pop import PopDetail

        mock_pop_detail.return_value = PopDetail(93.5, "breakevens_from_short_strikes")
        strikes = list(range(22600, 24650, 50))
        cache = _fill_strikes(
            strikes,
            23622.9,
            bid_fn=lambda s, r: 3.0,
            ask_fn=lambda s, r: 3.1,
            delta_fn=lambda s, r: 0.025,
        )
        cache[(22850, "Put")] = _quote(22850, "Put", bid=6.80, ask=6.95, delta=0.02)
        cache[(22750, "Put")] = _quote(22750, "Put", bid=5.65, ask=5.80, delta=0.018)
        cache[(24400, "Call")] = _quote(24400, "Call", bid=5.40, ask=5.55, delta=0.02)
        cache[(24500, "Call")] = _quote(24500, "Call", bid=3.85, ask=3.95, delta=0.018)
        ctx_pass = _ctx_from_cache(cache, spot=23622.9, min_pop_pct=93.0)
        ctx_reject = _ctx_from_cache(cache, spot=23622.9, min_pop_pct=94.0)
        stats = IronCondorRejectionStats()
        survivors_pass = enumerate_symmetric_iron_condors(ctx_pass, 22850, 24400)
        enumerate_symmetric_iron_condors(ctx_reject, 22850, 24400, stats=stats)
        self.assertGreater(len(survivors_pass), 0)
        self.assertLess(survivors_pass[0].pop, 94.0)
        self.assertEqual(
            enumerate_symmetric_iron_condors(ctx_reject, 22850, 24400),
            [],
        )
        self.assertGreater(stats.counts.get("pop_floor", 0), 0)

    def test_short_strikes_include_band_and_atm_extension(self):
        """PoP-band shortlist must include ATM-ward strikes beyond legacy top-K."""
        strikes = list(range(22500, 24700, 50))
        target = 0.025  # 95% PoP target delta

        def delta_fn(s, r):
            if s == 22700 and r == "Put":
                return 0.08
            if s == 24500 and r == "Call":
                return 0.08
            if s == 22800 and r == "Put":
                return 0.02
            if s == 24400 and r == "Call":
                return 0.02
            dist = abs(s - 23623)
            return max(0.015, target - dist / 50000)

        def bid_fn(s: int, r: str) -> float:
            if (s, r) == (22800, "Put"):
                return 10.0
            if (s, r) == (24400, "Call"):
                return 10.0
            return 4.0

        cache = _fill_strikes(
            strikes,
            23623.0,
            bid_fn=bid_fn,
            ask_fn=lambda s, r: bid_fn(s, r) + 0.1,
            delta_fn=delta_fn,
        )
        ctx = _ctx_from_cache(cache, min_pop_pct=95.0)
        puts = _ic_short_strikes_for_pop_band(
            ctx,
            [s for s in ctx.liquid_pe_strikes if s < ctx.spot],
            "Put",
            95.0,
        )
        calls = _ic_short_strikes_for_pop_band(
            ctx,
            [s for s in ctx.liquid_ce_strikes if s > ctx.spot],
            "Call",
            95.0,
            opposite_strikes=puts,
        )
        self.assertIn(22700, puts)
        self.assertIn(24500, calls)
        self.assertTrue(_pop_band_covered(ctx, puts, calls, 95.0))


if __name__ == "__main__":
    unittest.main()
