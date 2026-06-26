"""Short strangle — constraint-first multi-objective income optimizer."""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

from icici_breeze_backend.app.services.options_strategy_engine.helpers import (
    meets_pop_floor,
    skip,
)
from icici_breeze_backend.app.services.options_strategy_engine.pop import (
    PopDetail,
    pop_detail_for_legs,
)
from icici_breeze_backend.app.services.options_strategy_engine.sizing import min_qty_for_one_lot
from icici_breeze_backend.audit.strategy_evaluation_audit import StrategyAuditCollector
from icici_breeze_backend.app.services.options_strategy_engine.audit_helpers import audit_calc
from icici_breeze_backend.app.services.options_strategy_engine.strategies.common import make_result
from icici_breeze_backend.app.services.options_strategy_engine.strategies.income._common import (
    IncomeSearchState,
    NAKED_ANCHOR_TOP_K,
    SPAN_SHORTLIST_N,
    adaptive_short_strikes,
    iter_pop_band_expansions,
    passes_capital_gate,
    pop_band,
    pop_for_short_strike,
    record_feasible,
    run_income_champion_pipeline,
    score_ann_return,
    setup_income_collector,
    span_score_candidates,
    unit_span_from_cache,
)
from icici_breeze_backend.app.services.options_strategy_engine.types import (
    EngineContext,
    QuoteRow,
    Right,
    StrategyResult,
    TradeLeg,
)

SS_SPAN_SHORTLIST_N = SPAN_SHORTLIST_N

ShortStrangleRejectionStats = StrategyAuditCollector


def score_short_strangle_candidate(
    pop_pct: float,
    net_premium: float,
    unit_span: float | None,
    dte: int | None,
) -> float:
    del pop_pct
    return score_ann_return(net_premium, unit_span or 0.0, dte)


def _ss_pop_bucket(pop_pct: float, floor_pct: float) -> str:
    from icici_breeze_backend.audit.strategy_evaluation_audit import pop_bucket_label

    return pop_bucket_label(pop_pct, floor_pct, band_width=pop_band(floor_pct))


def _unit_span_margin(ctx: EngineContext, legs: list, *, strategy_id: str) -> float:
    del strategy_id
    return unit_span_from_cache(ctx, legs)


def _geometric_mean(values: list[float]) -> float:
    positive = [v for v in values if v > 0]
    if not positive:
        return 0.0
    return math.exp(sum(math.log(v) for v in positive) / len(positive))


def _leg_spread_score(q: QuoteRow) -> float:
    mid = q.mid_price
    if mid <= 0:
        return 0.0
    return max(0.0, min(1.0, 1.0 - q.spread / mid))


def score_short_strangle_ror(
    net_collected: float,
    leg_quotes: list[QuoteRow],
) -> tuple[float, dict[str, float]]:
    liquidity_vals = [q.liquidity_score for q in leg_quotes if q.liquidity_score > 0]
    spread_vals = [_leg_spread_score(q) for q in leg_quotes]
    liquidity_weight = _geometric_mean(liquidity_vals) if liquidity_vals else 0.5
    spread_weight = _geometric_mean(spread_vals) if spread_vals else 0.5
    score = net_collected * liquidity_weight * spread_weight
    return score, {
        "liquidity_weight": round(liquidity_weight, 6),
        "spread_weight": round(spread_weight, 6),
    }


@dataclass(frozen=True)
class ShortStrangleCandidate:
    short_put: int
    short_call: int
    credit: float
    qty: int
    pop: float
    legs: list[TradeLeg]
    net_collected: float
    final_score: float = 0.0
    score_factors: dict[str, float] = field(default_factory=dict)


async def _pick_top_candidates(ctx, candidates, **kwargs):
    span_shortlist_n = kwargs.get("span_shortlist_n", SS_SPAN_SHORTLIST_N)
    return_top_n = kwargs.get("return_top_n", 3)
    scored = await span_score_candidates(
        ctx,
        candidates,
        strategy_id="short_strangle",
        phase="ss_candidate_span",
        shortlist_n=span_shortlist_n,
    )
    return [(s.candidate, s.ann_return) for s in scored[:return_top_n]], [
        {"annualized_return_pct": s.ann_return, "unit_span": s.unit_span} for s in scored
    ]


def _collect_candidates(ctx, pairs, *, stats=None):
    candidates: list[ShortStrangleCandidate] = []
    for sp, sc in pairs:
        candidates.extend(enumerate_short_strangles(ctx, sp, sc, stats=stats))
    return candidates


