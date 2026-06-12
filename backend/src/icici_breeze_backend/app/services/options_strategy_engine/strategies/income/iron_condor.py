"""Iron condor strategy calculator."""
from __future__ import annotations

from icici_breeze_backend.app.services.options_strategy_engine.delta_anchor import best_strike_near_delta
from icici_breeze_backend.app.services.options_strategy_engine.helpers import (
    days_to_expiry,
    legs_to_margin_input,
    parse_float,
    skip,
)
from icici_breeze_backend.app.services.options_strategy_engine.pruning import iron_condor_short_pairs
from icici_breeze_backend.app.services.options_strategy_engine.ranking import (
    build_ranking_summary,
    score_iron_condor_candidate,
)
from icici_breeze_backend.app.services.options_strategy_engine.sizing import legs_at_lots
from icici_breeze_backend.app.services.options_strategy_engine.strategies.base import (
    IronCondorCandidate,
    all_liquid,
    enumerate_symmetric_iron_condors,
    make_result,
)
from icici_breeze_backend.app.services.options_strategy_engine.strategies.income._common import short_delta
from icici_breeze_backend.app.services.options_strategy_engine.types import (
    IC_RETURN_TOP_N,
    IC_SPAN_REFINE_TOP_N,
    MIN_IC_ANNUALIZED_RETURN_PCT,
    EngineContext,
    StrategyResult,
)


def _unit_span_margin(
    ctx: EngineContext,
    legs: list,
    *,
    strategy_id: str,
) -> float:
    one_lot_legs = legs_at_lots(legs, ctx.lot_size, lots=1)
    margin_input = legs_to_margin_input(
        one_lot_legs, ctx.stock_code, ctx.exchange_code, ctx.expiry_display
    )
    res = ctx.processor.strategy_builder_margin(
        ctx.user_id,
        ctx.exchange_code,
        margin_input,
        audit=ctx.audit,
        audit_context={
            "strategy_id": strategy_id,
            "legs": margin_input,
            "phase": "ic_candidate_span",
        },
    )
    return parse_float((res.get("Success") or {}).get("span_margin_required"))


def _collect_candidates(
    ctx: EngineContext, pairs: list[tuple[int, int]] | None = None
) -> list[IronCondorCandidate]:
    if pairs is None:
        pairs = iron_condor_short_pairs(ctx)
    candidates: list[IronCondorCandidate] = []
    for sp, sc in pairs:
        candidates.extend(enumerate_symmetric_iron_condors(ctx, sp, sc))

    if candidates:
        return candidates

    target = short_delta(ctx, 2)
    stp_sp = best_strike_near_delta(
        all_liquid(ctx, "Put"), ctx.cache, "Put", target, strike_filter=lambda s: s < ctx.spot
    )
    stp_sc = best_strike_near_delta(
        all_liquid(ctx, "Call"), ctx.cache, "Call", target, strike_filter=lambda s: s > ctx.spot
    )
    if stp_sp is None or stp_sc is None:
        return []
    return enumerate_symmetric_iron_condors(ctx, stp_sp, stp_sc)


def _pick_top_candidates(
    ctx: EngineContext,
    candidates: list[IronCondorCandidate],
    *,
    strategy_id: str,
    top_n: int = IC_RETURN_TOP_N,
) -> tuple[list[tuple[IronCondorCandidate, float]], list[dict]]:
    """Rank by ROR composite score; SPAN annualized return breaks near-ties."""
    span_pool = sorted(candidates, key=lambda c: c.final_score, reverse=True)[:IC_SPAN_REFINE_TOP_N]
    dte = days_to_expiry(ctx.expiry_display)

    span_scores: list[dict] = []
    ann_by_id: dict[int, float] = {}

    for cand in span_pool:
        unit_span = _unit_span_margin(ctx, cand.legs, strategy_id=strategy_id)
        ann_return = score_iron_condor_candidate(
            cand.pop, cand.net_collected, cand.max_loss_u * cand.qty, unit_span, dte
        )
        ann_by_id[id(cand)] = ann_return
        span_scores.append(
            {
                "short_put": cand.short_put,
                "short_call": cand.short_call,
                "wing_width": cand.wing_width,
                "final_score": round(cand.final_score, 4),
                "unit_span": unit_span,
                "annualized_return_pct": round(ann_return, 2),
                "net_collected": cand.net_collected,
            }
        )

    def rank_key(c: IronCondorCandidate) -> tuple[float, float, float]:
        return (c.final_score, ann_by_id.get(id(c), 0.0), c.net_collected)

    ranked = sorted(candidates, key=rank_key, reverse=True)

    winners: list[tuple[IronCondorCandidate, float]] = []
    for cand in ranked[:top_n]:
        ann = ann_by_id.get(id(cand))
        if ann is None:
            ann = score_iron_condor_candidate(
                cand.pop, cand.net_collected, cand.max_loss_u * cand.qty, None, dte
            )
        winners.append((cand, ann))

    return winners, span_scores


