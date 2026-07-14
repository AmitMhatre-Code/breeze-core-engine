"""Per-side implied-vol smile: trust-gated per-strike IV anchors, linear interpolation in
log-moneyness space. Deep-OTM index strikes trade at meaningfully higher IV than ATM (skew);
feeding flat ATM sigma into a deep-OTM strike's probability formula understates the true
probability of the underlying reaching that strike. This resolves each strike's own sigma
from nearby trustworthy quotes instead."""
from __future__ import annotations

import math

from icici_breeze_backend.app.services.options_strategy_engine.helpers import sigma_for_pop
from icici_breeze_backend.app.services.options_strategy_engine.types import EngineContext, QuoteRow, Right

MAX_TRUSTED_REL_SPREAD = 0.10

SmileCurve = list[tuple[float, float]]  # sorted ascending by x = ln(strike/spot); (x, iv)


def is_trusted_quote(q: QuoteRow) -> bool:
    """Stricter than QuoteRow.liquid: caps relative bid/ask spread so this strike's own IV
    inversion is numerically stable enough to serve as a smile anchor. In ADDITION to
    `.liquid` (both-side depth > 0), not a replacement for it — thin, wide-relative-spread,
    near-zero-premium quotes can pass `.liquid` while still producing a noisy IV inversion."""
    if not q.liquid:
        return False
    if q.iv is None or q.iv <= 0:
        return False
    if q.best_bid_price <= 0 or q.best_offer_price <= 0:
        return False
    mid = q.mid_price
    if mid <= 0:
        return False
    return (q.spread / mid) <= MAX_TRUSTED_REL_SPREAD


def build_iv_smile(ctx: EngineContext) -> dict[Right, SmileCurve]:
    """One pass over ctx.cache. Requires q.iv already populated (run after enrich_greeks's
    IV-inversion pass)."""
    by_right: dict[Right, list[tuple[float, float]]] = {"Call": [], "Put": []}
    if ctx.spot <= 0:
        return by_right
    for q in ctx.cache.values():
        if not is_trusted_quote(q):
            continue
        x = math.log(float(q.strike) / ctx.spot)
        by_right[q.right].append((x, float(q.iv)))
    for right in by_right:
        by_right[right].sort(key=lambda p: p[0])
    return by_right


def get_iv_smile(ctx: EngineContext) -> dict[Right, SmileCurve]:
    """Memoized on ctx.iv_smile_cache — built once per EngineContext, reused across every
    pop_detail_for_legs() call for every candidate strategy evaluated in one propose-trades
    run (the engine can evaluate hundreds/thousands of candidates; rebuilding per-lookup would
    multiply an O(chain size) loop by candidate count)."""
    if ctx.iv_smile_cache is None:
        ctx.iv_smile_cache = build_iv_smile(ctx)
    return ctx.iv_smile_cache


def sigma_for_strike(curve: SmileCurve, strike: float, spot: float, fallback: float) -> float:
    """Linear interpolation in log-moneyness space; flat-clamp beyond the outermost anchor;
    fall back to `fallback` (ATM iv, else 0.20) when the side has fewer than 2 trusted anchors."""
    if len(curve) < 2 or spot <= 0 or strike <= 0:
        return fallback
    x = math.log(strike / spot)
    if x <= curve[0][0]:
        return curve[0][1]
    if x >= curve[-1][0]:
        return curve[-1][1]
    lo = curve[0]
    for hi in curve[1:]:
        if lo[0] <= x <= hi[0]:
            if hi[0] == lo[0]:
                return lo[1]
            t = (x - lo[0]) / (hi[0] - lo[0])
            return lo[1] + t * (hi[1] - lo[1])
        lo = hi
    return fallback  # unreachable given the bounds checks above


def resolve_leg_sigma(ctx: EngineContext, strike_or_price_level: float, right: Right) -> float:
    """Sigma for one strike (or an arbitrary price level, e.g. a breakeven — same lookup,
    since the smile is indexed by log-moneyness of a price, not literally by chain strike)."""
    curve = get_iv_smile(ctx).get(right, [])
    return sigma_for_strike(curve, float(strike_or_price_level), ctx.spot, sigma_for_pop(ctx))