def _naked_anchor_strikes(
    ctx: EngineContext,
    strikes: list[int],
    right: Right,
    *,
    spot_filter: Callable[[int], bool],
) -> list[int]:
    """Single-leg shorts that individually satisfy the PoP floor."""
    anchors: list[tuple[float, int]] = []
    for s in strikes:
        if not spot_filter(s):
            continue
        pop = pop_for_short_strike(ctx, s, right)
        if pop >= ctx.min_pop_pct:
            anchors.append((pop, s))
    anchors.sort(key=lambda x: -x[0])
    return [s for _, s in anchors]


def _short_lists_for_band(
    ctx: EngineContext,
    *,
    ceiling_pop: float,
) -> tuple[list[int], list[int]]:
    pe_strikes = [s for s in ctx.liquid_pe_strikes if s < ctx.spot]
    ce_strikes = [s for s in ctx.liquid_ce_strikes if s > ctx.spot]
    floor = ctx.min_pop_pct
    puts = adaptive_short_strikes(
        ctx,
        pe_strikes,
        "Put",
        spot_filter=lambda s: s < ctx.spot,
    )
    calls = adaptive_short_strikes(
        ctx,
        ce_strikes,
        "Call",
        spot_filter=lambda s: s > ctx.spot,
    )
    if not puts:
        puts = [
            s
            for s in pe_strikes
            if floor <= pop_for_short_strike(ctx, s, "Put") <= ceiling_pop
        ]
    if not calls:
        calls = [
            s
            for s in ce_strikes
            if floor <= pop_for_short_strike(ctx, s, "Call") <= ceiling_pop
        ]
    return puts, calls


def short_strangle_pairs(
    ctx: EngineContext,
    *,
    search_state: IncomeSearchState | None = None,
) -> list[tuple[int, int]]:
    """PoP-aware put/call pairs with naked-anchor forced cross-products."""
    pe_pool = [s for s in ctx.liquid_pe_strikes if s < ctx.spot]
    ce_pool = [s for s in ctx.liquid_ce_strikes if s > ctx.spot]
    anchor_puts = _naked_anchor_strikes(
        ctx, pe_pool, "Put", spot_filter=lambda s: s < ctx.spot
    )
    anchor_calls = _naked_anchor_strikes(
        ctx, ce_pool, "Call", spot_filter=lambda s: s > ctx.spot
    )

    initial = pop_band(ctx.min_pop_pct)
    if search_state is not None:
        search_state.initial_pop_band = initial

    pair_set: set[tuple[int, int]] = set()
    for expansion, (floor_pop, ceiling_pop) in enumerate(iter_pop_band_expansions(ctx.min_pop_pct)):
        del floor_pop
        puts, calls = _short_lists_for_band(ctx, ceiling_pop=ceiling_pop)
        for sp in puts:
            for sc in calls:
                if sp < sc:
                    pair_set.add((sp, sc))
        for anchor_ce in anchor_calls[:NAKED_ANCHOR_TOP_K]:
            for sp in puts[:NAKED_ANCHOR_TOP_K]:
                if sp < anchor_ce:
                    pair_set.add((sp, anchor_ce))
        for anchor_pe in anchor_puts[:NAKED_ANCHOR_TOP_K]:
            for sc in calls[:NAKED_ANCHOR_TOP_K]:
                if anchor_pe < sc:
                    pair_set.add((anchor_pe, sc))
        if pair_set:
            if search_state is not None:
                search_state.final_pop_band = ceiling_pop - ctx.min_pop_pct
                search_state.expansion_attempts = expansion
                search_state.full_chain_exhausted = ceiling_pop >= 100.0
            break
        if ceiling_pop >= 100.0:
            if search_state is not None:
                search_state.full_chain_exhausted = True
                search_state.final_pop_band = ceiling_pop - ctx.min_pop_pct
                search_state.expansion_attempts = expansion
            break

    return sorted(pair_set)


