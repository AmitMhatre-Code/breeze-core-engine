"""Bull put spread — constraint-first multi-objective income optimizer."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from icici_breeze_backend.app.services.options_strategy_engine.helpers import (
    meets_pop_floor,
    skip,
)
from icici_breeze_backend.app.services.options_strategy_engine.pop import (
    PopDetail,
    pop_detail_for_legs,
)
from icici_breeze_backend.app.services.options_strategy_engine.pruning import (
    passes_economic_prune,
    wing_strikes_from_multipliers,
)
from icici_breeze_backend.app.services.options_strategy_engine.sizing import min_qty_for_one_lot
from icici_breeze_backend.audit.strategy_evaluation_audit import (
    StrategyAuditCollector,
    pop_bucket_label,
)
from icici_breeze_backend.app.services.options_strategy_engine.audit_helpers import audit_calc
from icici_breeze_backend.app.services.options_strategy_engine.strategies.common import make_result
from icici_breeze_backend.app.services.options_strategy_engine.strategies.income._common import (
    IncomeRejectionStats,
    IncomeSearchState,
    SPAN_SHORTLIST_N,
    adaptive_short_strikes,
    iter_pop_band_expansions,
    passes_capital_gate,
    pop_band,
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

WING_WIDTH_MULTIPLIERS: tuple[int, ...] = (1, 2, 3, 4)
BPS_SPAN_SHORTLIST_N = SPAN_SHORTLIST_N

BullPutSpreadRejectionStats = StrategyAuditCollector


def score_bull_put_spread_candidate(
    pop_pct: float,
    net_premium: float,
    unit_span: float | None,
    dte: int | None,
) -> float:
    del pop_pct
    return score_ann_return(net_premium, unit_span or 0.0, dte)


def _bps_pop_bucket(pop_pct: float, floor_pct: float) -> str:
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


def score_bull_put_spread_ror(
    net_collected: float,
    max_loss: float,
    leg_quotes: list[QuoteRow],
) -> tuple[float, dict[str, float]]:
    """Credit proxy: ROR × liquidity × spread (PoP excluded from ranking)."""
    ror = net_collected / max(max_loss, 1.0)
    liquidity_vals = [q.liquidity_score for q in leg_quotes if q.liquidity_score > 0]
    spread_vals = [_leg_spread_score(q) for q in leg_quotes]
    liquidity_weight = _geometric_mean(liquidity_vals) if liquidity_vals else 0.5
    spread_weight = _geometric_mean(spread_vals) if spread_vals else 0.5
    score = ror * liquidity_weight * spread_weight
    factors = {
        "ror": round(ror, 6),
        "liquidity_weight": round(liquidity_weight, 6),
        "spread_weight": round(spread_weight, 6),
    }
    return score, factors


@dataclass(frozen=True)
class BullPutSpreadCandidate:
    short_strike: int
    long_strike: int
    wing_width: int
    credit: float
    max_loss_u: float
    qty: int
    pop: float
    legs: list[TradeLeg]
    net_collected: float
    final_score: float = 0.0
    score_factors: dict[str, float] = field(default_factory=dict)


async def _pick_top_candidates(ctx, candidates, **kwargs):
    """Backward-compatible test hook delegating to shared SPAN scoring."""
    span_shortlist_n = kwargs.get("span_shortlist_n", BPS_SPAN_SHORTLIST_N)
    return_top_n = kwargs.get("return_top_n", 3)
    scored = await span_score_candidates(
        ctx,
        candidates,
        strategy_id="bull_put_spread",
        phase="bps_candidate_span",
        shortlist_n=span_shortlist_n,
    )
    winners = [(s.candidate, s.ann_return) for s in scored[:return_top_n]]
    span_scores = [
        {"annualized_return_pct": s.ann_return, "unit_span": s.unit_span} for s in scored
    ]
    return winners, span_scores


def _collect_candidates(ctx, short_strikes, *, stats=None):
    candidates: list[BullPutSpreadCandidate] = []
    for short_strike in short_strikes:
        candidates.extend(enumerate_bull_put_spreads(ctx, short_strike, stats=stats))
    return candidates


def bull_put_spread_short_strikes(
    ctx: EngineContext,
    *,
    search_state: IncomeSearchState | None = None,
) -> list[int]:
    pe_strikes = [s for s in ctx.liquid_pe_strikes if s <= ctx.atm_strike]
    return adaptive_short_strikes(
        ctx,
        pe_strikes,
        "Put",
        spot_filter=lambda s: s <= ctx.atm_strike,
        search_state=search_state,
    )


def _wing_strikes_for_short(
    ctx: EngineContext,
    short_stp: int,
    wing_strikes: list[int],
) -> list[tuple[int, int]]:
    liquid_set = set(wing_strikes)
    wings = wing_strikes_from_multipliers(
        short_stp, ctx.strike_step, liquid_set, wing_is_higher=False
    )
    if not wings:
        wings = [s for s in wing_strikes if s < short_stp]
    out: list[tuple[int, int]] = []
    seen: set[int] = set()
    for wing in wings:
        if wing in seen:
            continue
        seen.add(wing)
        out.append((wing, abs(wing - short_stp)))
    return out


def enumerate_bull_put_spreads(
    ctx: EngineContext,
    short_strike: int,
    *,
    stats: BullPutSpreadRejectionStats | None = None,
) -> list[BullPutSpreadCandidate]:
    """All feasible bull put spreads for a short strike."""
    L = ctx.lot_size
    sid = "bull_put_spread"
    out: list[BullPutSpreadCandidate] = []
    if stats is not None:
        stats.min_pop_pct = ctx.min_pop_pct

    def _reject(
        reason: str,
        *,
        long_strike: int | None = None,
        wing_width: int | None = None,
        pop_detail: PopDetail | None = None,
        credit: float | None = None,
        **detail: object,
    ) -> None:
        if stats is not None:
            stats.record(
                reason,
                short_strike=short_strike,
                long_strike=long_strike,
                wing_width=wing_width,
                **detail,
            )
            stats.record_evaluation(
                short_strike=short_strike,
                long_strike=long_strike,
                wing_width=wing_width,
                outcome="rejected",
                reject_reason=reason,
                pop_detail=pop_detail,
                credit=credit,
            )

    qs = ctx.cache.get((short_strike, "Put"))
    if not qs:
        if stats is not None:
            stats.record_generated()
        _reject("missing_quote")
        return out
    if not qs.liquid:
        if stats is not None:
            stats.record_generated()
        _reject("illiquid")
        return out

    short_prem = qs.best_bid_price or qs.ltp
    wing_pairs = _wing_strikes_for_short(ctx, short_strike, ctx.liquid_pe_strikes)

    for long_strike, wing_width in wing_pairs:
        if stats is not None:
            stats.record_generated()
        qw = ctx.cache.get((long_strike, "Put"))
        if not qw:
            _reject("missing_quote", long_strike=long_strike, wing_width=wing_width)
            continue
        if not qw.liquid:
            _reject("illiquid", long_strike=long_strike, wing_width=wing_width)
            continue
        if stats is not None:
            stats.record_stage("passed_liquidity")

        wing_prem = qw.best_offer_price or qw.ltp
        credit = short_prem - wing_prem
        if credit <= 0:
            _reject(
                "no_credit",
                long_strike=long_strike,
                wing_width=wing_width,
                credit=credit,
            )
            continue
        if stats is not None:
            stats.record_stage("passed_credit")

        max_loss_u = wing_width - credit
        if not passes_economic_prune(
            net_credit=credit,
            max_loss_per_unit=max_loss_u,
            max_loss_total=max_loss_u * L,
            max_loss_budget=ctx.max_loss_rupees,
            require_pop=False,
        ):
            _reject(
                "max_loss_budget",
                long_strike=long_strike,
                wing_width=wing_width,
                credit=credit,
            )
            continue
        if stats is not None:
            stats.record_stage("passed_economic_prune")
            stats.record_stage("passed_loss")

        qty = min_qty_for_one_lot(L)
        if qty < L:
            _reject("quantity", long_strike=long_strike, wing_width=wing_width)
            continue

        legs = [
            TradeLeg("Put", "Sell", short_strike, qty, short_prem),
            TradeLeg("Put", "Buy", long_strike, qty, wing_prem),
        ]
        pop_detail = pop_detail_for_legs(ctx, legs)
        pop = pop_detail.pop_pct
        if not meets_pop_floor(ctx, pop):
            _reject(
                "pop_floor",
                long_strike=long_strike,
                wing_width=wing_width,
                pop_detail=pop_detail,
                credit=credit,
                floor=ctx.min_pop_pct,
            )
            continue

        capital_ok = passes_capital_gate(
            ctx,
            strategy_id=sid,
            legs=legs,
            unit_max_loss=max_loss_u,
            margin_estimate=max_loss_u * L,
        )
        if not capital_ok:
            _reject("capital", long_strike=long_strike, wing_width=wing_width)
            continue

        if stats is not None:
            stats.record_evaluation(
                short_strike=short_strike,
                long_strike=long_strike,
                wing_width=wing_width,
                outcome="accepted",
                reject_reason=None,
                pop_detail=pop_detail,
                credit=credit,
            )
        record_feasible(stats, pop_detail=pop_detail, credit=credit, passed_capital=True)

        net_collected = credit * qty
        max_loss = max_loss_u * qty
        leg_quotes: list[QuoteRow] = [qs, qw]
        final_score, score_factors = score_bull_put_spread_ror(
            net_collected, max_loss, leg_quotes
        )
        out.append(
            BullPutSpreadCandidate(
                short_strike=short_strike,
                long_strike=long_strike,
                wing_width=wing_width,
                credit=credit,
                max_loss_u=max_loss_u,
                qty=qty,
                pop=pop,
                legs=legs,
                net_collected=net_collected,
                final_score=final_score,
                score_factors=score_factors,
            )
        )
    return out


def _collect_with_adaptive_search(
    ctx: EngineContext,
    *,
    stats: BullPutSpreadRejectionStats | None = None,
) -> tuple[list[BullPutSpreadCandidate], IncomeSearchState]:
    search_state = IncomeSearchState(initial_pop_band=pop_band(ctx.min_pop_pct), final_pop_band=0.0)
    candidates: list[BullPutSpreadCandidate] = []
    initial = pop_band(ctx.min_pop_pct)
    search_state.initial_pop_band = initial

    for expansion, (floor_pop, ceiling_pop) in enumerate(iter_pop_band_expansions(ctx.min_pop_pct)):
        del floor_pop
        if stats is not None:
            stats.pop_band_width = ceiling_pop - ctx.min_pop_pct
        pe_strikes = [s for s in ctx.liquid_pe_strikes if s <= ctx.atm_strike]
        short_strikes = adaptive_short_strikes(
            ctx,
            pe_strikes,
            "Put",
            spot_filter=lambda s: s <= ctx.atm_strike,
        )
        candidates = []
        for short_strike in short_strikes:
            candidates.extend(enumerate_bull_put_spreads(ctx, short_strike, stats=stats))
        if candidates:
            search_state.final_pop_band = ceiling_pop - ctx.min_pop_pct
            search_state.expansion_attempts = expansion
            search_state.full_chain_exhausted = ceiling_pop >= 100.0
            break
        if ceiling_pop >= 100.0:
            search_state.full_chain_exhausted = True
            search_state.final_pop_band = ceiling_pop - ctx.min_pop_pct
            search_state.expansion_attempts = expansion
            break

    return candidates, search_state


def _candidate_to_result(
    ctx: EngineContext,
    cand: BullPutSpreadCandidate,
    rank: int,
    ann_return: float,
    badges: list[str],
    ranking_summary: str | None,
) -> StrategyResult:
    sid = "bull_put_spread"
    name = "Bull Put Spread" if rank == 1 else f"Bull Put Spread #{rank}"
    max_loss = cand.max_loss_u * cand.qty
    result = make_result(
        ctx,
        sid,
        name,
        cand.legs,
        max_loss=max_loss,
        rr=f"{max_loss:.0f} : {cand.net_collected:.0f}",
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


async def calc_bull_put_spread(ctx: EngineContext) -> list[StrategyResult]:
    sid, name = "bull_put_spread", "Bull Put Spread"
    if ctx.halted:
        return [skip(sid, name, ctx.halt_reason or "Market halted")]

    stats = setup_income_collector(ctx)
    candidates, search_state = _collect_with_adaptive_search(ctx, stats=stats)
    if stats is not None:
        stats.end_generation(ctx.audit.telemetry if ctx.audit else None)

    if not candidates:
        skip_reason = stats.skip_message() if stats else (
            "No bull put spread meets minimum PoP within risk limits."
        )
        if ctx.audit:
            audit_calc(
                ctx,
                "Bull put spread candidate search",
                {"survivors": 0, "pop_band_target": [ctx.min_pop_pct, ctx.min_pop_pct + pop_band(ctx.min_pop_pct)]},
                {"rejection_counts": stats.counts if stats else {}},
                rationale="No bull put spread passed feasibility filters.",
                strategy_id=sid,
            )
        return [skip(sid, name, skip_reason)]

    results = await run_income_champion_pipeline(
        ctx,
        candidates,
        strategy_id=sid,
        strategy_name=name,
        stats=stats,
        to_result=_candidate_to_result,
        span_phase="bps_candidate_span",
        search_state=search_state,
    )

    if not results:
        return [
            skip(
                sid,
                name,
                f"No bull put spread meets minimum annualized return "
                f"{ctx.min_ann_return_pct:.1f}%.",
            )
        ]

    if ctx.audit:
        audit_calc(
            ctx,
            "Bull put spread objective champions",
            {"feasible": len(candidates), "returned": len(results)},
            {"search_state": search_state.__dict__},
            rationale="Constraint-first multi-objective champions from feasible set.",
            strategy_id=sid,
        )

    return results


def prefetch_bull_put_spread(ctx: EngineContext) -> set[tuple[int, Right]]:
    from icici_breeze_backend.app.services.options_strategy_engine.strategies.common import (
        prefetch_atm_pairs,
    )

    pairs = prefetch_atm_pairs(ctx)
    for s in bull_put_spread_short_strikes(ctx):
        pairs.add((s, "Put"))
        for mult in WING_WIDTH_MULTIPLIERS:
            pairs.add((s - mult * ctx.strike_step, "Put"))
    return pairs
