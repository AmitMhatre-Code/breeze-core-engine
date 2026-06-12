"""Black-Scholes IV and delta enrichment (Gemini §3.1)."""
from __future__ import annotations

import math

from icici_breeze_backend.app.services.iv_compute import DEFAULT_Q, DEFAULT_R, implied_volatility
from icici_breeze_backend.app.services.options_strategy_engine.helpers import sigma_for_pop
from icici_breeze_backend.app.services.options_strategy_engine.types import EngineContext, QuoteRow, Right


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_delta(
    spot: float,
    strike: float,
    t: float,
    sigma: float,
    right: Right,
    *,
    r: float = DEFAULT_R,
    q: float = DEFAULT_Q,
) -> float:
    if t <= 0 or sigma <= 0 or spot <= 0 or strike <= 0:
        if right == "Call":
            return 1.0 if spot > strike else 0.0
        return -1.0 if spot < strike else 0.0
    sqrt_t = math.sqrt(t)
    d1 = (math.log(spot / strike) + (r - q + 0.5 * sigma * sigma) * t) / (sigma * sqrt_t)
    call_delta = _norm_cdf(d1)
    return call_delta if right == "Call" else call_delta - 1.0


def prob_above_strike(
    spot: float,
    strike: float,
    t: float,
    sigma: float,
    *,
    r: float = DEFAULT_R,
    q: float = DEFAULT_Q,
) -> float:
    """Risk-neutral P(S_T > K) via N(d2)."""
    if t <= 0 or sigma <= 0 or spot <= 0 or strike <= 0:
        return 1.0 if spot > strike else 0.0
    sqrt_t = math.sqrt(t)
    d2 = (math.log(spot / strike) + (r - q - 0.5 * sigma * sigma) * t) / (sigma * sqrt_t)
    return _norm_cdf(d2)


def prob_below_strike(
    spot: float,
    strike: float,
    t: float,
    sigma: float,
    *,
    r: float = DEFAULT_R,
    q: float = DEFAULT_Q,
) -> float:
    return 1.0 - prob_above_strike(spot, strike, t, sigma, r=r, q=q)


def compute_atm_iv(ctx: EngineContext) -> float | None:
    t = ctx.t_years
    ce = ctx.cache.get((ctx.atm_strike, "Call"))
    pe = ctx.cache.get((ctx.atm_strike, "Put"))
    ivs: list[float] = []
    for q, opt in ((ce, "call"), (pe, "put")):
        if not q:
            continue
        px = q.ltp or q.best_offer_price or q.best_bid_price
        if px > 0:
            iv = implied_volatility(px, ctx.spot, ctx.atm_strike, t, opt)
            if iv:
                ivs.append(iv)
    if not ivs:
        return None
    return sum(ivs) / len(ivs)


def enrich_greeks(ctx: EngineContext) -> None:
    """Back out IV and delta for every liquid quote in cache."""
    t = ctx.t_years
    fallback_sigma = sigma_for_pop(ctx)
    max_oi = max((q.oi for q in ctx.cache.values() if q.liquid), default=1) or 1
    max_depth = max(
        (min(q.total_buy_qty, q.total_sell_qty) for q in ctx.cache.values() if q.liquid),
        default=1,
    ) or 1

    for q in ctx.cache.values():
        if not q.liquid:
            continue
        px = q.mid_price
        opt = "call" if q.right == "Call" else "put"
        iv = implied_volatility(px, ctx.spot, q.strike, t, opt) if px > 0 else None
        q.iv = iv
        sigma = iv if iv and iv > 0 else fallback_sigma
        q.delta = bs_delta(ctx.spot, float(q.strike), t, sigma, q.right)
        spread_penalty = 1.0 / (1.0 + q.spread) if q.spread > 0 else 1.0
        q.liquidity_score = (
            0.4 * (q.oi / max_oi)
            + 0.4 * (min(q.total_buy_qty, q.total_sell_qty) / max_depth)
            + 0.2 * spread_penalty
        )
