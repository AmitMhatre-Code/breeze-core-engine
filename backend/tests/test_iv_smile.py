"""Trust-gated per-strike IV smile: interpolation, memoization, and the deep-OTM PoP-inflation
regression this module fixes (flat ATM sigma understating deep-OTM strike probabilities)."""
from __future__ import annotations

import math
import unittest
from unittest.mock import MagicMock

from icici_breeze_backend.app.services.options_strategy_engine.iv_smile import (
    MAX_TRUSTED_REL_SPREAD,
    build_iv_smile,
    get_iv_smile,
    is_trusted_quote,
    resolve_leg_sigma,
    sigma_for_strike,
)
from icici_breeze_backend.app.services.options_strategy_engine.pop import pop_detail_for_legs, pop_short_otm_expiry
from icici_breeze_backend.app.services.options_strategy_engine.types import EngineContext, QuoteRow, TradeLeg


def _quote(
    strike: int,
    right: str,
    *,
    bid: float,
    ask: float,
    iv: float | None,
    delta: float | None = None,
    spot: float = 23623.0,
) -> QuoteRow:
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
        delta=delta,
        iv=iv,
    )


def _ctx_from_cache(cache: dict, *, spot: float = 23623.0, atm_iv: float = 0.15) -> EngineContext:
    strikes = sorted({s for s, _ in cache})
    return EngineContext(
        processor=MagicMock(),
        user_id="u1",
        stock_code="NIFTY",
        exchange_code="NFO",
        expiry_display="16-Jun-2026",
        margin_rupees=50_000_000,
        max_loss_rupees=4_000_000,
        min_pop_pct=65.0,
        provision_elm=False,
        strategy_category="income",
        lot_size=65,
        strikes=strikes,
        strike_step=50,
        search_interval=50,
        spot=spot,
        atm_strike=23600,
        atm_iv=atm_iv,
        cache=cache,
    )


class TestIsTrustedQuote(unittest.TestCase):
    def test_spread_at_cap_is_trusted(self):
        # bid=9.5, ask=10.5 -> mid=10, spread=1.0, rel=0.10 == cap
        q = _quote(23000, "Put", bid=9.5, ask=10.5, iv=0.18)
        self.assertAlmostEqual((q.best_offer_price - q.best_bid_price) / q.mid_price, MAX_TRUSTED_REL_SPREAD)
        self.assertTrue(is_trusted_quote(q))

    def test_spread_just_over_cap_is_untrusted(self):
        q = _quote(23000, "Put", bid=9.4, ask=10.5, iv=0.18)
        self.assertGreater((q.best_offer_price - q.best_bid_price) / q.mid_price, MAX_TRUSTED_REL_SPREAD)
        self.assertFalse(is_trusted_quote(q))

    def test_illiquid_quote_is_untrusted(self):
        q = _quote(23000, "Put", bid=9.5, ask=9.6, iv=0.18)
        q.total_sell_qty = 0
        self.assertFalse(is_trusted_quote(q))

    def test_missing_iv_is_untrusted(self):
        q = _quote(23000, "Put", bid=9.5, ask=9.6, iv=None)
        self.assertFalse(is_trusted_quote(q))

    def test_zero_iv_is_untrusted(self):
        q = _quote(23000, "Put", bid=9.5, ask=9.6, iv=0.0)
        self.assertFalse(is_trusted_quote(q))

    def test_one_sided_quote_is_untrusted(self):
        q = _quote(23000, "Put", bid=0.0, ask=9.6, iv=0.18)
        self.assertFalse(is_trusted_quote(q))


class TestBuildIvSmile(unittest.TestCase):
    def test_filters_sorts_and_splits_by_side(self):
        cache = {
            (23000, "Put"): _quote(23000, "Put", bid=9.5, ask=9.6, iv=0.20),
            (22500, "Put"): _quote(22500, "Put", bid=9.5, ask=9.6, iv=0.25),
            # wide relative spread -> excluded despite being liquid
            (22000, "Put"): _quote(22000, "Put", bid=0.4, ask=0.6, iv=0.35),
            (24000, "Call"): _quote(24000, "Call", bid=9.5, ask=9.6, iv=0.16),
        }
        ctx = _ctx_from_cache(cache)
        smile = build_iv_smile(ctx)
        put_strikes = [round(math.exp(x) * ctx.spot) for x, _ in smile["Put"]]
        self.assertEqual(put_strikes, [22500, 23000])  # ascending by x, 22000 excluded
        self.assertEqual([iv for _, iv in smile["Put"]], [0.25, 0.20])
        self.assertEqual(len(smile["Call"]), 1)


