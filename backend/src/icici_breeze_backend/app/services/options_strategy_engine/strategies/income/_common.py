"""Shared constraint-first, multi-objective engine for income optimizers."""
from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar

from icici_breeze_backend.app.services.options_strategy_engine.helpers import (
    annualized_carry_percent_on_span,
    days_to_expiry,
    elm_addon,
    legs_to_margin_input,
    parse_float,
    short_lots_in_legs,
)
from icici_breeze_backend.app.services.options_strategy_engine.margin_async_fetch import (
    MarginFetchRequest,
    fetch_margins_concurrent,
)
from icici_breeze_backend.app.services.options_strategy_engine.pop import (
    PopDetail,
    pop_detail_for_legs,
)
from icici_breeze_backend.app.services.options_strategy_engine.sizing import (
    legs_at_lots,
    size_quantity_from_budgets,
    structural_margin_key,
)
from icici_breeze_backend.audit.strategy_evaluation_audit import (
    StrategyAuditCollector,
    candidate_id_for_legs,
    record_income_champion,
    record_income_search_behaviour,
    setup_income_audit,
)
from icici_breeze_backend.app.services.options_strategy_engine.types import (
    EngineContext,
    Right,
    StrategyResult,
    TradeLeg,
)

BADGE_INCOME = "Income Maximiser"
BADGE_CAPITAL = "Capital Efficient"
BADGE_MARGIN = "Margin Saver"

SPAN_SHORTLIST_N = 10
NAKED_ANCHOR_TOP_K = 10
OBJECTIVE_KEYS = ("income", "capital", "margin")

IncomeRejectionStats = StrategyAuditCollector

T = TypeVar("T")


class IncomeCandidate(Protocol):
    legs: list[TradeLeg]
    pop: float
    net_collected: float
    credit: float


@dataclass
class IncomeSearchState:
    initial_pop_band: float
    final_pop_band: float
    expansion_attempts: int = 0
    full_chain_exhausted: bool = False


@dataclass
class SpanScoredCandidate:
    candidate: Any
    unit_span: float
    ann_return: float
    required_margin: float


@dataclass
class ChampionEntry:
    candidate: Any
    badges: list[str]
    ann_return: float
    unit_span: float
    required_margin: float


def pop_band(user_min_pop: float) -> float:
    """Adaptive PoP exploration width; does not override the hard PoP floor."""
    if user_min_pop < 30:
        return 10.0
    if user_min_pop < 60:
        return 5.0
    if user_min_pop < 90:
        return 3.0
    return 2.0


def pop_band_ceiling(user_min_pop: float, band_width: float) -> float:
    return min(100.0, user_min_pop + band_width)


def expand_search_band(initial_band: float, attempt: int, user_min_pop: float) -> float:
    """Widen exploration: 95–97 → 95–98 → … → 95–100."""
    max_band = max(0.0, 100.0 - user_min_pop)
    return min(max_band, initial_band + attempt)


def iter_pop_band_expansions(user_min_pop: float) -> Iterator[tuple[float, float]]:
    """Yield (floor_pop, ceiling_pop) pairs until the full chain is covered."""
    initial = pop_band(user_min_pop)
    attempt = 0
    while True:
        band = expand_search_band(initial, attempt, user_min_pop)
        ceiling = pop_band_ceiling(user_min_pop, band)
        yield user_min_pop, ceiling
        if ceiling >= 100.0:
            break
        attempt += 1


def setup_income_collector(ctx: EngineContext) -> StrategyAuditCollector | None:
    if ctx.audit_collector is None:
        return None
    collector = ctx.audit_collector
    collector.min_pop_pct = ctx.min_pop_pct
    collector.pop_band_width = pop_band(ctx.min_pop_pct)
    setup_income_audit(collector)
    return collector


def pop_for_short_strike(ctx: EngineContext, strike: int, right: Right) -> float:
    q = ctx.cache.get((strike, right))
    if not q or not q.liquid:
        return 0.0
    prem = q.best_bid_price or q.ltp
    legs = [TradeLeg(right, "Sell", strike, ctx.lot_size, prem)]
    return pop_detail_for_legs(ctx, legs).pop_pct


