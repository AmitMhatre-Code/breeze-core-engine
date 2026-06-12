"""Candidate ranking and capital efficiency (Gemini §7.1)."""
from __future__ import annotations

import math

from icici_breeze_backend.app.services.options_strategy_engine.helpers import (
    annualized_carry_percent_on_span,
)
from icici_breeze_backend.app.services.options_strategy_engine.pop import expected_value_heuristic
from icici_breeze_backend.app.services.options_strategy_engine.types import QuoteRow


def capital_efficiency_ratio(expected_value: float, capital_utilized: float) -> float:
    if capital_utilized <= 0:
        return expected_value
    return expected_value / capital_utilized


def score_credit_trade(
    pop_pct: float,
    net_premium: float,
    max_loss: float,
    span_margin: float | None = None,
) -> float:
    ev = expected_value_heuristic(pop_pct, net_premium, max_loss)
    capital = span_margin if span_margin and span_margin > 0 else max(net_premium, 1.0)
    return capital_efficiency_ratio(ev, capital)


def score_debit_trade(
    pop_pct: float,
    max_profit: float,
    max_loss: float,
) -> float:
    return expected_value_heuristic(pop_pct, max_profit, max_loss)


def score_directional_candidate(
    pop_pct: float,
    max_profit: float,
    max_loss: float,
) -> float:
    """Capital Efficiency Ratio for directional debit structures."""
    ev = expected_value_heuristic(pop_pct, max_profit, max_loss)
    capital = max(max_loss, 1.0)
    return capital_efficiency_ratio(ev, capital)


def score_iron_condor_candidate(
    pop_pct: float,
    net_premium: float,
    max_loss: float,
    unit_span: float | None,
    dte: int | None,
) -> float:
    """Annualized carry on SPAN when available, else proxy score."""
    if unit_span and unit_span > 0 and dte is not None and dte > 0:
        return annualized_carry_percent_on_span(net_premium, dte, unit_span)
    return score_credit_trade(pop_pct, net_premium, max_loss, span_margin=unit_span)


def _geometric_mean(values: list[float]) -> float:
    positive = [v for v in values if v > 0]
    if not positive:
        return 0.0
    return math.exp(sum(math.log(v) for v in positive) / len(positive))


def leg_spread_score(q: QuoteRow) -> float:
    mid = q.mid_price
    if mid <= 0:
        return 0.0
    return max(0.0, min(1.0, 1.0 - q.spread / mid))


def score_iron_condor_ror(
    pop_pct: float,
    net_premium: float,
    max_loss: float,
    target_pop: float,
    leg_quotes: list[QuoteRow],
) -> tuple[float, dict[str, float]]:
    """Return-on-risk composite score for iron condor ranking."""
    ror = net_premium / max(max_loss, 1.0)
    pop_weight = (pop_pct / max(target_pop, 1.0)) ** 0.5
    liquidity_vals = [q.liquidity_score for q in leg_quotes if q.liquidity_score > 0]
    spread_vals = [leg_spread_score(q) for q in leg_quotes]
    liquidity_weight = _geometric_mean(liquidity_vals) if liquidity_vals else 0.5
    spread_weight = _geometric_mean(spread_vals) if spread_vals else 0.5
    score = ror * pop_weight * liquidity_weight * spread_weight
    factors = {
        "ror": round(ror, 6),
        "pop_weight": round(pop_weight, 6),
        "liquidity_weight": round(liquidity_weight, 6),
        "spread_weight": round(spread_weight, 6),
    }
    return score, factors


def build_ranking_summary(
    winner_credit: float,
    winner_pop: float,
    winner_ror: float,
    runner_credit: float,
    runner_pop: float,
    runner_ror: float,
) -> str:
    """Explain why the higher-ranked condor beat the runner-up."""
    parts: list[str] = []
    credit_delta = winner_credit - runner_credit
    if abs(credit_delta) >= 1.0:
        parts.append(f"Higher credit ({credit_delta:+.0f})")
    pop_delta = winner_pop - runner_pop
    if abs(pop_delta) >= 0.5:
        parts.append(f"PoP {winner_pop:.1f}% vs {runner_pop:.1f}%")
    if abs(winner_ror - runner_ror) >= 0.001:
        parts.append(f"better ROR ({winner_ror:.3f} vs {runner_ror:.3f})")
    if not parts:
        return "Higher composite ROR score."
    return "; ".join(parts) + "."
