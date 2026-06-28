"""Black-Scholes IV and delta enrichment (Gemini §3.1)."""
from __future__ import annotations

import math
from typing import Literal

from icici_breeze_backend.app.services.iv_compute import DEFAULT_Q, DEFAULT_R, implied_volatility
from icici_breeze_backend.app.services.options_strategy_engine.helpers import sigma_for_pop
from icici_breeze_backend.app.services.options_strategy_engine.types import EngineContext, QuoteRow, Right

SnapPrefer = Literal["floor", "ceil", "nearest"]


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def norm_ppf(p: float) -> float:
    """Inverse standard normal CDF (Acklam rational approximation)."""
    p = min(max(p, 1e-12), 1.0 - 1e-12)

    a = (
        -3.969683028665376e01,
        2.209460984245205e02,
        -2.759285104469687e02,
        1.383577518672690e02,
        -3.066479806614716e01,
        2.506628277459239e00,
    )
    b = (
        -5.447609879822406e01,
        1.615858368580409e02,
        -1.556989798598866e02,
        6.680131188771972e01,
        -1.328068155288572e01,
    )
    c = (
        -7.784894002430293e-03,
        -3.223964580411365e-01,
        -2.400758277161838e00,
        -2.549732539343734e00,
        4.374664141464968e00,
        2.938163982698783e00,
    )
    d = (
        7.784695709041462e-03,
        3.224671290700398e-01,
        2.445134137142996e00,
        3.754408661907416e00,
    )

    plow = 0.02425
    if p < plow:
        q = math.sqrt(-2.0 * math.log(p))
        num = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5])
        den = ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
        return num / den
    if p > 1.0 - plow:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        num = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5])
        den = ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
        return -(num / den)

    q = p - 0.5
    r = q * q
    num = (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q
    den = (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)
    return num / den


def _bs_d1(
    spot: float,
    strike: float,
    t: float,
    sigma: float,
    *,
    r: float = DEFAULT_R,
    q: float = DEFAULT_Q,
) -> float | None:
    if t <= 0 or sigma <= 0 or spot <= 0 or strike <= 0:
        return None
    sqrt_t = math.sqrt(t)
    return (math.log(spot / strike) + (r - q + 0.5 * sigma * sigma) * t) / (sigma * sqrt_t)


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
    d1 = _bs_d1(spot, strike, t, sigma, r=r, q=q)
    if d1 is None:
        return 0.0
    call_delta = _norm_cdf(d1)
    return call_delta if right == "Call" else call_delta - 1.0


def bs_gamma(
    spot: float,
    strike: float,
    t: float,
    sigma: float,
    *,
    r: float = DEFAULT_R,
    q: float = DEFAULT_Q,
) -> float:
    """Black-Scholes gamma (mirrors frontend blackScholes.ts bsGamma)."""
    if t <= 0 or sigma <= 0 or spot <= 0 or strike <= 0:
        return 0.0
    d1 = _bs_d1(spot, strike, t, sigma, r=r, q=q)
    if d1 is None:
        return 0.0
    sqrt_t = math.sqrt(t)
    return (math.exp(-q * t) * _norm_pdf(d1)) / (spot * sigma * sqrt_t)


def bs_theta(
    spot: float,
    strike: float,
    t: float,
    sigma: float,
    right: Right,
    *,
    r: float = DEFAULT_R,
    q: float = DEFAULT_Q,
) -> float:
    """Black-Scholes theta per calendar year (negative for long options)."""
    if t <= 0 or sigma <= 0 or spot <= 0 or strike <= 0:
        return 0.0
    sqrt_t = math.sqrt(t)
    d1 = _bs_d1(spot, strike, t, sigma, r=r, q=q)
    if d1 is None:
        return 0.0
    d2 = d1 - sigma * sqrt_t
    disc_q = math.exp(-q * t)
    disc_r = math.exp(-r * t)
    pdf_d1 = math.exp(-0.5 * d1 * d1) / math.sqrt(2.0 * math.pi)
    common = -(spot * disc_q * pdf_d1 * sigma) / (2.0 * sqrt_t)
    if right == "Call":
        return common - r * strike * disc_r * _norm_cdf(d2) + q * spot * disc_q * _norm_cdf(d1)
    return common + r * strike * disc_r * _norm_cdf(-d2) - q * spot * disc_q * _norm_cdf(-d1)


def theta_for_quote(ctx: EngineContext, q: QuoteRow) -> float:
    """Theta for a cached quote using its IV or fallback sigma."""
    t = ctx.t_years
    sigma = q.iv if q.iv and q.iv > 0 else sigma_for_pop(ctx)
    return bs_theta(ctx.spot, float(q.strike), t, sigma, q.right)


def strike_for_abs_delta(
    spot: float,
    t: float,
    sigma: float,
    right: Right,
    target_abs_delta: float,
    *,
    r: float = DEFAULT_R,
    q: float = DEFAULT_Q,
) -> float:
    """Invert Black-Scholes delta to an expected strike (continuous K)."""
    if t <= 0 or sigma <= 0 or spot <= 0 or target_abs_delta <= 0:
        return spot
    target = min(max(target_abs_delta, 1e-6), 0.999)
    if right == "Call":
        d1 = norm_ppf(target)
    else:
        d1 = norm_ppf(1.0 - target)
    sqrt_t = math.sqrt(t)
    ln_sk = d1 * sigma * sqrt_t - (r - q + 0.5 * sigma * sigma) * t
    return spot * math.exp(-ln_sk)


def snap_strike(
    strikes: list[int],
    k: float,
    *,
    prefer: SnapPrefer = "nearest",
) -> int | None:
    """Map continuous strike K to the scrip-master grid."""
    if not strikes:
        return None
    if prefer == "nearest":
        return min(strikes, key=lambda s: abs(s - k))
    ordered = sorted(strikes)
    if prefer == "floor":
        candidates = [s for s in ordered if s <= k]
        return candidates[-1] if candidates else ordered[0]
    candidates = [s for s in ordered if s >= k]
    return candidates[0] if candidates else ordered[-1]


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