def strikes_for_pop_target(
    ctx: EngineContext,
    strikes: list[int],
    right: Right,
    *,
    floor_pop: float,
    ceiling_pop: float,
    spot_filter: Callable[[int], bool] | None = None,
) -> list[int]:
    """Return liquid strikes whose single-short PoP lies in [floor_pop, ceiling_pop]."""
    selected: list[tuple[float, int]] = []
    for s in strikes:
        if spot_filter is not None and not spot_filter(s):
            continue
        q = ctx.cache.get((s, right))
        if not q or not q.liquid:
            continue
        pop = pop_for_short_strike(ctx, s, right)
        if floor_pop <= pop <= ceiling_pop:
            selected.append((pop, s))
    selected.sort(key=lambda x: -x[0])
    return [s for _, s in selected]


def adaptive_short_strikes(
    ctx: EngineContext,
    strikes: list[int],
    right: Right,
    *,
    spot_filter: Callable[[int], bool] | None = None,
    search_state: IncomeSearchState | None = None,
) -> list[int]:
    """Expand PoP band until strikes found or chain exhausted."""
    initial = pop_band(ctx.min_pop_pct)
    if search_state is not None:
        search_state.initial_pop_band = initial

    seen: set[int] = set()
    out: list[int] = []
    expansion = 0
    full_exhausted = False

    for expansion, (floor_pop, ceiling_pop) in enumerate(iter_pop_band_expansions(ctx.min_pop_pct)):
        batch = strikes_for_pop_target(
            ctx,
            strikes,
            right,
            floor_pop=floor_pop,
            ceiling_pop=ceiling_pop,
            spot_filter=spot_filter,
        )
        for s in batch:
            if s not in seen:
                seen.add(s)
                out.append(s)
        if out:
            if search_state is not None:
                search_state.final_pop_band = expand_search_band(initial, expansion, ctx.min_pop_pct)
                search_state.expansion_attempts = expansion
            break
        if ceiling_pop >= 100.0:
            full_exhausted = True
            break

    if search_state is not None:
        search_state.full_chain_exhausted = full_exhausted
        if not out:
            search_state.final_pop_band = expand_search_band(
                initial, max(0, expansion), ctx.min_pop_pct
            )
            search_state.expansion_attempts = expansion

    return out


def record_feasible(
    stats: StrategyAuditCollector | None,
    *,
    pop_detail: PopDetail,
    credit: float,
    passed_capital: bool = True,
) -> None:
    if stats is None:
        return
    stats.record_stage("passed_pop")
    if passed_capital:
        stats.record_stage("passed_capital")
    stats.record_stage("passed_loss")
    stats.record_stage("feasible")
    stats.record_survivor_metrics(pop_pct=pop_detail.pop_pct, credit=credit)


def passes_capital_gate(
    ctx: EngineContext,
    *,
    strategy_id: str,
    legs: list[TradeLeg],
    unit_max_loss: float,
    margin_estimate: float | None = None,
) -> bool:
    """True when at least one lot fits margin budget (pre-SPAN estimate)."""
    L = ctx.lot_size
    unit_short_lots = short_lots_in_legs(legs, L)
    per_lot_margin = margin_estimate if margin_estimate is not None else max(
        unit_max_loss, sum(l.premium_per_unit for l in legs if l.side == "Sell") * L
    )
    qty = size_quantity_from_budgets(
        strategy_id,
        per_lot_margin,
        unit_max_loss * L,
        margin_rupees=ctx.margin_rupees,
        max_loss_rupees=ctx.max_loss_rupees,
        lot_size=L,
        unit_short_lots=unit_short_lots,
        spot=ctx.spot,
        provision_elm=ctx.provision_elm,
    )
    return qty >= L


def score_ann_return(
    net_collected: float,
    unit_span: float,
    dte: int | None,
) -> float:
    if unit_span > 0 and dte is not None and dte > 0:
        return annualized_carry_percent_on_span(net_collected, dte, unit_span)
    return 0.0


def required_margin(ctx: EngineContext, unit_span: float, legs: list[TradeLeg]) -> float:
    short_lots = short_lots_in_legs(legs, ctx.lot_size)
    elm = elm_addon(ctx.spot, ctx.lot_size, short_lots, ctx.provision_elm)
    return unit_span + elm


