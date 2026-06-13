"""Bear call spread strategy calculator — PoP-band search aligned with short strangle."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from icici_breeze_backend.app.services.options_strategy_engine.delta_anchor import (
    abs_delta,
    pop_to_short_delta,
)
from icici_breeze_backend.app.services.options_strategy_engine.helpers import (
    annualized_carry_percent_on_span,
    days_to_expiry,
    legs_to_margin_input,
    meets_pop_floor,
    parse_float,
    requires_pop_gate,
    skip,
)
from icici_breeze_backend.app.services.options_strategy_engine.margin_async_fetch import (
    MarginFetchRequest,
    fetch_margins_concurrent,
)
from icici_breeze_backend.app.services.options_strategy_engine.pop import (
    PopDetail,
    pop_detail_for_legs,
)
from icici_breeze_backend.app.services.options_strategy_engine.pruning import (
    passes_economic_prune,
    wing_strikes_from_multipliers,
)
from icici_breeze_backend.app.services.options_strategy_engine.sizing import (
    legs_at_lots,
    min_qty_for_one_lot,
    structural_margin_key,
)
from icici_breeze_backend.audit.strategy_evaluation_audit import (
    StrategyAuditCollector,
    candidate_id_for_legs,
    pop_bucket_label,
)
from icici_breeze_backend.app.services.options_strategy_engine.audit_helpers import audit_calc
from icici_breeze_backend.app.services.options_strategy_engine.strategies.common import make_result
from icici_breeze_backend.app.services.options_strategy_engine.types import (
    EngineContext,
    QuoteRow,
    Right,
    StrategyResult,
    TradeLeg,
)

# --- Bear call spread tuning constants (owned by this module) ---

BCS_TOP_K_SHORT_STRIKES = 10
BCS_POP_BAND_WIDTH_PCT = 2.0
BCS_SHORT_STRIKES_MAX_ATM = 12
BCS_SHORT_STRIKES_MAX = 12
BCS_SPAN_SHORTLIST_N = 10
BCS_RETURN_TOP_N = 3
MIN_BCS_ANNUALIZED_RETURN_PCT = 5.0
WING_WIDTH_MULTIPLIERS: tuple[int, ...] = (1, 2, 3, 4)

BearCallSpreadRejectionStats = StrategyAuditCollector

_STAGES_PASSED = (
    "passed_liquidity",
    "passed_credit",
    "passed_economic_prune",
    "passed_pop",
    "margin_refined",
    "returned",
)


def score_bear_call_spread_candidate(
    pop_pct: float,
    net_premium: float,
    unit_span: float | None,
    dte: int | None,
) -> float:
    """Annualized carry on SPAN when available, else premium proxy."""
    del pop_pct
    if unit_span and unit_span > 0 and dte is not None and dte > 0:
        return annualized_carry_percent_on_span(net_premium, dte, unit_span)
    return net_premium / max(net_premium, 1.0)


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


def score_bear_call_spread_ror(
    pop_pct: float,
    net_collected: float,
    max_loss: float,
    min_pop_pct: float,
    leg_quotes: list[QuoteRow],
) -> tuple[float, dict[str, float]]:
    """Credit proxy: ROR × liquidity × spread; PoP tiebreak only at floor."""
    ror = net_collected / max(max_loss, 1.0)
    liquidity_vals = [q.liquidity_score for q in leg_quotes if q.liquidity_score > 0]
    spread_vals = [_leg_spread_score(q) for q in leg_quotes]
    liquidity_weight = _geometric_mean(liquidity_vals) if liquidity_vals else 0.5
    spread_weight = _geometric_mean(spread_vals) if spread_vals else 0.5
    score = ror * liquidity_weight * spread_weight
    pop_tiebreak = min(pop_pct, min_pop_pct) / max(min_pop_pct, 1.0)
    score *= 1.0 + 1e-4 * pop_tiebreak
    factors = {
        "ror": round(ror, 6),
        "pop_tiebreak": round(pop_tiebreak, 6),
        "liquidity_weight": round(liquidity_weight, 6),
        "spread_weight": round(spread_weight, 6),
    }
    return score, factors


def build_ranking_summary(
    *,
    higher_rank: int,
    lower_rank: int,
    viewing_rank: int,
    higher_ann_return: float,
    higher_credit: float,
    higher_pop: float,
    lower_ann_return: float,
    lower_credit: float,
    lower_pop: float,
) -> str:
    """Explain how the higher-ranked spread compares to the adjacent lower rank."""
    if viewing_rank == higher_rank:
        lead = f"Ranked #{higher_rank} over #{lower_rank}:"
    else:
        lead = f"#{higher_rank} ranks above this variant:"

    details: list[str] = []
    ann_delta = higher_ann_return - lower_ann_return
    if abs(ann_delta) >= 0.1:
        details.append(
            f"{ann_delta:+.1f}pp annualized return on SPAN "
            f"(#{higher_rank} {higher_ann_return:.1f}% vs "
            f"#{lower_rank} {lower_ann_return:.1f}%)"
        )
    credit_delta = higher_credit - lower_credit
    if abs(credit_delta) >= 1.0:
        if credit_delta > 0:
            details.append(f"₹{credit_delta:.0f} more net credit per lot")
        else:
            details.append(f"₹{abs(credit_delta):.0f} less net credit per lot")
    pop_delta = higher_pop - lower_pop
    if abs(pop_delta) >= 0.5:
        details.append(
            f"PoP #{higher_rank} {higher_pop:.1f}% vs #{lower_rank} {lower_pop:.1f}%"
        )
    if not details:
        details.append("higher income efficiency on deployed SPAN margin")
    return f"{lead} {'; '.join(details)}."


def _bcs_pop_band_delta_bounds(min_pop_pct: float) -> tuple[float, float]:
    """Per-wing abs-delta range for [min_pop, min_pop + band] on single short call."""
    floor_delta = pop_to_short_delta(min_pop_pct, short_legs=1)
    ceil_delta = pop_to_short_delta(min_pop_pct + BCS_POP_BAND_WIDTH_PCT, short_legs=1)
    return ceil_delta, floor_delta


def _bcs_pop_bucket(pop_pct: float, floor_pct: float) -> str:
    return pop_bucket_label(pop_pct, floor_pct, band_width=BCS_POP_BAND_WIDTH_PCT)


def _audit_collector(ctx: EngineContext) -> StrategyAuditCollector | None:
    if ctx.audit_collector is not None:
        c = ctx.audit_collector
        c.min_pop_pct = ctx.min_pop_pct
        c.pop_band_width = BCS_POP_BAND_WIDTH_PCT
        return c
    return None


def _bcs_short_strikes_for_pop_band(
    ctx: EngineContext,
    ce_strikes: list[int],
    min_pop_pct: float,
) -> list[int]:
    """PoP-band shortlist: OTM band strikes plus ATM-ward expansion toward floor credit."""
    cache = ctx.cache
    ceil_delta, floor_delta = _bcs_pop_band_delta_bounds(min_pop_pct)
    scored: list[tuple[float, int]] = []
    for s in ce_strikes:
        q = cache.get((s, "Call"))
        d = abs_delta(q)
        if not q or not q.liquid or d is None:
            continue
        scored.append((d, s))

    seen: set[int] = set()
    band_strikes: list[int] = []
    for d, s in sorted(scored, key=lambda x: -x[0]):
        if ceil_delta <= d <= floor_delta and s not in seen:
            seen.add(s)
            band_strikes.append(s)

    atm_candidates = sorted(
        [(d, s) for d, s in scored if d > floor_delta],
        key=lambda x: -x[0],
    )
    atm_strikes: list[int] = []
    for _, s in atm_candidates[:BCS_SHORT_STRIKES_MAX_ATM]:
        if s not in seen:
            seen.add(s)
            atm_strikes.append(s)

    selected: list[int] = []
    for s in atm_strikes + band_strikes:
        if s not in selected:
            selected.append(s)

    if not selected:
        for _, s in sorted(scored, key=lambda x: abs(x[0] - floor_delta))[:BCS_TOP_K_SHORT_STRIKES]:
            if s not in seen:
                selected.append(s)
    return selected[:BCS_SHORT_STRIKES_MAX]


def bear_call_spread_short_strikes(ctx: EngineContext) -> list[int]:
    """Return short call strikes for bear call spread optimization."""
    ce_strikes = [s for s in ctx.liquid_ce_strikes if s >= ctx.atm_strike]
    return _bcs_short_strikes_for_pop_band(ctx, ce_strikes, ctx.min_pop_pct)


@dataclass(frozen=True)
class BearCallSpreadCandidate:
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


def _bcs_credit_shortlist_key(cand: BearCallSpreadCandidate) -> tuple[float, float]:
    """Net credit DESC; PoP DESC for deterministic ties."""
    return (cand.net_collected, cand.pop)


def _bcs_final_rank_key(
    cand: BearCallSpreadCandidate,
    *,
    ann_return: float,
) -> tuple[float, float, float, float]:
    """Ann return on SPAN, net credit, liquidity×spread, PoP (all DESC)."""
    liq = cand.score_factors.get("liquidity_weight", 0.5)
    spread = cand.score_factors.get("spread_weight", 0.5)
    return (ann_return, cand.net_collected, liq * spread, cand.pop)


def _wing_strikes_for_short(
    ctx: EngineContext,
    short_stp: int,
    wing_strikes: list[int],
) -> list[tuple[int, int]]:
    """Return (long_strike, wing_width) pairs using multiplier widths + liquid fallback."""
    liquid_set = set(wing_strikes)
    wings = wing_strikes_from_multipliers(
        short_stp, ctx.strike_step, liquid_set, wing_is_higher=True
    )
    if not wings:
        wings = [s for s in wing_strikes if s > short_stp]
    out: list[tuple[int, int]] = []
    seen: set[int] = set()
    for wing in wings:
        if wing in seen:
            continue
        seen.add(wing)
        out.append((wing, abs(wing - short_stp)))
    return out


def enumerate_bear_call_spreads(
    ctx: EngineContext,
    short_strike: int,
    *,
    stats: BearCallSpreadRejectionStats | None = None,
) -> list[BearCallSpreadCandidate]:
    """All feasible bear call spreads for a short strike, or empty if all rejected."""
    L = ctx.lot_size
    out: list[BearCallSpreadCandidate] = []
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

    def _accept(
        *,
        long_strike: int,
        wing_width: int,
        pop_detail: PopDetail,
        credit: float,
    ) -> None:
        if stats is not None:
            stats.record_stage("passed_pop")
            stats.record_evaluation(
                short_strike=short_strike,
                long_strike=long_strike,
                wing_width=wing_width,
                outcome="accepted",
                reject_reason=None,
                pop_detail=pop_detail,
                credit=credit,
            )
            stats.record_survivor_metrics(pop_pct=pop_detail.pop_pct, credit=credit)

    qs = ctx.cache.get((short_strike, "Call"))
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
    wing_pairs = _wing_strikes_for_short(ctx, short_strike, ctx.liquid_ce_strikes)

    for long_strike, wing_width in wing_pairs:
        if stats is not None:
            stats.record_generated()
        qw = ctx.cache.get((long_strike, "Call"))
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
            require_pop=requires_pop_gate(ctx),
            min_pop_pct=ctx.min_pop_pct,
        ):
            _reject(
                "economic_prune",
                long_strike=long_strike,
                wing_width=wing_width,
                credit=credit,
            )
            continue
        if stats is not None:
            stats.record_stage("passed_economic_prune")

        qty = min_qty_for_one_lot(L)
        if qty < L:
            _reject("quantity", long_strike=long_strike, wing_width=wing_width)
            continue

        legs = [
            TradeLeg("Call", "Sell", short_strike, qty, short_prem),
            TradeLeg("Call", "Buy", long_strike, qty, wing_prem),
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

        _accept(
            long_strike=long_strike,
            wing_width=wing_width,
            pop_detail=pop_detail,
            credit=credit,
        )
        net_collected = credit * qty
        max_loss = max_loss_u * qty
        leg_quotes: list[QuoteRow] = [qs, qw]
        final_score, score_factors = score_bear_call_spread_ror(
            pop, net_collected, max_loss, ctx.min_pop_pct, leg_quotes
        )
        out.append(
            BearCallSpreadCandidate(
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


def _collect_candidates(
    ctx: EngineContext,
    short_strikes: list[int],
    *,
    stats: BearCallSpreadRejectionStats | None = None,
) -> list[BearCallSpreadCandidate]:
    candidates: list[BearCallSpreadCandidate] = []
    for short_strike in short_strikes:
        candidates.extend(enumerate_bear_call_spreads(ctx, short_strike, stats=stats))
    return candidates


def _build_pop_audit_summary(
    stats: BearCallSpreadRejectionStats,
    candidates: list[BearCallSpreadCandidate],
    min_pop_pct: float,
) -> dict[str, object]:
    survivors_by_bucket: dict[str, int] = {}
    for cand in candidates:
        bucket = _bcs_pop_bucket(cand.pop, min_pop_pct)
        survivors_by_bucket[bucket] = survivors_by_bucket.get(bucket, 0) + 1
    return {
        "pop_distribution": dict(sorted(stats.pop_bucket_counts.items())),
        "survivors_by_pop_bucket": dict(sorted(survivors_by_bucket.items())),
        "pop_band_target": [min_pop_pct, min_pop_pct + BCS_POP_BAND_WIDTH_PCT],
    }


def _bcs_search_rationale() -> str:
    return (
        "PoP-band strike shortlist; PoP hard floor; survivors ranked for highest "
        "annualized return on SPAN (income efficiency), not raw premium."
    )


def _unit_span_margin(
    ctx: EngineContext,
    legs: list,
    *,
    strategy_id: str,
) -> float:
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
        audit_context={
            "strategy_id": strategy_id,
            "legs": margin_input,
            "phase": "bcs_candidate_span",
        },
    )
    span = parse_float((res.get("Success") or {}).get("span_margin_required"))
    ctx.unit_span_by_structure[struct_key] = span
    return span


async def _pick_top_candidates(
    ctx: EngineContext,
    candidates: list[BearCallSpreadCandidate],
    *,
    strategy_id: str,
    stats: StrategyAuditCollector | None = None,
    span_shortlist_n: int = BCS_SPAN_SHORTLIST_N,
    return_top_n: int = BCS_RETURN_TOP_N,
) -> tuple[list[tuple[BearCallSpreadCandidate, float]], list[dict]]:
    """Top credit shortlist; margin only those; re-rank by annualized return on SPAN."""
    if stats is not None:
        stats.begin_ranking()
    dte = days_to_expiry(ctx.expiry_display)
    shortlist = sorted(
        candidates,
        key=_bcs_credit_shortlist_key,
        reverse=True,
    )[:span_shortlist_n]

    margin_requests: list[MarginFetchRequest] = []
    for cand in shortlist:
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
                phase="bcs_candidate_span",
            )
        )

    if margin_requests:
        spans = await fetch_margins_concurrent(
            ctx.processor,
            ctx.user_id,
            ctx.exchange_code,
            margin_requests,
            audit=ctx.audit,
            existing_cache=ctx.unit_span_by_structure,
        )
        ctx.unit_span_by_structure.update(spans)

    span_scores: list[dict] = []
    scored: list[tuple[BearCallSpreadCandidate, float]] = []

    for credit_rank, cand in enumerate(shortlist, start=1):
        unit_span = _unit_span_margin(ctx, cand.legs, strategy_id=strategy_id)
        if unit_span <= 0 and stats is not None:
            stats.record("span_failure", short_strike=cand.short_strike, long_strike=cand.long_strike)
        ann_return = score_bear_call_spread_candidate(
            cand.pop, cand.net_collected, unit_span, dte
        )
        if stats is not None:
            stats.record_survivor_metrics(
                pop_pct=cand.pop,
                credit=cand.credit,
                ann_return_pct=ann_return,
                unit_span=unit_span,
            )
        span_scores.append(
            {
                "short_strike": cand.short_strike,
                "long_strike": cand.long_strike,
                "wing_width": cand.wing_width,
                "shortlist_rank_by_credit": credit_rank,
                "final_score": round(cand.final_score, 4),
                "unit_span": unit_span,
                "annualized_return_pct": round(ann_return, 2),
                "net_collected": cand.net_collected,
            }
        )
        scored.append((cand, ann_return))

    winners = sorted(
        scored,
        key=lambda item: _bcs_final_rank_key(item[0], ann_return=item[1]),
        reverse=True,
    )[:return_top_n]

    winner_keys = {id(c) for c, _ in winners}
    if stats is not None:
        for final_rank, (cand, ann_return) in enumerate(winners, start=1):
            credit_rank = next(
                i for i, sc in enumerate(span_scores, start=1)
                if sc["short_strike"] == cand.short_strike and sc["long_strike"] == cand.long_strike
            )
            span_rank = final_rank
            stats.record_winner(
                candidate_id=candidate_id_for_legs(cand.legs),
                legs=cand.legs,
                metrics={
                    "pop_pct": round(cand.pop, 2),
                    "net_credit": round(cand.credit, 4),
                    "net_collected": cand.net_collected,
                    "annualized_return_pct": round(ann_return, 2),
                    "unit_span": span_scores[credit_rank - 1]["unit_span"],
                    "max_loss": cand.max_loss_u * cand.qty,
                    "engine_score": round(cand.final_score, 4),
                    "liquidity_weight": cand.score_factors.get("liquidity_weight"),
                    "spread_weight": cand.score_factors.get("spread_weight"),
                },
                stages_passed=list(_STAGES_PASSED),
                ranks={"credit": credit_rank, "span": span_rank, "final": final_rank},
            )
        for cand, ann_return in scored:
            if id(cand) in winner_keys:
                continue
            stats.record_near_miss(
                candidate_id=candidate_id_for_legs(cand.legs),
                metrics={
                    "pop_pct": round(cand.pop, 2),
                    "net_collected": cand.net_collected,
                    "annualized_return_pct": round(ann_return, 2),
                },
                rejection_reason="not_finalist",
                context="SPAN shortlist finalist did not rank in top return set.",
            )
        stats.end_ranking(ctx.audit.telemetry if ctx.audit else None)

    return winners, span_scores


def _candidate_to_result(
    ctx: EngineContext,
    cand: BearCallSpreadCandidate,
    *,
    rank: int,
    ann_return: float,
    ranking_summary: str | None,
) -> StrategyResult:
    sid = "bear_call_spread"
    name = "Bear Call Spread" if rank == 1 else f"Bear Call Spread #{rank}"
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
    )
    result.annualized_return_pct = round(ann_return, 2)
    return result


async def calc_bear_call_spread(ctx: EngineContext) -> list[StrategyResult]:
    sid, name = "bear_call_spread", "Bear Call Spread"
    if ctx.halted:
        return [skip(sid, name, ctx.halt_reason or "Market halted")]

    stats = _audit_collector(ctx)
    short_strikes = bear_call_spread_short_strikes(ctx)
    candidates = _collect_candidates(ctx, short_strikes, stats=stats)
    if stats is not None:
        stats.end_generation(ctx.audit.telemetry if ctx.audit else None)

    if not candidates:
        skip_reason = stats.skip_message() if stats else (
            "No bear call spread meets minimum PoP within risk limits."
        )
        if ctx.audit:
            audit_calc(
                ctx,
                "Bear call spread candidate search",
                {
                    "shorts_evaluated": len(short_strikes),
                    "survivors": 0,
                    "pop_band_target": [ctx.min_pop_pct, ctx.min_pop_pct + BCS_POP_BAND_WIDTH_PCT],
                },
                {
                    "rejection_counts": stats.counts if stats else {},
                    "combos_tried": stats.combos_tried if stats else 0,
                },
                rationale="No bear call spread passed PoP filters.",
                strategy_id=sid,
            )
        return [skip(sid, name, skip_reason)]

    if ctx.audit:
        audit_calc(
            ctx,
            "Bear call spread candidate search",
            {
                "shorts_evaluated": len(short_strikes),
                "survivors": len(candidates),
                "pop_band_target": [ctx.min_pop_pct, ctx.min_pop_pct + BCS_POP_BAND_WIDTH_PCT],
            },
            {
                "rejection_counts": stats.counts if stats else {},
                "combos_tried": stats.combos_tried if stats else 0,
                "top_scores": [
                    {
                        "short_strike": c.short_strike,
                        "long_strike": c.long_strike,
                        "wing_width": c.wing_width,
                        "credit": c.credit,
                        "pop": round(c.pop, 2),
                        "final_score": round(c.final_score, 4),
                    }
                    for c in sorted(
                        candidates,
                        key=lambda x: (x.net_collected, x.pop),
                        reverse=True,
                    )[:5]
                ],
            },
            rationale=_bcs_search_rationale(),
            strategy_id=sid,
        )

    winners, span_scores = await _pick_top_candidates(
        ctx, candidates, strategy_id=sid, stats=stats
    )
    if not winners:
        return [skip(sid, name, "Could not resolve bear call spread finalists.")]

    if ctx.audit:
        audit_calc(
            ctx,
            "Bear call spread SPAN refinement",
            {"finalists": len(span_scores)},
            {"scores": span_scores},
            rationale=(
                f"Top {BCS_SPAN_SHORTLIST_N} by net credit shortlisted; SPAN margin fetched "
                "for finalists only; re-ranked by annualized return on SPAN, then net "
                f"credit, liquidity/spread, PoP; top {BCS_RETURN_TOP_N} returned."
            ),
            strategy_id=sid,
        )

    best_ann = winners[0][1]
    if best_ann < MIN_BCS_ANNUALIZED_RETURN_PCT:
        if stats is not None and winners:
            cand, _ = winners[0]
            stats.record_near_miss(
                candidate_id=candidate_id_for_legs(cand.legs),
                metrics={
                    "pop_pct": round(cand.pop, 2),
                    "net_collected": cand.net_collected,
                    "annualized_return_pct": round(best_ann, 2),
                },
                rejection_reason="below_min_ann_return",
                context=(
                    f"Best annualized return {best_ann:.1f}% below minimum "
                    f"{MIN_BCS_ANNUALIZED_RETURN_PCT:.1f}%."
                ),
            )
        return [
            skip(
                sid,
                name,
                f"Best bear call spread annualized return {best_ann:.1f}% below minimum "
                f"{MIN_BCS_ANNUALIZED_RETURN_PCT:.1f}%.",
            )
        ]

    results: list[StrategyResult] = []
    for rank, (cand, ann_return) in enumerate(winners, start=1):
        summary: str | None = None
        if rank == 1 and len(winners) > 1:
            runner = winners[1][0]
            summary = build_ranking_summary(
                higher_rank=1,
                lower_rank=2,
                viewing_rank=1,
                higher_ann_return=ann_return,
                higher_credit=cand.net_collected,
                higher_pop=cand.pop,
                lower_ann_return=winners[1][1],
                lower_credit=runner.net_collected,
                lower_pop=runner.pop,
            )
        elif rank > 1:
            prev = winners[rank - 2][0]
            summary = build_ranking_summary(
                higher_rank=rank - 1,
                lower_rank=rank,
                viewing_rank=rank,
                higher_ann_return=winners[rank - 2][1],
                higher_credit=prev.net_collected,
                higher_pop=prev.pop,
                lower_ann_return=ann_return,
                lower_credit=cand.net_collected,
                lower_pop=cand.pop,
            )
        results.append(
            _candidate_to_result(
                ctx, cand, rank=rank, ann_return=ann_return, ranking_summary=summary
            )
        )

    return results


def prefetch_bear_call_spread(ctx: EngineContext) -> set[tuple[int, Right]]:
    from icici_breeze_backend.app.services.options_strategy_engine.strategies.common import (
        prefetch_atm_pairs,
    )

    pairs = prefetch_atm_pairs(ctx)
    for s in bear_call_spread_short_strikes(ctx):
        pairs.add((s, "Call"))
        for mult in WING_WIDTH_MULTIPLIERS:
            pairs.add((s + mult * ctx.strike_step, "Call"))
    return pairs
