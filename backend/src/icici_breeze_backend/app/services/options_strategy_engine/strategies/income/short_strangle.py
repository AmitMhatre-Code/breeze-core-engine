"""Short strangle strategy calculator — PoP-band search aligned with iron condor."""
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
    skip,
)
from icici_breeze_backend.app.services.options_strategy_engine.pop import (
    PopDetail,
    pop_detail_for_legs,
    pop_for_legs,
)
from icici_breeze_backend.app.services.options_strategy_engine.margin_async_fetch import (
    MarginFetchRequest,
    fetch_margins_concurrent,
)
from icici_breeze_backend.app.services.options_strategy_engine.sizing import (
    legs_at_lots,
    min_qty_for_one_lot,
    structural_margin_key,
)
from icici_breeze_backend.app.services.options_strategy_engine.strategies.common import make_result
from icici_breeze_backend.app.services.options_strategy_engine.types import (
    EngineContext,
    QuoteRow,
    Right,
    StrategyResult,
    TradeLeg,
)

# --- Short strangle tuning constants (owned by this module) ---

SS_SHORT_STRIKES_INWARD = 7
SS_SHORT_STRIKES_OUTWARD = 3
SS_TOP_K_SHORT_STRIKES = SS_SHORT_STRIKES_INWARD + SS_SHORT_STRIKES_OUTWARD
SS_POP_BAND_WIDTH_PCT = 2.0
SS_SHORT_STRIKES_MAX_ATM = 12
SS_SHORT_STRIKES_MAX_PER_WING = 12
SS_SPAN_SHORTLIST_N = 10
SS_RETURN_TOP_N = 3
MIN_SS_ANNUALIZED_RETURN_PCT = 5.0


