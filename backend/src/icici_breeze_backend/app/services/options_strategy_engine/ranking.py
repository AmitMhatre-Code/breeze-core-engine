"""Candidate ranking and capital efficiency (Gemini §7.1)."""
from __future__ import annotations

from icici_breeze_backend.app.services.options_strategy_engine.pop import expected_value_heuristic


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