async def fetch_unit_spans(
    ctx: EngineContext,
    candidates: list[Any],
    *,
    strategy_id: str,
    phase: str,
    stats: StrategyAuditCollector | None = None,
) -> dict[tuple, float]:
    margin_requests: list[MarginFetchRequest] = []
    for cand in candidates:
        if stats is not None:
            stats.record_stage("margin_refined")
        one_lot_legs = legs_at_lots(cand.legs, ctx.lot_size, lots=1)
        struct_key = structural_margin_key(one_lot_legs)
        if struct_key in ctx.unit_span_by_structure:
            continue
        margin_input = legs_to_margin_input(
            one_lot_legs, ctx.stock_code, ctx.exchange_code, ctx.expiry_display
        )
        margin_requests.append(
            MarginFetchRequest(
                cache_key=struct_key,
                margin_input=margin_input,
                strategy_id=strategy_id,
                phase=phase,
            )
        )

    if not margin_requests:
        return {}

    spans = await fetch_margins_concurrent(
        ctx.processor,
        ctx.user_id,
        ctx.exchange_code,
        margin_requests,
        audit=ctx.audit,
        existing_cache=ctx.unit_span_by_structure,
    )
    ctx.unit_span_by_structure.update(spans)
    return spans


def unit_span_from_cache(ctx: EngineContext, legs: list[TradeLeg]) -> float:
    one_lot_legs = legs_at_lots(legs, ctx.lot_size, lots=1)
    struct_key = structural_margin_key(one_lot_legs)
    cached = ctx.unit_span_by_structure.get(struct_key)
    if cached is not None:
        return cached

    margin_input = legs_to_margin_input(
        one_lot_legs, ctx.stock_code, ctx.exchange_code, ctx.expiry_display
    )
    res = ctx.processor.strategy_builder_margin(
        ctx.user_id,
        ctx.exchange_code,
        margin_input,
        audit=ctx.audit,
        audit_context={"strategy_id": "income", "legs": margin_input, "phase": "sync_span"},
    )
    span = parse_float((res.get("Success") or {}).get("span_margin_required"))
    ctx.unit_span_by_structure[struct_key] = span
    return span


async def span_score_candidates(
    ctx: EngineContext,
    candidates: list[Any],
    *,
    strategy_id: str,
    phase: str,
    stats: StrategyAuditCollector | None = None,
    shortlist_n: int = SPAN_SHORTLIST_N,
) -> list[SpanScoredCandidate]:
    """SPAN shortlist by net credit; score ann return and margin."""
    if stats is not None:
        stats.begin_ranking()

    shortlist = sorted(candidates, key=lambda c: c.net_collected, reverse=True)[:shortlist_n]
    await fetch_unit_spans(ctx, shortlist, strategy_id=strategy_id, phase=phase, stats=stats)

    dte = days_to_expiry(ctx.expiry_display)
    scored: list[SpanScoredCandidate] = []

    for cand in shortlist:
        unit_span = unit_span_from_cache(ctx, cand.legs)
        if unit_span <= 0:
            if stats is not None:
                stats.record("span_failure")
            continue
        ann = score_ann_return(cand.net_collected, unit_span, dte)
        margin = required_margin(ctx, unit_span, cand.legs)
        if stats is not None:
            stats.record_survivor_metrics(
                pop_pct=cand.pop,
                credit=cand.credit,
                ann_return_pct=ann,
                unit_span=unit_span,
            )
        scored.append(
            SpanScoredCandidate(
                candidate=cand,
                unit_span=unit_span,
                ann_return=ann,
                required_margin=margin,
            )
        )

    if stats is not None:
        stats.end_ranking(ctx.audit.telemetry if ctx.audit else None)

    return scored


def select_objective_champions(
    scored: list[SpanScoredCandidate],
    *,
    min_ann_return_pct: float,
) -> dict[str, SpanScoredCandidate | None]:
    """Pick income, capital-efficient, and margin-saver champions from SPAN-scored set."""
    eligible = [s for s in scored if s.ann_return >= min_ann_return_pct]
    if not eligible:
        return {k: None for k in OBJECTIVE_KEYS}

    income = max(eligible, key=lambda s: s.candidate.net_collected)
    capital = max(eligible, key=lambda s: s.ann_return)
    margin = min(eligible, key=lambda s: s.required_margin)

    return {"income": income, "capital": capital, "margin": margin}


