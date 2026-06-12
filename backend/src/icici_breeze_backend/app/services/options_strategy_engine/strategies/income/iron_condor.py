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
from icici_breeze_backend.app.services.options_strategy_engine.ranking import score_iron_condor_candidate
from icici_breeze_backend.app.services.options_strategy_engine.sizing import legs_at_lots
from icici_breeze_backend.app.services.options_strategy_engine.strategies.base import (
    IronCondorCandidate,
    all_liquid,
    evaluate_symmetric_iron_condor,
    make_result,
)
from icici_breeze_backend.app.services.options_strategy_engine.strategies.income._common import short_delta
from icici_breeze_backend.app.services.options_strategy_engine.types import (
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
        cand = evaluate_symmetric_iron_condor(ctx, sp, sc)
        if cand:
            candidates.append(cand)

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
    cand = evaluate_symmetric_iron_condor(ctx, stp_sp, stp_sc)
    return [cand] if cand else []


def _pick_winner(
    ctx: EngineContext,
    candidates: list[IronCondorCandidate],
    *,
    strategy_id: str,
) -> tuple[IronCondorCandidate | None, float, list[dict]]:
    candidates.sort(key=lambda c: (c.proxy_score, c.net_collected), reverse=True)
    finalists = candidates[:IC_SPAN_REFINE_TOP_N]
    dte = days_to_expiry(ctx.expiry_display)

    span_scores: list[dict] = []
    best: IronCondorCandidate | None = None
    best_return = -1.0

    for cand in finalists:
        unit_span = _unit_span_margin(ctx, cand.legs, strategy_id=strategy_id)
        ann_return = score_iron_condor_candidate(
            cand.pop, cand.net_collected, cand.max_loss_u * cand.qty, unit_span, dte
        )
        span_scores.append(
            {
                "short_put": cand.short_put,
                "short_call": cand.short_call,
                "wing_width": cand.wing_width,
                "proxy_score": round(cand.proxy_score, 4),
                "unit_span": unit_span,
                "annualized_return_pct": round(ann_return, 2),
                "net_collected": cand.net_collected,
            }
        )
        if unit_span <= 0:
            continue
        if ann_return > best_return:
            best_return = ann_return
            best = cand
        elif (
            ann_return == best_return
            and best is not None
            and cand.net_collected > best.net_collected
        ):
            best = cand

    if best is None and finalists:
        best = finalists[0]
        best_return = score_iron_condor_candidate(
            best.pop, best.net_collected, best.max_loss_u * best.qty, None, dte
        )

    return best, best_return, span_scores


def calc_iron_condor(ctx: EngineContext) -> StrategyResult:
    sid, name = "iron_condor", "Iron Condor"
    if ctx.halted:
        return skip(sid, name, ctx.halt_reason or "Market halted")

    pairs = iron_condor_short_pairs(ctx)
    candidates = _collect_candidates(ctx, pairs)
    if not candidates:
        return skip(sid, name, "No iron condor meets minimum PoP within risk limits.")

    if ctx.audit:
        ctx.audit.record_calculation(
            "Iron condor candidate search",
            {"pairs_evaluated": len(pairs), "survivors": len(candidates)},
            {
                "top_proxy": [
                    {
                        "short_put": c.short_put,
                        "short_call": c.short_call,
                        "wing_width": c.wing_width,
                        "credit": c.credit,
                        "proxy_score": round(c.proxy_score, 4),
                    }
                    for c in sorted(candidates, key=lambda x: x.proxy_score, reverse=True)[:5]
                ]
            },
            rationale="Enumerated top-K short pairs × wing multipliers with per-wing credit gate.",
        )

    winner, best_return, span_scores = _pick_winner(ctx, candidates, strategy_id=sid)
    if winner is None:
        return skip(sid, name, "Could not resolve SPAN margin for iron condor finalists.")

    if ctx.audit:
        ctx.audit.record_calculation(
            "Iron condor SPAN refinement",
            {"finalists": len(span_scores)},
            {"scores": span_scores, "winner_short_put": winner.short_put, "winner_short_call": winner.short_call},
            rationale="Ranked top finalists by annualized return on one-lot SPAN.",
        )

    if best_return < MIN_IC_ANNUALIZED_RETURN_PCT:
        return skip(
            sid,
            name,
            f"Best iron condor annualized return {best_return:.1f}% below minimum "
            f"{MIN_IC_ANNUALIZED_RETURN_PCT:.1f}%.",
        )

    max_loss = winner.max_loss_u * winner.qty
    return make_result(
        ctx,
        sid,
        name,
        winner.legs,
        max_loss=max_loss,
        rr=f"{max_loss:.0f} : {winner.credit * winner.qty:.0f}",
        pop=winner.pop,
        net_premium_val=winner.net_collected,
    )