def score_short_strangle_candidate(
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


def score_short_strangle_ror(
    pop_pct: float,
    net_premium: float,
    min_pop_pct: float,
    leg_quotes: list[QuoteRow],
) -> tuple[float, dict[str, float]]:
    """Credit proxy: ROR × liquidity × spread; PoP tiebreak only at floor."""
    ror = net_premium / max(net_premium, 1.0)
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
    """Explain how the higher-ranked strangle compares to the adjacent lower rank."""
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


def _ss_pop_band_delta_bounds(min_pop_pct: float) -> tuple[float, float]:
    """Per-wing abs-delta range for [min_pop, min_pop + band] on symmetric shorts."""
    floor_delta = pop_to_short_delta(min_pop_pct, short_legs=2)
    ceil_delta = pop_to_short_delta(min_pop_pct + SS_POP_BAND_WIDTH_PCT, short_legs=2)
    return ceil_delta, floor_delta


def _ss_pop_bucket(pop_pct: float, floor_pct: float) -> str:
    """1% PoP bucket anchored at the user floor."""
    if pop_pct < floor_pct:
        return f"<{floor_pct:.0f}"
    offset = int((pop_pct - floor_pct) // 1.0)
    if offset >= SS_POP_BAND_WIDTH_PCT + 1:
        return f">={floor_pct + SS_POP_BAND_WIDTH_PCT + 1:.0f}"
    lo = floor_pct + offset
    hi = lo + 1.0
    return f"{lo:.0f}-{hi:.0f}"


def _pop_short_strangle_pair(
    ctx: EngineContext,
    short_put: int,
    short_call: int,
) -> float:
    """Breakeven PoP for a short put + short call pair."""
    pe = ctx.cache.get((short_put, "Put"))
    ce = ctx.cache.get((short_call, "Call"))
    if not pe or not ce or not pe.liquid or not ce.liquid:
        return 0.0
    prem_p = pe.best_bid_price or pe.ltp
    prem_c = ce.best_bid_price or ce.ltp
    legs = [
        TradeLeg("Put", "Sell", short_put, 1, prem_p),
        TradeLeg("Call", "Sell", short_call, 1, prem_c),
    ]
    return pop_for_legs(ctx, legs)


def _pop_band_covered(
    ctx: EngineContext,
    short_puts: list[int],
    short_calls: list[int],
    min_pop_pct: float,
) -> bool:
    """True when shortlists span [floor, floor+band]: lower and upper PoP sub-ranges."""
    lo = min_pop_pct
    hi = min_pop_pct + SS_POP_BAND_WIDTH_PCT
    mid = lo + SS_POP_BAND_WIDTH_PCT / 2.0
    has_lower = False
    has_upper = False
    for sp in short_puts:
        for sc in short_calls:
            if sp >= sc:
                continue
            pop = _pop_short_strangle_pair(ctx, sp, sc)
            if lo <= pop <= mid:
                has_lower = True
            if mid <= pop <= hi:
                has_upper = True
    return has_lower and has_upper


def _ss_short_strikes_for_pop_band(
    ctx: EngineContext,
    strikes: list[int],
    right: Right,
    min_pop_pct: float,
    *,
    opposite_strikes: list[int] | None = None,
) -> list[int]:
    """PoP-band shortlist: OTM band strikes plus ATM-ward expansion toward floor credit."""
    cache = ctx.cache
    ceil_delta, floor_delta = _ss_pop_band_delta_bounds(min_pop_pct)
    scored: list[tuple[float, int]] = []
    for s in strikes:
        q = cache.get((s, right))
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
    for _, s in atm_candidates[:SS_SHORT_STRIKES_MAX_ATM]:
        if s not in seen:
            seen.add(s)
            atm_strikes.append(s)

    selected: list[int] = []
    for s in atm_strikes + band_strikes:
        if s not in selected:
            selected.append(s)

    if opposite_strikes is not None and not _pop_band_covered(
        ctx,
        selected if right == "Put" else opposite_strikes,
        opposite_strikes if right == "Put" else selected,
        min_pop_pct,
    ):
        otm_extended = sorted(
            [(d, s) for d, s in scored if d < ceil_delta],
            key=lambda x: -x[0],
        )
        for _, s in otm_extended:
            if s in seen:
                continue
            seen.add(s)
            selected.append(s)
            if right == "Put":
                if _pop_band_covered(ctx, selected, opposite_strikes, min_pop_pct):
                    break
            elif _pop_band_covered(ctx, opposite_strikes, selected, min_pop_pct):
                break
            if len(selected) >= SS_SHORT_STRIKES_MAX_ATM + SS_TOP_K_SHORT_STRIKES:
                break

    if not selected:
        for _, s in sorted(scored, key=lambda x: abs(x[0] - floor_delta))[:SS_TOP_K_SHORT_STRIKES]:
            if s not in seen:
                selected.append(s)
    return selected[:SS_SHORT_STRIKES_MAX_PER_WING]


def short_strangle_pairs(ctx: EngineContext) -> list[tuple[int, int]]:
    """Return (short_put, short_call) shortlists for short strangle optimization."""
    pe_strikes = [s for s in ctx.liquid_pe_strikes if s < ctx.spot]
    ce_strikes = [s for s in ctx.liquid_ce_strikes if s > ctx.spot]
    short_puts = _ss_short_strikes_for_pop_band(ctx, pe_strikes, "Put", ctx.min_pop_pct)
    short_calls = _ss_short_strikes_for_pop_band(ctx, ce_strikes, "Call", ctx.min_pop_pct)
    for _ in range(3):
        new_puts = _ss_short_strikes_for_pop_band(
            ctx,
            pe_strikes,
            "Put",
            ctx.min_pop_pct,
            opposite_strikes=short_calls,
        )
        new_calls = _ss_short_strikes_for_pop_band(
            ctx,
            ce_strikes,
            "Call",
            ctx.min_pop_pct,
            opposite_strikes=new_puts,
        )
        if new_puts == short_puts and new_calls == short_calls:
            break
        short_puts, short_calls = new_puts, new_calls

    out: list[tuple[int, int]] = []
    for sp in short_puts:
        for sc in short_calls:
            if sp >= sc:
                continue
            out.append((sp, sc))
    return out


@dataclass
class ShortStrangleRejectionStats:
    """Tracks why short strangle combos were rejected during search."""

    combos_tried: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    samples: list[dict] = field(default_factory=list)
    evaluations: list[dict] = field(default_factory=list)
    pop_bucket_counts: dict[str, int] = field(default_factory=dict)
    survivors_by_pop_bucket: dict[str, int] = field(default_factory=dict)
    min_pop_pct: float = 0.0

    def record_evaluation(
        self,
        *,
        short_put: int,
        short_call: int,
        outcome: str,
        reject_reason: str | None = None,
        pop_detail: PopDetail | None = None,
        credit: float | None = None,
    ) -> None:
        entry: dict[str, object] = {
            "short_put": short_put,
            "short_call": short_call,
            "outcome": outcome,
            "reject_reason": reject_reason,
            "credit": round(credit, 4) if credit is not None else None,
        }
        pop_pct: float | None = None
        if pop_detail is not None:
            entry.update(pop_detail.to_audit_dict())
            pop_pct = pop_detail.pop_pct
        else:
            entry["pop_pct"] = None
            entry["pop_basis"] = None
        self.evaluations.append(entry)

        if pop_pct is not None and self.min_pop_pct > 0:
            bucket = _ss_pop_bucket(pop_pct, self.min_pop_pct)
            self.pop_bucket_counts[bucket] = self.pop_bucket_counts.get(bucket, 0) + 1
            if outcome == "accepted":
                self.survivors_by_pop_bucket[bucket] = (
                    self.survivors_by_pop_bucket.get(bucket, 0) + 1
                )

    def record(self, reason: str, **detail: object) -> None:
        self.combos_tried += 1
        self.counts[reason] = self.counts.get(reason, 0) + 1
        if len(self.samples) < 25:
            self.samples.append({"reason": reason, **detail})

    def skip_message(self) -> str:
        if not self.counts:
            return "No short strangle candidates could be evaluated on the liquid chain."
        total = sum(self.counts.values())
        parts = ", ".join(
            f"{count} {reason}" for reason, count in sorted(self.counts.items(), key=lambda x: -x[1])
        )
        top_reason = max(self.counts.items(), key=lambda x: x[1])[0]
        if top_reason == "pop_floor":
            return (
                f"No short strangle meets minimum PoP within risk limits "
                f"({total} rejected: {parts})."
            )
        return f"No short strangle passed filters ({total} rejected: {parts})."


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


def _ss_credit_shortlist_key(cand: ShortStrangleCandidate) -> tuple[float, float]:
    """Net credit DESC; PoP DESC for deterministic ties."""
    return (cand.net_collected, cand.pop)


def _ss_final_rank_key(
    cand: ShortStrangleCandidate,
    *,
    ann_return: float,
) -> tuple[float, float, float, float]:
    """Ann return on SPAN, net credit, liquidity×spread, PoP (all DESC)."""
    liq = cand.score_factors.get("liquidity_weight", 0.5)
    spread = cand.score_factors.get("spread_weight", 0.5)
    return (ann_return, cand.net_collected, liq * spread, cand.pop)


def enumerate_short_strangles(
    ctx: EngineContext,
    short_put: int,
    short_call: int,
    *,
    stats: ShortStrangleRejectionStats | None = None,
) -> list[ShortStrangleCandidate]:
    """Feasible short strangle for a put/call pair, or empty if rejected."""
    L = ctx.lot_size
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

    def _accept(
        *,
        pop_detail: PopDetail,
        credit: float,
    ) -> None:
        if stats is not None:
            stats.record_evaluation(
                short_put=short_put,
                short_call=short_call,
                outcome="accepted",
                reject_reason=None,
                pop_detail=pop_detail,
                credit=credit,
            )

    pe = ctx.cache.get((short_put, "Put"))
    ce = ctx.cache.get((short_call, "Call"))
    if not pe or not ce:
        _reject("missing_quote")
        return out
    if not pe.liquid or not ce.liquid:
        _reject("illiquid")
        return out

    prem_p = pe.best_bid_price or pe.ltp
    prem_c = ce.best_bid_price or ce.ltp
    credit = prem_p + prem_c
    if credit <= 0:
        _reject("no_credit", credit=credit)
        return out

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
        _reject(
            "pop_floor",
            pop_detail=pop_detail,
            credit=credit,
            floor=ctx.min_pop_pct,
        )
        return out

    _accept(pop_detail=pop_detail, credit=credit)
    net_collected = credit * qty
    leg_quotes: list[QuoteRow] = [pe, ce]
    final_score, score_factors = score_short_strangle_ror(
        pop, net_collected, ctx.min_pop_pct, leg_quotes
    )
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


def _collect_candidates(
    ctx: EngineContext,
    pairs: list[tuple[int, int]],
    *,
    stats: ShortStrangleRejectionStats | None = None,
) -> list[ShortStrangleCandidate]:
    candidates: list[ShortStrangleCandidate] = []
    for sp, sc in pairs:
        candidates.extend(enumerate_short_strangles(ctx, sp, sc, stats=stats))
    return candidates


def _build_pop_audit_summary(
    stats: ShortStrangleRejectionStats,
    candidates: list[ShortStrangleCandidate],
    min_pop_pct: float,
) -> dict[str, object]:
    survivors_by_bucket: dict[str, int] = {}
    for cand in candidates:
        bucket = _ss_pop_bucket(cand.pop, min_pop_pct)
        survivors_by_bucket[bucket] = survivors_by_bucket.get(bucket, 0) + 1
    return {
        "pop_distribution": dict(sorted(stats.pop_bucket_counts.items())),
        "survivors_by_pop_bucket": dict(sorted(survivors_by_bucket.items())),
        "pop_band_target": [min_pop_pct, min_pop_pct + SS_POP_BAND_WIDTH_PCT],
    }


def _ss_search_rationale() -> str:
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
            "phase": "ss_candidate_span",
        },
    )
    span = parse_float((res.get("Success") or {}).get("span_margin_required"))
    ctx.unit_span_by_structure[struct_key] = span
    return span


