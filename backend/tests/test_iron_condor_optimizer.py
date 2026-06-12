"""Regression tests for iron condor optimizer revamp."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from icici_breeze_backend.app.services.options_strategy_engine.ranking import score_iron_condor_candidate
from icici_breeze_backend.app.services.options_strategy_engine.strategies.base import (
    IronCondorCandidate,
    evaluate_symmetric_iron_condor,
)
from icici_breeze_backend.app.services.options_strategy_engine.strategies.income.iron_condor import (
    _pick_winner,
    calc_iron_condor,
)
from icici_breeze_backend.app.services.options_strategy_engine.types import (
    EngineContext,
    QuoteRow,
    TOP_K_SHORT_STRIKES,
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


class TestEvaluateSymmetricIronCondor(unittest.TestCase):
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

    def test_prefers_wider_wing_when_credit_higher(self):
        strikes = list(range(22700, 24600, 50))
        cache = _fill_strikes(
            strikes,
            23623.0,
            bid_fn=lambda s, r: 8.0 if s in (22800, 24400) else 4.0,
            ask_fn=lambda s, r: 8.5 if s in (22800, 24400) else 4.5,
            delta_fn=lambda s, r: 0.20,
        )
        cache[(22750, "Put")] = _quote(22750, "Put", bid=7.9, ask=8.0, delta=0.18)
        cache[(22700, "Put")] = _quote(22700, "Put", bid=2.0, ask=2.5, delta=0.17)
        cache[(24450, "Call")] = _quote(24450, "Call", bid=7.9, ask=8.0, delta=0.18)
        cache[(24500, "Call")] = _quote(24500, "Call", bid=2.0, ask=2.5, delta=0.17)
        ctx = _ctx_from_cache(cache, min_pop_pct=30.0)
        result = evaluate_symmetric_iron_condor(ctx, 22800, 24400)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.wing_width, 100)
        self.assertGreater(result.put_credit, 0)
        self.assertGreater(result.call_credit, 0)


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
        """Regression: 22200/22150 PE debit wing must not win when better condor exists."""
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
        ctx = _ctx_from_cache(
            cache,
            min_pop_pct=50.0,
            processor=proc,
        )

        from unittest.mock import patch

        with patch(
            "icici_breeze_backend.app.services.options_strategy_engine.strategies.income.iron_condor.MIN_IC_ANNUALIZED_RETURN_PCT",
            0.0,
        ):
            result = calc_iron_condor(ctx)

        self.assertEqual(result.status, "ok")
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
        ctx = _ctx_from_cache(
            {},
            spot=23623.0,
            processor=proc,
        )
        ctx.strikes = [22800, 22900, 23250, 24400, 24500]

        def _cand(short_put: int, proxy: float, premium: float) -> IronCondorCandidate:
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
                proxy_score=proxy,
                net_collected=premium,
            )

        high_proxy = _cand(23250, proxy=100.0, premium=10_000.0)
        low_span = _cand(22900, proxy=50.0, premium=8_000.0)

        winner, best_return, scores = _pick_winner(
            ctx, [high_proxy, low_span], strategy_id="iron_condor"
        )
        self.assertIsNotNone(winner)
        assert winner is not None
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
        result = calc_iron_condor(ctx)
        self.assertEqual(result.status, "skipped")
        self.assertIn("annualized return", (result.skip_reason or "").lower())


class TestSearchBounds(unittest.TestCase):
    def test_top_k_limits_pair_count(self):
        strikes = list(range(22000, 25100, 50))
        cache = _fill_strikes(
            strikes,
            23623.0,
            bid_fn=lambda s, r: 5.0,
            ask_fn=lambda s, r: 5.2,
            delta_fn=lambda s, r: 0.20,
        )
        ctx = _ctx_from_cache(cache, min_pop_pct=50.0)
        from icici_breeze_backend.app.services.options_strategy_engine.pruning import (
            iron_condor_short_pairs,
        )

        pairs = iron_condor_short_pairs(ctx)
        max_pairs = TOP_K_SHORT_STRIKES * TOP_K_SHORT_STRIKES
        self.assertLessEqual(len(pairs), max_pairs)
        max_evals = max_pairs * len(WING_WIDTH_MULTIPLIERS)
        survivors = sum(
            1
            for sp, sc in pairs
            if evaluate_symmetric_iron_condor(ctx, sp, sc) is not None
        )
        self.assertLessEqual(survivors, max_evals)


if __name__ == "__main__":
    unittest.main()
