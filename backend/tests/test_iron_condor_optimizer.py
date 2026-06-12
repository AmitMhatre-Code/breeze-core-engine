"""Regression tests for iron condor optimizer revamp."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from icici_breeze_backend.app.services.options_strategy_engine.delta_anchor import (
    iron_condor_short_delta_window,
)
from icici_breeze_backend.app.services.options_strategy_engine.pruning import (
    iron_condor_delta_window,
    iron_condor_short_pairs,
)
from icici_breeze_backend.app.services.options_strategy_engine.ranking import (
    build_ranking_summary,
    score_iron_condor_candidate,
    score_iron_condor_ror,
)
from icici_breeze_backend.app.services.options_strategy_engine.strategies.base import (
    IronCondorCandidate,
    enumerate_symmetric_iron_condors,
    evaluate_symmetric_iron_condor,
)
from icici_breeze_backend.app.services.options_strategy_engine.strategies.income.iron_condor import (
    _pick_top_candidates,
    calc_iron_condor,
)
from icici_breeze_backend.app.services.options_strategy_engine.types import (
    EngineContext,
    IC_TOP_K_SHORT_STRIKES,
    QuoteRow,
    TradeLeg,
    WING_WIDTH_MULTIPLIERS,
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

    def test_min_credit_pct_rejects_low_premium(self):
        cache = {
            (22800, "Put"): _quote(22800, "Put", bid=0.20, ask=0.25, delta=0.05),
            (22750, "Put"): _quote(22750, "Put", bid=0.10, ask=0.15, delta=0.04),
            (24400, "Call"): _quote(24400, "Call", bid=0.20, ask=0.25, delta=0.05),
            (24450, "Call"): _quote(24450, "Call", bid=0.10, ask=0.15, delta=0.04),
        }
        ctx = _ctx_from_cache(cache, min_pop_pct=50.0)
        variants = enumerate_symmetric_iron_condors(ctx, 22800, 24400)
        self.assertEqual(variants, [])

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
        score_a, _ = score_iron_condor_ror(95.1, 2_000.0, 50_000.0, 95.0, quotes)
        score_b, _ = score_iron_condor_ror(93.8, 11_000.0, 50_000.0, 95.0, quotes)
        self.assertGreater(score_b, score_a)

    def test_ranking_summary_mentions_credit(self):
        summary = build_ranking_summary(11_000.0, 93.8, 0.22, 2_000.0, 95.1, 0.04)
        self.assertIn("credit", summary.lower())
        self.assertIn("ROR", summary)


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
        ctx = _ctx_from_cache(cache, min_pop_pct=50.0, processor=proc)

        with patch(
            "icici_breeze_backend.app.services.options_strategy_engine.strategies.income.iron_condor.MIN_IC_ANNUALIZED_RETURN_PCT",
            0.0,
        ), patch(
            "icici_breeze_backend.app.services.options_strategy_engine.strategies.base.MIN_IC_CREDIT_PCT_OF_WIDTH",
            0.01,
        ):
            results = calc_iron_condor(ctx)

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
                score_factors={"ror": 0.1},
            )

        high_score = _cand(23250, final=100.0, premium=10_000.0)
        low_span = _cand(22900, final=100.0, premium=8_000.0)

        winners, scores = _pick_top_candidates(
            ctx, [high_score, low_span], strategy_id="iron_condor", top_n=1
        )
        self.assertEqual(len(winners), 1)
        winner, best_return = winners[0]
        self.assertEqual(winner.short_put, 22900)
        self.assertGreater(best_return, 0)
        self.assertEqual(len(scores), 2)

    def test_skips_when_annualized_return_below_minimum(self):
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
        ctx = _ctx_from_cache(cache, min_pop_pct=30.0, processor=proc)
        results = calc_iron_condor(ctx)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, "skipped")
        self.assertIn("annualized return", (results[0].skip_reason or "").lower())

    def test_returns_top_variants_with_ranks(self):
        strikes = list(range(22700, 24700, 50))
        cache = _fill_strikes(
            strikes,
            23623.0,
            bid_fn=lambda s, r: 8.0,
            ask_fn=lambda s, r: 5.0,
            delta_fn=lambda s, r: 0.05,
        )
        proc = MagicMock()
        proc.strategy_builder_margin.return_value = {"Success": {"span_margin_required": 50_000.0}}
        ctx = _ctx_from_cache(cache, min_pop_pct=50.0, processor=proc)

        with patch(
            "icici_breeze_backend.app.services.options_strategy_engine.strategies.income.iron_condor.MIN_IC_ANNUALIZED_RETURN_PCT",
            0.0,
        ):
            results = calc_iron_condor(ctx)

        ok = [r for r in results if r.status == "ok"]
        self.assertGreater(len(ok), 1)
        self.assertEqual(ok[0].variant_rank, 1)
        self.assertIsNotNone(ok[0].engine_score)
        if len(ok) > 1:
            self.assertEqual(ok[1].variant_rank, 2)
            self.assertIsNotNone(ok[1].ranking_summary)


class TestSearchBounds(unittest.TestCase):
    def test_delta_window_for_95_pop(self):
        lo, hi = iron_condor_short_delta_window(95.0)
        self.assertAlmostEqual(lo, 0.02, places=2)
        self.assertLessEqual(hi, 0.12)
        self.assertLess(lo, hi)

    def test_top_k_limits_pair_count(self):
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
        max_pairs = IC_TOP_K_SHORT_STRIKES * IC_TOP_K_SHORT_STRIKES
        self.assertLessEqual(len(pairs), max_pairs)

        window = iron_condor_delta_window(ctx)
        for sp, sc in pairs:
            qp = ctx.cache[(sp, "Put")]
            qc = ctx.cache[(sc, "Call")]
            self.assertGreaterEqual(abs(qp.delta or 0), window.lo)
            self.assertLessEqual(abs(qp.delta or 0), window.hi)
            self.assertGreaterEqual(abs(qc.delta or 0), window.lo)
            self.assertLessEqual(abs(qc.delta or 0), window.hi)

        max_evals = max_pairs * len(WING_WIDTH_MULTIPLIERS)
        survivors = sum(
            len(enumerate_symmetric_iron_condors(ctx, sp, sc)) for sp, sc in pairs
        )
        self.assertLessEqual(survivors, max_evals)


if __name__ == "__main__":
    unittest.main()