async def _pick_top_candidates(
    ctx: EngineContext,
    candidates: list[ShortStrangleCandidate],
    *,
    strategy_id: str,
    span_shortlist_n: int = SS_SPAN_SHORTLIST_N,
    return_top_n: int = SS_RETURN_TOP_N,
) -> tuple[list[tuple[ShortStrangleCandidate, float]], list[dict]]:
    """Top credit shortlist; margin only those; re-rank by annualized return on SPAN."""
    dte = days_to_expiry(ctx.expiry_display)
    shortlist = sorted(
        candidates,
        key=_ss_credit_shortlist_key,
        reverse=True,
    )[:span_shortlist_n]

    margin_requests: list[MarginFetchRequest] = []
    for cand in shortlist:
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
                phase="ss_candidate_span",
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
    scored: list[tuple[ShortStrangleCandidate, float]] = []

    for credit_rank, cand in enumerate(shortlist, start=1):
        unit_span = _unit_span_margin(ctx, cand.legs, strategy_id=strategy_id)
        ann_return = score_short_strangle_candidate(
            cand.pop, cand.net_collected, unit_span, dte
        )
        span_scores.append(
            {
                "short_put": cand.short_put,
                "short_call": cand.short_call,
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
        key=lambda item: _ss_final_rank_key(item[0], ann_return=item[1]),
        reverse=True,
    )[:return_top_n]
    return winners, span_scores


def _candidate_to_result(
    ctx: EngineContext,
    cand: ShortStrangleCandidate,
    *,
    rank: int,
    ann_return: float,
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
    )
    result.annualized_return_pct = round(ann_return, 2)
    return result


async def calc_short_strangle(ctx: EngineContext) -> list[StrategyResult]:
    sid, name = "short_strangle", "Short Strangle"
    if ctx.halted:
        return [skip(sid, name, ctx.halt_reason or "Market halted")]

    stats = ShortStrangleRejectionStats() if ctx.audit else None
    pairs = short_strangle_pairs(ctx)
    candidates = _collect_candidates(ctx, pairs, stats=stats)

    if not candidates:
        skip_reason = stats.skip_message() if stats else (
            "No short strangle meets minimum PoP on the liquid chain."
        )
        if ctx.audit and stats is not None:
            pop_summary = _build_pop_audit_summary(stats, [], ctx.min_pop_pct)
            ctx.audit.record_calculation(
                "Short strangle candidate search",
                {
                    "pairs_evaluated": len(pairs),
                    "survivors": 0,
                    "pop_band_target": pop_summary["pop_band_target"],
                },
                {
                    "rejection_counts": stats.counts,
                    "combos_tried": stats.combos_tried,
                    "samples": stats.samples[:15],
                    "candidates_evaluated": stats.evaluations,
                    **pop_summary,
                },
                rationale="No short strangle passed PoP filters.",
            )
        return [skip(sid, name, skip_reason)]

    if ctx.audit:
        audit_outputs: dict = {
            "top_scores": [
                {
                    "short_put": c.short_put,
                    "short_call": c.short_call,
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
        }
        if stats is not None:
            audit_outputs["rejection_counts"] = stats.counts
            audit_outputs["combos_tried"] = stats.combos_tried
            audit_outputs["rejection_samples"] = stats.samples[:10]
            audit_outputs["candidates_evaluated"] = stats.evaluations
            audit_outputs.update(
                _build_pop_audit_summary(stats, candidates, ctx.min_pop_pct)
            )
        ctx.audit.record_calculation(
            "Short strangle candidate search",
            {
                "pairs_evaluated": len(pairs),
                "survivors": len(candidates),
                "pop_band_target": [ctx.min_pop_pct, ctx.min_pop_pct + SS_POP_BAND_WIDTH_PCT],
            },
            audit_outputs,
            rationale=_ss_search_rationale(),
        )

    winners, span_scores = await _pick_top_candidates(ctx, candidates, strategy_id=sid)
    if not winners:
        return [skip(sid, name, "Could not resolve short strangle finalists.")]

    if ctx.audit:
        ctx.audit.record_calculation(
            "Short strangle SPAN refinement",
            {"finalists": len(span_scores)},
            {"scores": span_scores},
            rationale=(
                f"Top {SS_SPAN_SHORTLIST_N} by net credit shortlisted; SPAN margin fetched "
                "for finalists only; re-ranked by annualized return on SPAN, then net "
                f"credit, liquidity/spread, PoP; top {SS_RETURN_TOP_N} returned."
            ),
        )

    best_ann = winners[0][1]
    if best_ann < MIN_SS_ANNUALIZED_RETURN_PCT:
        return [
            skip(
                sid,
                name,
                f"Best short strangle annualized return {best_ann:.1f}% below minimum "
                f"{MIN_SS_ANNUALIZED_RETURN_PCT:.1f}%.",
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


def prefetch_short_strangle(ctx: EngineContext) -> set[tuple[int, Right]]:
    from icici_breeze_backend.app.services.options_strategy_engine.strategies.common import (
        prefetch_atm_pairs,
    )

    pairs = prefetch_atm_pairs(ctx)
    for sp, sc in short_strangle_pairs(ctx):
        pairs.add((sp, "Put"))
        pairs.add((sc, "Call"))
    return pairs