def _candidate_to_result(
    ctx: EngineContext,
    cand: IronCondorCandidate,
    *,
    rank: int,
    ann_return: float,
    ranking_summary: str | None,
) -> StrategyResult:
    sid = "iron_condor"
    name = "Iron Condor" if rank == 1 else f"Iron Condor #{rank}"
    max_loss = cand.max_loss_u * cand.qty
    result = make_result(
        ctx,
        sid,
        name,
        cand.legs,
        max_loss=max_loss,
        rr=f"{max_loss:.0f} : {cand.credit * cand.qty:.0f}",
        pop=cand.pop,
        net_premium_val=cand.net_collected,
        variant_rank=rank,
        engine_score=round(cand.final_score, 6),
        ranking_summary=ranking_summary,
        score_breakdown=cand.score_factors,
    )
    result.annualized_return_pct = round(ann_return, 2)
    return result


def calc_iron_condor(ctx: EngineContext) -> list[StrategyResult]:
    sid, name = "iron_condor", "Iron Condor"
    if ctx.halted:
        return [skip(sid, name, ctx.halt_reason or "Market halted")]

    pairs = iron_condor_short_pairs(ctx)
    candidates = _collect_candidates(ctx, pairs)
    if not candidates:
        return [skip(sid, name, "No iron condor meets minimum PoP within risk limits.")]

    if ctx.audit:
        ctx.audit.record_calculation(
            "Iron condor candidate search",
            {"pairs_evaluated": len(pairs), "survivors": len(candidates)},
            {
                "top_scores": [
                    {
                        "short_put": c.short_put,
                        "short_call": c.short_call,
                        "wing_width": c.wing_width,
                        "credit": c.credit,
                        "final_score": round(c.final_score, 4),
                    }
                    for c in sorted(candidates, key=lambda x: x.final_score, reverse=True)[:5]
                ]
            },
            rationale="Enumerated top-K short pairs × all wing widths with ROR scoring.",
        )

    winners, span_scores = _pick_top_candidates(ctx, candidates, strategy_id=sid)
    if not winners:
        return [skip(sid, name, "Could not resolve iron condor finalists.")]

    if ctx.audit:
        ctx.audit.record_calculation(
            "Iron condor SPAN refinement",
            {"finalists": len(span_scores)},
            {"scores": span_scores},
            rationale="Ranked finalists by ROR score; SPAN annualized return breaks near-ties.",
        )

    best_ann = winners[0][1]
    if best_ann < MIN_IC_ANNUALIZED_RETURN_PCT:
        return [
            skip(
                sid,
                name,
                f"Best iron condor annualized return {best_ann:.1f}% below minimum "
                f"{MIN_IC_ANNUALIZED_RETURN_PCT:.1f}%.",
            )
        ]

    results: list[StrategyResult] = []
    for rank, (cand, ann_return) in enumerate(winners, start=1):
        summary: str | None = None
        if rank == 1 and len(winners) > 1:
            runner = winners[1][0]
            summary = build_ranking_summary(
                cand.net_collected,
                cand.pop,
                cand.score_factors.get("ror", 0.0),
                runner.net_collected,
                runner.pop,
                runner.score_factors.get("ror", 0.0),
            )
        elif rank > 1:
            prev = winners[rank - 2][0]
            summary = build_ranking_summary(
                prev.net_collected,
                prev.pop,
                prev.score_factors.get("ror", 0.0),
                cand.net_collected,
                cand.pop,
                cand.score_factors.get("ror", 0.0),
            )
        results.append(
            _candidate_to_result(
                ctx, cand, rank=rank, ann_return=ann_return, ranking_summary=summary
            )
        )

    return results
