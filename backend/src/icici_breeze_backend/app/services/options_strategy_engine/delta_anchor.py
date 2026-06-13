"""Delta-anchored strike selection (Template Delta-Anchoring model)."""
from __future__ import annotations

from typing import Callable, Literal

from icici_breeze_backend.app.services.options_strategy_engine.types import QuoteRow, Right

RiskRewardProfile = Literal["conservative", "moderate", "aggressive"]
ConvictionStrategyKind = Literal["long_option", "spread"]
CONVICTION_PROFILES: tuple[RiskRewardProfile, ...] = ("conservative", "moderate", "aggressive")
DELTA_TOLERANCE = 0.05
DELTA_CANDIDATE_WINDOW = 0.08
DELTA_TOLERANCE_SEQUENCE: tuple[float, ...] = (0.05, 0.08, 0.10, 0.15)
MAX_CANDIDATES_PER_CONVICTION = 12
MIN_LIQUIDITY_SCORE = 0.05

_LONG_OPTION_DELTAS: dict[str, tuple[float, float]] = {
    "conservative": (0.60, 0.0),
    "moderate": (0.50, 0.0),
    "aggressive": (0.40, 0.0),
}
_SPREAD_PROFILE_DELTAS: dict[str, tuple[float, float]] = {
    "conservative": (0.40, 0.20),
    "moderate": (0.50, 0.25),
    "aggressive": (0.60, 0.30),
}


def conviction_delta_templates() -> dict[str, dict[str, tuple[float, float]]]:
    """Return conviction profile → (long_Δ, short_Δ) templates by strategy kind."""
    return {
        "long_option": dict(_LONG_OPTION_DELTAS),
        "spread": dict(_SPREAD_PROFILE_DELTAS),
    }


def pop_to_short_delta(min_pop_pct: float, short_legs: int = 1) -> float:
    """85% PoP with one short wing → ~0.15Δ; symmetric strangle uses two wings."""
    return (100.0 - min_pop_pct) / 100.0 / max(1, short_legs)


def profile_deltas(
    profile: str,
    *,
    kind: ConvictionStrategyKind = "spread",
) -> tuple[float, float]:
    templates = _LONG_OPTION_DELTAS if kind == "long_option" else _SPREAD_PROFILE_DELTAS
    return templates.get(profile, templates["moderate"])


def abs_delta(q: QuoteRow | None) -> float | None:
    if q is None or q.delta is None:
        return None
    return abs(q.delta)


def strikes_near_delta(
    strikes: list[int],
    cache: dict[tuple[int, Right], QuoteRow],
    right: Right,
    target: float,
    *,
    tolerance: float = DELTA_TOLERANCE,
) -> list[int]:
    out: list[int] = []
    for s in strikes:
        q = cache.get((s, right))
        d = abs_delta(q)
        if q and q.liquid and d is not None and abs(d - target) <= tolerance:
            out.append(s)
    return out


def best_strike_near_delta(
    strikes: list[int],
    cache: dict[tuple[int, Right], QuoteRow],
    right: Right,
    target: float,
    *,
    strike_filter: Callable[[int], bool] | None = None,
) -> int | None:
    best: tuple[float, int] | None = None
    for s in strikes:
        if strike_filter is not None and not strike_filter(s):
            continue
        q = cache.get((s, right))
        if not q or not q.liquid:
            continue
        d = abs_delta(q)
        if d is None:
            continue
        dist = abs(d - target)
        if best is None or dist < best[0]:
            best = (dist, s)
    return best[1] if best else None


def strikes_ranked_by_delta(
    strikes: list[int],
    cache: dict[tuple[int, Right], QuoteRow],
    right: Right,
    target: float,
    *,
    strike_filter: Callable[[int], bool] | None = None,
) -> list[int]:
    scored: list[tuple[float, int]] = []
    for s in strikes:
        if strike_filter is not None and not strike_filter(s):
            continue
        q = cache.get((s, right))
        if not q or not q.liquid:
            continue
        d = abs_delta(q)
        if d is None:
            continue
        scored.append((abs(d - target), s))
    scored.sort(key=lambda x: x[0])
    return [s for _, s in scored]