def merge_champions_with_badges(
    champions: dict[str, SpanScoredCandidate | None],
) -> list[ChampionEntry]:
    """Collapse identical trades; attach multiple badges when one dominates objectives."""
    badge_map = {
        "income": BADGE_INCOME,
        "capital": BADGE_CAPITAL,
        "margin": BADGE_MARGIN,
    }
    merged: dict[str, ChampionEntry] = {}

    for key, scored in champions.items():
        if scored is None:
            continue
        cid = candidate_id_for_legs(scored.candidate.legs)
        badge = badge_map[key]
        if cid in merged:
            entry = merged[cid]
            if badge not in entry.badges:
                entry.badges.append(badge)
        else:
            merged[cid] = ChampionEntry(
                candidate=scored.candidate,
                badges=[badge],
                ann_return=scored.ann_return,
                unit_span=scored.unit_span,
                required_margin=scored.required_margin,
            )

    order = {BADGE_INCOME: 0, BADGE_CAPITAL: 1, BADGE_MARGIN: 2}
    out = list(merged.values())
    out.sort(
        key=lambda e: (
            min(order.get(b, 99) for b in e.badges),
            -e.candidate.net_collected,
        )
    )
    return out


def build_badge_ranking_summary(badges: list[str]) -> str:
    if len(badges) == 1:
        return f"Best feasible trade for {badges[0]} among all candidates meeting your constraints."
    joined = ", ".join(badges[:-1]) + f" and {badges[-1]}"
    return f"This trade is the feasible-set champion for {joined}."


def record_champion_audit(
    stats: StrategyAuditCollector | None,
    entries: list[ChampionEntry],
    *,
    search_state: IncomeSearchState | None,
) -> None:
    if search_state is not None:
        record_income_search_behaviour(
            stats,
            initial_pop_band=search_state.initial_pop_band,
            final_pop_band=search_state.final_pop_band,
            expansion_attempts=search_state.expansion_attempts,
            full_chain_exhausted=search_state.full_chain_exhausted,
        )
    if stats is None:
        return
    for entry in entries:
        cid = candidate_id_for_legs(entry.candidate.legs)
        stats.record_winner(
            candidate_id=cid,
            legs=entry.candidate.legs,
            metrics={
                "pop_pct": round(entry.candidate.pop, 2),
                "net_credit": round(entry.candidate.credit, 4),
                "net_collected": entry.candidate.net_collected,
                "annualized_return_pct": round(entry.ann_return, 2),
                "unit_span": entry.unit_span,
                "margin": round(entry.required_margin, 2),
                "badges": entry.badges,
            },
            stages_passed=[
                "passed_liquidity",
                "passed_credit",
                "passed_pop",
                "passed_capital",
                "passed_loss",
                "feasible",
                "margin_refined",
                "returned",
            ],
            ranks={"objectives": len(entry.badges)},
        )
        stats.record_stage("returned")
        record_income_champion(
            stats,
            candidate_id=cid,
            badges=entry.badges,
            net_credit=entry.candidate.credit,
            annualized_return_pct=entry.ann_return,
            margin=entry.required_margin,
            pop=entry.candidate.pop,
        )


async def run_income_champion_pipeline(
    ctx: EngineContext,
    candidates: list[Any],
    *,
    strategy_id: str,
    strategy_name: str,
    stats: StrategyAuditCollector | None,
    to_result: Callable[[EngineContext, Any, int, float, list[str], str | None], StrategyResult],
    span_phase: str,
    search_state: IncomeSearchState | None = None,
) -> list[StrategyResult]:
    """Score feasible candidates, pick objective champions, return deduped results."""
    if not candidates:
        return []

    scored = await span_score_candidates(
        ctx,
        candidates,
        strategy_id=strategy_id,
        phase=span_phase,
        stats=stats,
    )
    if not scored:
        return []

    champions = select_objective_champions(
        scored, min_ann_return_pct=ctx.min_ann_return_pct
    )
    if all(v is None for v in champions.values()):
        if stats is not None and scored:
            best = max(scored, key=lambda s: s.ann_return)
            stats.record_near_miss(
                candidate_id=candidate_id_for_legs(best.candidate.legs),
                metrics={
                    "pop_pct": round(best.candidate.pop, 2),
                    "net_collected": best.candidate.net_collected,
                    "annualized_return_pct": round(best.ann_return, 2),
                },
                rejection_reason="below_min_ann_return",
                context=(
                    f"Best annualized return {best.ann_return:.1f}% below minimum "
                    f"{ctx.min_ann_return_pct:.1f}%."
                ),
            )
        return []

    entries = merge_champions_with_badges(champions)
    record_champion_audit(stats, entries, search_state=search_state)

    results: list[StrategyResult] = []
    for rank, entry in enumerate(entries, start=1):
        summary = build_badge_ranking_summary(entry.badges)
        result = to_result(
            ctx,
            entry.candidate,
            rank,
            entry.ann_return,
            entry.badges,
            summary,
        )
        results.append(result)
    return results