def enumerate_short_strangles(
    ctx: EngineContext,
    short_put: int,
    short_call: int,
    *,
    stats: ShortStrangleRejectionStats | None = None,
) -> list[ShortStrangleCandidate]:
    L = ctx.lot_size
    sid = "short_strangle"
    out: list[ShortStrangleCandidate] = []
    if stats is not None:
        stats.min_pop_pct = ctx.min_pop_pct

    def _reject(
        reason: str,
        *,
        pop_detail: PopDetail | None = None,
        credit: float | None = None,
        **detail: object,
    ) -> None:
        if stats is not None:
            stats.record(reason, short_put=short_put, short_call=short_call, **detail)
            stats.record_evaluation(
                short_put=short_put,
                short_call=short_call,
                outcome="rejected",
                reject_reason=reason,
                pop_detail=pop_detail,
                credit=credit,
            )

    if stats is not None:
        stats.record_generated()
    pe = ctx.cache.get((short_put, "Put"))
    ce = ctx.cache.get((short_call, "Call"))
    if not pe or not ce:
        _reject("missing_quote")
        return out
    if not pe.liquid or not ce.liquid:
        _reject("illiquid")
        return out
    if stats is not None:
        stats.record_stage("passed_liquidity")

    prem_p = pe.best_bid_price or pe.ltp
    prem_c = ce.best_bid_price or ce.ltp
    credit = prem_p + prem_c
    if credit <= 0:
        _reject("no_credit", credit=credit)
        return out
    if stats is not None:
        stats.record_stage("passed_credit")
        stats.record_stage("passed_loss")

    qty = min_qty_for_one_lot(L)
    if qty < L:
        _reject("quantity")
        return out

    legs = [
        TradeLeg("Put", "Sell", short_put, qty, prem_p),
        TradeLeg("Call", "Sell", short_call, qty, prem_c),
    ]
    pop_detail = pop_detail_for_legs(ctx, legs)
    pop = pop_detail.pop_pct
    if not meets_pop_floor(ctx, pop):
        _reject("pop_floor", pop_detail=pop_detail, credit=credit, floor=ctx.min_pop_pct)
        return out

    margin_est = credit * L * 5
    if not passes_capital_gate(
        ctx,
        strategy_id=sid,
        legs=legs,
        unit_max_loss=0.0,
        margin_estimate=margin_est,
    ):
        _reject("capital")
        return out

    if stats is not None:
        stats.record_evaluation(
            short_put=short_put,
            short_call=short_call,
            outcome="accepted",
            reject_reason=None,
            pop_detail=pop_detail,
            credit=credit,
        )
    record_feasible(stats, pop_detail=pop_detail, credit=credit, passed_capital=True)

    net_collected = credit * qty
    final_score, score_factors = score_short_strangle_ror(net_collected, [pe, ce])
    out.append(
        ShortStrangleCandidate(
            short_put=short_put,
            short_call=short_call,
            credit=credit,
            qty=qty,
            pop=pop,
            legs=legs,
            net_collected=net_collected,
            final_score=final_score,
            score_factors=score_factors,
        )
    )
    return out


def _candidate_to_result(
    ctx: EngineContext,
    cand: ShortStrangleCandidate,
    rank: int,
    ann_return: float,
    badges: list[str],
    ranking_summary: str | None,
) -> StrategyResult:
    sid = "short_strangle"
    name = "Short Strangle" if rank == 1 else f"Short Strangle #{rank}"
    result = make_result(
        ctx,
        sid,
        name,
        cand.legs,
        max_loss=None,
        rr=f"Unlimited : {cand.net_collected:.0f}",
        pop=cand.pop,
        net_premium_val=cand.net_collected,
        variant_rank=rank,
        engine_score=round(cand.final_score, 6),
        ranking_summary=ranking_summary,
        score_breakdown=cand.score_factors,
        badges=badges,
    )
    result.annualized_return_pct = round(ann_return, 2)
    return result


async def calc_short_strangle(ctx: EngineContext) -> list[StrategyResult]:
    sid, name = "short_strangle", "Short Strangle"
    if ctx.halted:
        return [skip(sid, name, ctx.halt_reason or "Market halted")]

    stats = setup_income_collector(ctx)
    search_state = IncomeSearchState(initial_pop_band=pop_band(ctx.min_pop_pct), final_pop_band=0.0)
    pairs = short_strangle_pairs(ctx, search_state=search_state)
    candidates = _collect_candidates(ctx, pairs, stats=stats)
    if stats is not None:
        stats.end_generation(ctx.audit.telemetry if ctx.audit else None)

    if not candidates:
        skip_reason = stats.skip_message() if stats else (
            "No short strangle meets minimum PoP on the liquid chain."
        )
        return [skip(sid, name, skip_reason)]

    results = await run_income_champion_pipeline(
        ctx,
        candidates,
        strategy_id=sid,
        strategy_name=name,
        stats=stats,
        to_result=_candidate_to_result,
        span_phase="ss_candidate_span",
        search_state=search_state,
    )

    if not results:
        return [
            skip(
                sid,
                name,
                f"No short strangle meets minimum annualized return "
                f"{ctx.min_ann_return_pct:.1f}%.",
            )
        ]

    return results


def prefetch_short_strangle(ctx: EngineContext) -> set[tuple[int, Right]]:
    from icici_breeze_backend.app.services.options_strategy_engine.strategies.common import (
        prefetch_atm_pairs,
    )

    pairs = prefetch_atm_pairs(ctx)
    search_state = IncomeSearchState(initial_pop_band=pop_band(ctx.min_pop_pct), final_pop_band=0.0)
    for sp, sc in short_strangle_pairs(ctx, search_state=search_state):
        pairs.add((sp, "Put"))
        pairs.add((sc, "Call"))
    return pairs