class TestSigmaForStrike(unittest.TestCase):
    curve = [(-0.04868, 0.25), (-0.02673, 0.20)]

    def test_interpolates_between_anchors(self):
        x = math.log(22750 / 23623.0)
        expected_t = (x - self.curve[0][0]) / (self.curve[1][0] - self.curve[0][0])
        expected = self.curve[0][1] + expected_t * (self.curve[1][1] - self.curve[0][1])
        got = sigma_for_strike(self.curve, 22750, 23623.0, fallback=0.15)
        self.assertAlmostEqual(got, expected, places=6)
        self.assertTrue(self.curve[1][1] < got < self.curve[0][1])

    def test_flat_clamps_below_lowest_anchor(self):
        got = sigma_for_strike(self.curve, 20000, 23623.0, fallback=0.15)
        self.assertEqual(got, self.curve[0][1])

    def test_flat_clamps_above_highest_anchor(self):
        got = sigma_for_strike(self.curve, 23623.0, 23623.0, fallback=0.15)
        self.assertEqual(got, self.curve[1][1])

    def test_falls_back_with_fewer_than_two_anchors(self):
        self.assertEqual(sigma_for_strike([], 22750, 23623.0, fallback=0.15), 0.15)
        self.assertEqual(sigma_for_strike([(0.0, 0.2)], 22750, 23623.0, fallback=0.15), 0.15)


class TestGetIvSmileMemoization(unittest.TestCase):
    def test_same_ctx_returns_identical_cached_object(self):
        cache = {
            (23000, "Put"): _quote(23000, "Put", bid=9.5, ask=9.6, iv=0.20),
            (22500, "Put"): _quote(22500, "Put", bid=9.5, ask=9.6, iv=0.25),
        }
        ctx = _ctx_from_cache(cache)
        first = get_iv_smile(ctx)
        ctx.cache[(22000, "Put")] = _quote(22000, "Put", bid=9.5, ask=9.6, iv=0.30)
        second = get_iv_smile(ctx)
        self.assertIs(first, second)  # not rebuilt despite cache mutation


class TestDeepOtmSkewRegression(unittest.TestCase):
    """Reproduces the reported bug: a deep-OTM short put resolves a materially higher sigma
    (and therefore lower, more accurate PoP) once the put-side skew is honored, instead of the
    flat ATM sigma that previously inflated PoP for this exact shape of leg."""

    def _skewed_cache(self) -> dict:
        return {
            (23000, "Put"): _quote(23000, "Put", bid=9.5, ask=9.6, iv=0.20),
            (22500, "Put"): _quote(22500, "Put", bid=9.5, ask=9.6, iv=0.25),
        }

    def test_resolve_leg_sigma_exceeds_atm_for_deep_otm_put(self):
        ctx = _ctx_from_cache(self._skewed_cache(), atm_iv=0.15)
        resolved = resolve_leg_sigma(ctx, 22750, "Put")
        self.assertGreater(resolved, ctx.atm_iv)

    def test_deep_otm_short_put_pop_is_lower_than_flat_atm_would_give(self):
        cache = self._skewed_cache()
        ctx = _ctx_from_cache(cache, atm_iv=0.15)
        leg = TradeLeg("Put", "Sell", 22750, 65, 5.0)
        # leg's own strike is deliberately absent from ctx.cache -> exercises the smile
        # fallback path via resolve_leg_sigma, matching a thin/uncached deep-OTM quote.
        detail = pop_detail_for_legs(ctx, [leg])
        self.assertEqual(detail.basis, "short_strike_otm")

        # PoP boundary is the strike itself (P of OTM expiry), so the leg's own strike sigma
        # governs; honoring the put-side skew raises that sigma and lowers PoP vs flat ATM.
        skewed_sigma = resolve_leg_sigma(ctx, float(leg.strike), "Put")
        skewed_pop = pop_short_otm_expiry(ctx.spot, float(leg.strike), "Put", ctx.t_years, skewed_sigma)
        flat_atm_pop = pop_short_otm_expiry(ctx.spot, float(leg.strike), "Put", ctx.t_years, ctx.atm_iv)

        self.assertAlmostEqual(detail.pop_pct, skewed_pop, places=6)
        self.assertLess(skewed_pop, flat_atm_pop)


if __name__ == "__main__":
    unittest.main()
