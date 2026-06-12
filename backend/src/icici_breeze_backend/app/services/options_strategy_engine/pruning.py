"""Search-space pruning: delta windows, K/M caps, economic filters."""
from __future__ import annotations

from dataclasses import dataclass

from icici_breeze_backend.app.services.options_strategy_engine.pop import pop_iron_condor_short_pair
from icici_breeze_backend.app.services.options_strategy_engine.types import (
    MAX_CANDIDATES_PER_STRATEGY,
    POP_TOLERANCE_PCT,
    TOP_K_SHORT_STRIKES,
    TOP_M_WING_STRIKES,
    WING_WIDTH_MULTIPLIERS,
    EngineContext,
    QuoteRow,
    Right,
)


@dataclass(frozen=True)
class DeltaWindow:
    lo: float
    hi: float


DELTA_INCOME_SHORT = DeltaWindow(0.13, 0.32)
DELTA_INCOME_HEDGE = DeltaWindow(0.02, 0.10)
DELTA_DIRECTIONAL_LONG = DeltaWindow(0.45, 0.65)
DELTA_DIRECTIONAL_SHORT = DeltaWindow(0.20, 0.35)


def delta_in_window(delta: float | None, window: DeltaWindow) -> bool:
    if delta is None:
        return False
    return window.lo <= abs(delta) <= window.hi


def strike_credit_score(q: QuoteRow) -> float:
    prem = q.best_bid_price or q.ltp
    return prem * 0.6 + q.liquidity_score * 0.4


def strike_debit_score(q: QuoteRow) -> float:
    prem = q.best_offer_price or q.ltp
    return (1.0 / max(prem, 0.01)) * 0.5 + q.liquidity_score * 0.5


def top_k_strikes(
    strikes: list[int],
    cache: dict[tuple[int, Right], QuoteRow],
    right: Right,
    k: int,
    *,
    credit: bool = True,
    delta_window: DeltaWindow | None = None,
) -> list[int]:
    scored: list[tuple[float, int]] = []
    for s in strikes:
        q = cache.get((s, right))
        if not q or not q.liquid:
            continue
        if delta_window and not delta_in_window(q.delta, delta_window):
            continue
        if q.best_bid_price <= 0 or q.best_offer_price <= 0:
            continue
        score = strike_credit_score(q) if credit else strike_debit_score(q)
        scored.append((score, s))
    scored.sort(reverse=True)
    return [s for _, s in scored[:k]]


def top_m_wings(
    short_strike: int,
    wing_strikes: list[int],
    cache: dict[tuple[int, Right], QuoteRow],
    right: Right,
    m: int,
    *,
    wing_is_higher: bool,
    delta_window: DeltaWindow | None = DELTA_INCOME_HEDGE,
) -> list[int]:
    candidates = [s for s in wing_strikes if (s > short_strike if wing_is_higher else s < short_strike)]
    if wing_is_higher:
        candidates.sort()
    else:
        candidates.sort(reverse=True)
    scored: list[tuple[float, int]] = []
    for s in candidates:
        q = cache.get((s, right))
        if not q or not q.liquid:
            continue
        if delta_window and not delta_in_window(q.delta, delta_window):
            continue
        scored.append((q.liquidity_score, s))
    scored.sort(reverse=True)
    return [s for _, s in scored[:m]]


def wing_strikes_from_multipliers(
    short_strike: int,
    step: int,
    liquid_strikes: set[int],
    *,
    wing_is_higher: bool,
) -> list[int]:
    out: list[int] = []
    for mult in WING_WIDTH_MULTIPLIERS:
        w = mult * step
        s = short_strike + w if wing_is_higher else short_strike - w
        if s in liquid_strikes:
            out.append(s)
    return out


def passes_economic_prune(
    *,
    net_credit: float | None = None,
    net_debit: float | None = None,
    max_loss_per_unit: float | None = None,
    max_loss_total: float | None = None,
    pop_pct: float | None = None,
    min_pop_pct: float = 0.0,
    require_pop: bool = False,
    max_loss_budget: float | None = None,
    min_premium: float = 0.01,
) -> bool:
    if net_credit is not None and net_credit < min_premium and net_debit is None:
        return False
    if net_debit is not None and net_debit < min_premium and net_credit is None:
        return False
    if max_loss_per_unit is not None and max_loss_per_unit <= 0:
        return False
    if max_loss_total is not None and max_loss_budget is not None:
        if max_loss_total > max_loss_budget:
            return False
    if require_pop and pop_pct is not None and pop_pct < min_pop_pct:
        return False
    return True


def pop_within_tolerance(estimated_pop: float, target_pop: float, tolerance: float = POP_TOLERANCE_PCT) -> bool:
    return abs(estimated_pop - target_pop) <= tolerance


def iron_condor_short_pairs(ctx: EngineContext) -> list[tuple[int, int]]:
    """Return (short_put, short_call) shortlists for iron condor optimization."""
    short_puts = top_k_strikes(
        [s for s in ctx.liquid_pe_strikes if s < ctx.spot],
        ctx.cache,
        "Put",
        TOP_K_SHORT_STRIKES,
        credit=True,
        delta_window=DELTA_INCOME_SHORT,
    )
    short_calls = top_k_strikes(
        [s for s in ctx.liquid_ce_strikes if s > ctx.spot],
        ctx.cache,
        "Call",
        TOP_K_SHORT_STRIKES,
        credit=True,
        delta_window=DELTA_INCOME_SHORT,
    )
    if not short_puts:
        short_puts = [s for s in ctx.liquid_pe_strikes if s < ctx.spot]
    if not short_calls:
        short_calls = [s for s in ctx.liquid_ce_strikes if s > ctx.spot]

    out: list[tuple[int, int]] = []
    for sp in short_puts:
        qp = ctx.cache.get((sp, "Put"))
        for sc in short_calls:
            if sp >= sc:
                continue
            qc = ctx.cache.get((sc, "Call"))
            est_pop = pop_iron_condor_short_pair(
                qp.delta if qp else None,
                qc.delta if qc else None,
            )
            if ctx.strategy_category == "income" and est_pop < ctx.min_pop_pct - POP_TOLERANCE_PCT:
                continue
            out.append((sp, sc))
    return out


def cap_candidates(candidates: list, max_n: int = MAX_CANDIDATES_PER_STRATEGY) -> list:
    return candidates[:max_n]
