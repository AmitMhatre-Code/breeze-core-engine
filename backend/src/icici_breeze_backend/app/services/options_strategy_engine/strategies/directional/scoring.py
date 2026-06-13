"""Finite conviction scoring for directional debit structures (no PoP in ranking)."""
from __future__ import annotations

import math
from typing import Iterable

from icici_breeze_backend.app.services.options_strategy_engine.delta_anchor import DELTA_TOLERANCE
from icici_breeze_backend.app.services.options_strategy_engine.greeks import theta_for_quote
from icici_breeze_backend.app.services.options_strategy_engine.types import EngineContext, QuoteRow

_EPS = 1e-6


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _geometric_mean(values: list[float]) -> float:
    positive = [v for v in values if v > 0]
    if not positive:
        return 0.0
    return math.exp(sum(math.log(v) for v in positive) / len(positive))


def delta_alignment(actual_abs_delta: float, target: float) -> float:
    return _clamp01(1.0 - abs(actual_abs_delta - target) / max(DELTA_TOLERANCE, _EPS))


def normalize_min_max(values: Iterable[float]) -> list[float]:
    vals = list(values)
    if not vals:
        return []
    lo = min(vals)
    hi = max(vals)
    if hi <= lo:
        return [1.0 for _ in vals]
    span = hi - lo
    return [(v - lo) / span for v in vals]


def score_long_option_components(
    ctx: EngineContext,
    q: QuoteRow,
    *,
    target_delta: float,
    premium_per_unit: float,
) -> dict[str, float]:
    abs_delta = abs(q.delta) if q.delta is not None else 0.0
    delta_align = delta_alignment(abs_delta, target_delta)
    raw_premium_eff = abs_delta / max(premium_per_unit, _EPS)
    theta = abs(theta_for_quote(ctx, q))
    theta_eff = 1.0 / (1.0 + theta / max(premium_per_unit, _EPS))
    liquidity = _clamp01(q.liquidity_score)
    return {
        "delta_alignment": round(delta_align, 6),
        "premium_efficiency_raw": round(raw_premium_eff, 6),
        "theta_efficiency": round(theta_eff, 6),
        "liquidity": round(liquidity, 6),
    }


def finalize_long_option_score(
    components: dict[str, float],
    *,
    premium_efficiency_norm: float,
) -> tuple[float, dict[str, float]]:
    breakdown = {
        "delta_alignment": components["delta_alignment"],
        "premium_efficiency": round(premium_efficiency_norm, 6),
        "theta_efficiency": components["theta_efficiency"],
        "liquidity": components["liquidity"],
    }
    score = (
        0.40 * breakdown["delta_alignment"]
        + 0.30 * breakdown["premium_efficiency"]
        + 0.20 * breakdown["liquidity"]
        + 0.10 * breakdown["theta_efficiency"]
    )
    return round(score, 6), breakdown


def score_spread_components(
    long_q: QuoteRow,
    short_q: QuoteRow,
    *,
    long_target: float,
    short_target: float,
    max_gain: float,
    max_loss: float,
    debit_paid: float,
) -> dict[str, float]:
    long_d = abs(long_q.delta) if long_q.delta is not None else 0.0
    short_d = abs(short_q.delta) if short_q.delta is not None else 0.0
    delta_align = (
        delta_alignment(long_d, long_target) + delta_alignment(short_d, short_target)
    ) / 2.0
    reward_risk = max_gain / max(max_loss, _EPS)
    capital_eff = max_gain / max(debit_paid, _EPS)
    liquidity = _geometric_mean(
        [_clamp01(long_q.liquidity_score), _clamp01(short_q.liquidity_score)]
    )
    return {
        "delta_alignment": round(delta_align, 6),
        "reward_to_risk_raw": round(reward_risk, 6),
        "capital_efficiency_raw": round(capital_eff, 6),
        "liquidity": round(liquidity, 6),
    }


def finalize_spread_score(
    components: dict[str, float],
    *,
    reward_risk_norm: float,
    capital_eff_norm: float,
) -> tuple[float, dict[str, float]]:
    breakdown = {
        "delta_alignment": components["delta_alignment"],
        "reward_to_risk": round(reward_risk_norm, 6),
        "capital_efficiency": round(capital_eff_norm, 6),
        "liquidity": components["liquidity"],
    }
    score = (
        0.40 * breakdown["delta_alignment"]
        + 0.30 * breakdown["reward_to_risk"]
        + 0.20 * breakdown["capital_efficiency"]
        + 0.10 * breakdown["liquidity"]
    )
    return round(score, 6), breakdown
