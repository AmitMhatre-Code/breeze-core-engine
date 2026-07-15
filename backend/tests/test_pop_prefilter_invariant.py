"""PoP pre-filter tolerance and monotonic search inclusion invariants."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from icici_breeze_backend.app.services.options_strategy_engine.helpers import pre_filter_pop_floor
from icici_breeze_backend.app.services.options_strategy_engine.pop import pop_detail_for_legs
from icici_breeze_backend.app.services.options_strategy_engine.strategies.income._common import (
    estimate_short_strangle_pop,
    iter_pop_band_expansions,
    pop_for_short_strike,
)
from icici_breeze_backend.app.services.options_strategy_engine.strategies.income.bear_call_spread import (
    enumerate_bear_call_spreads,
)
from icici_breeze_backend.app.services.options_strategy_engine.strategies.income.bull_put_spread import (
    enumerate_bull_put_spreads,
)
from icici_breeze_backend.app.services.options_strategy_engine.strategies.income.iron_condor import (
    enumerate_symmetric_iron_condors,
    iron_condor_short_pairs,
)
from icici_breeze_backend.app.services.options_strategy_engine.strategies.income.short_strangle import (
    enumerate_short_strangles,
    short_strangle_pairs,
)
from icici_breeze_backend.app.services.options_strategy_engine.types import (
    POP_PRE_FILTER_TOLERANCE,
    EngineContext,
    QuoteRow,
    TradeLeg,
)
from icici_breeze_backend.audit.strategy_evaluation_audit import StrategyAuditCollector


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
    min_pop_pct: float,
    spot: float = 23623.0,
) -> EngineContext:
    strikes = sorted({s for s, _ in cache})
    return EngineContext(
        processor=MagicMock(),
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
        strikes=strikes,
        strike_step=50,
        search_interval=50,
        spot=spot,
        atm_strike=23600,
        atm_iv=0.15,
        min_ann_return_pct=0.0,
        cache=cache,
    )


def _high_pop_gap_cache() -> dict:
    """Single-leg PoP ~98.7% but full IC/strangle PoP >= 99%."""
    spot = 23623.0
    strikes = list(range(22500, 24800, 50))
    cache = {
        (s, r): _quote(s, r, bid=3.0, ask=3.1, delta=0.05, spot=spot)
        for s in strikes
        for r in ("Call", "Put")
    }
    short_put, short_call = 23100, 24100
    cache[(short_put, "Put")] = _quote(short_put, "Put", bid=8.0, ask=8.1, delta=0.013, spot=spot)
    cache[(short_call, "Call")] = _quote(short_call, "Call", bid=8.0, ask=8.1, delta=0.013, spot=spot)
    cache[(23050, "Put")] = _quote(23050, "Put", bid=0.5, ask=0.6, delta=0.008, spot=spot)
    cache[(24150, "Call")] = _quote(24150, "Call", bid=0.5, ask=0.6, delta=0.008, spot=spot)
    cache[(23000, "Put")] = _quote(23000, "Put", bid=0.4, ask=0.5, delta=0.007, spot=spot)
    cache[(24200, "Call")] = _quote(24200, "Call", bid=0.4, ask=0.5, delta=0.007, spot=spot)
    return cache


def _bps_gap_cache() -> dict:
    spot = 23623.0
    short_strike, long_strike = 23550, 23500
    return {
        (short_strike, "Put"): _quote(short_strike, "Put", bid=12.0, ask=12.1, delta=0.013, spot=spot),
        (long_strike, "Put"): _quote(long_strike, "Put", bid=0.3, ask=0.4, delta=0.005, spot=spot),
    }


class TestPreFilterHelpers(unittest.TestCase):
    def test_pre_filter_pop_floor_uses_tolerance(self):
        self.assertEqual(POP_PRE_FILTER_TOLERANCE, 2.0)
        self.assertEqual(pre_filter_pop_floor(99.0), 97.0)
        self.assertEqual(pre_filter_pop_floor(1.0), 0.0)

    def test_iter_pop_band_expansions_uses_soft_floor(self):
        floor, ceiling = next(iter_pop_band_expansions(99.0))
        self.assertEqual(floor, 97.0)
        self.assertEqual(ceiling, 100.0)


class TestHighMinPopRegression(unittest.TestCase):
    def test_fixture_has_estimate_actual_gap(self):
        """Single-leg PoP ~98.9% but full strangle PoP >= 99%, under the breakeven-based model.

        Deliberately a separate, dedicated cache (not `_high_pop_gap_cache`): the breakeven
        model ties leg PoP to strike distance/premium/sigma directly, so reproducing the
        estimate/actual gap needs a put strike close enough to spot to keep the single-leg
        PoP under the floor, with a wide net credit to keep the paired PoP over it.
        """
        spot = 23623.0
        short_put, short_call = 23250, 24300
        cache = {
            (short_put, "Put"): _quote(short_put, "Put", bid=45.0, ask=45.1, spot=spot),
            (short_call, "Call"): _quote(short_call, "Call", bid=45.0, ask=45.1, spot=spot),
        }
        ctx = _ctx_from_cache(cache, min_pop_pct=99.0, spot=spot)
        leg_pop = pop_for_short_strike(ctx, short_put, "Put")
        pair_pop = estimate_short_strangle_pop(ctx, short_put, short_call)
        self.assertLess(leg_pop, 99.0)
        self.assertGreaterEqual(pair_pop, 99.0)

    def test_min_pop_99_returns_iron_condor_above_floor(self):
        cache = _high_pop_gap_cache()
        ctx = _ctx_from_cache(cache, min_pop_pct=99.0)
        short_put, short_call = 23100, 24100
        pairs = iron_condor_short_pairs(ctx)
        self.assertIn((short_put, short_call), pairs)
        survivors = enumerate_symmetric_iron_condors(ctx, short_put, short_call)
        self.assertGreater(len(survivors), 0)
        self.assertGreaterEqual(survivors[0].pop, 99.0)

    def test_min_pop_99_returns_short_strangle_above_floor(self):
        cache = _high_pop_gap_cache()
        ctx = _ctx_from_cache(cache, min_pop_pct=99.0)
        short_put, short_call = 23100, 24100
        pairs = short_strangle_pairs(ctx)
        self.assertIn((short_put, short_call), pairs)
        survivors = enumerate_short_strangles(ctx, short_put, short_call)
        self.assertEqual(len(survivors), 1)
        self.assertGreaterEqual(survivors[0].pop, 99.0)


class TestMinPopMonotonicInclusion(unittest.TestCase):
    """Search at min_pop=X must not drop survivors that X-1 returned with final PoP >= X."""

    X = 99.0

    def _signatures_ic(self, ctx: EngineContext) -> dict[tuple, float]:
        out: dict[tuple, float] = {}
        for sp, sc in iron_condor_short_pairs(ctx):
            for cand in enumerate_symmetric_iron_condors(ctx, sp, sc):
                key = (cand.short_put, cand.short_call, cand.long_put, cand.long_call)
                out[key] = cand.pop
        return out

    def _signatures_strangle(self, ctx: EngineContext) -> dict[tuple, float]:
        out: dict[tuple, float] = {}
        for sp, sc in short_strangle_pairs(ctx):
            for cand in enumerate_short_strangles(ctx, sp, sc):
                key = (cand.short_put, cand.short_call)
                out[key] = cand.pop
        return out

    def _signatures_bps(self, ctx: EngineContext) -> dict[tuple, float]:
        out: dict[tuple, float] = {}
        for short in ctx.liquid_pe_strikes:
            for cand in enumerate_bull_put_spreads(ctx, short):
                key = (cand.short_strike, cand.long_strike)
                out[key] = cand.pop
        return out

    def _signatures_bcs(self, ctx: EngineContext) -> dict[tuple, float]:
        out: dict[tuple, float] = {}
        for short in ctx.liquid_ce_strikes:
            for cand in enumerate_bear_call_spreads(ctx, short):
                key = (cand.short_strike, cand.long_strike)
                out[key] = cand.pop
        return out

    def _assert_monotonic(
        self,
        lo_cache: dict,
        hi_cache: dict | None,
        signatures_fn,
    ) -> None:
        lo_ctx = _ctx_from_cache(lo_cache, min_pop_pct=self.X - 1)
        hi_ctx = _ctx_from_cache(hi_cache or lo_cache, min_pop_pct=self.X)
        lo_survivors = signatures_fn(lo_ctx)
        hi_survivors = signatures_fn(hi_ctx)
        qualifying = {k: p for k, p in lo_survivors.items() if p >= self.X}
        self.assertGreater(len(qualifying), 0, "fixture must yield survivors at X-1 with final PoP >= X")
        for key, pop in qualifying.items():
            self.assertIn(
                key,
                hi_survivors,
                f"min_pop={self.X} excluded {key} (final PoP {pop:.2f}% at min_pop={self.X - 1})",
            )
            self.assertGreaterEqual(hi_survivors[key], self.X)

    def test_iron_condor_monotonic(self):
        cache = _high_pop_gap_cache()
        self._assert_monotonic(cache, None, self._signatures_ic)

    def test_short_strangle_monotonic(self):
        cache = _high_pop_gap_cache()
        self._assert_monotonic(cache, None, self._signatures_strangle)

    def test_bull_put_spread_monotonic(self):
        cache = _bps_gap_cache()
        ctx = _ctx_from_cache(cache, min_pop_pct=self.X - 1)
        short_strike = 23550
        legs = [
            TradeLeg("Put", "Sell", short_strike, 65, 12.0),
            TradeLeg("Put", "Buy", 23500, 65, 0.35),
        ]
        pop = pop_detail_for_legs(ctx, legs).pop_pct
        if pop < self.X:
            self.skipTest("BPS fixture does not reach target PoP; IC/strangle cover the gap case")
        self._assert_monotonic(cache, None, self._signatures_bps)

    def test_bear_call_spread_monotonic(self):
        spot = 23623.0
        short_strike, long_strike = 23650, 23700
        cache = {
            (short_strike, "Call"): _quote(short_strike, "Call", bid=12.0, ask=12.1, delta=0.013, spot=spot),
            (long_strike, "Call"): _quote(long_strike, "Call", bid=0.3, ask=0.4, delta=0.005, spot=spot),
        }
        ctx = _ctx_from_cache(cache, min_pop_pct=self.X - 1)
        legs = [
            TradeLeg("Call", "Sell", short_strike, 65, 12.0),
            TradeLeg("Call", "Buy", long_strike, 65, 0.35),
        ]
        pop = pop_detail_for_legs(ctx, legs).pop_pct
        if pop < self.X:
            self.skipTest("BCS fixture does not reach target PoP; IC/strangle cover the gap case")
        self._assert_monotonic(cache, None, self._signatures_bcs)


class TestPopAuditTelemetry(unittest.TestCase):
    def test_record_evaluation_includes_estimate_and_actual(self):
        collector = StrategyAuditCollector()
        collector.min_pop_pct = 99.0
        collector.record_evaluation(
            outcome="rejected",
            reject_reason="pop_floor",
            pop_pct=98.5,
            estimated_pop=99.1,
            rejected_stage="pop_floor",
        )
        entry = collector.evaluations[-1]
        self.assertEqual(entry["actual_pop"], 98.5)
        self.assertEqual(entry["estimated_pop"], 99.1)
        self.assertEqual(entry["rejected_stage"], "pop_floor")

    def test_accepted_evaluation_has_null_rejected_stage(self):
        collector = StrategyAuditCollector()
        collector.record_evaluation(
            outcome="accepted",
            pop_pct=99.35,
            estimated_pop=98.7,
        )
        entry = collector.evaluations[-1]
        self.assertEqual(entry["actual_pop"], 99.35)
        self.assertEqual(entry["estimated_pop"], 98.7)
        self.assertIsNone(entry["rejected_stage"])


if __name__ == "__main__":
    unittest.main()
