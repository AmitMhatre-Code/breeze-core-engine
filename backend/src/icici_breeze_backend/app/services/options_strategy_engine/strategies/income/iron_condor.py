"""Iron condor strategy calculator — all IC-specific logic lives here."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from icici_breeze_backend.app.services.options_strategy_engine.delta_anchor import (
    abs_delta,
    best_strike_near_delta,
    pop_to_short_delta,
    strikes_ranked_by_delta,
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
from icici_breeze_backend.app.services.options_strategy_engine.pruning import passes_economic_prune
from icici_breeze_backend.app.services.options_strategy_engine.ranking import score_credit_trade
from icici_breeze_backend.app.services.options_strategy_engine.sizing import (
    legs_at_lots,
    min_qty_for_one_lot,
    structural_margin_key,
)
from icici_breeze_backend.app.services.options_strategy_engine.strategies.common import all_liquid, make_result
from icici_breeze_backend.app.services.options_strategy_engine.types import (
    EngineContext,
    QuoteRow,
    Right,
    StrategyResult,
    TradeLeg,
)

# --- Iron condor tuning constants (owned by this module) ---

WING_WIDTH_MULTIPLIERS: tuple[int, ...] = (1, 2, 3, 4)
IC_SHORT_STRIKES_INWARD = 3
IC_SHORT_STRIKES_OUTWARD = 3
IC_TOP_K_SHORT_STRIKES = IC_SHORT_STRIKES_INWARD + IC_SHORT_STRIKES_OUTWARD
MIN_WING_CREDIT = 0.05
MIN_IC_CREDIT_PCT_OF_WIDTH = 0.03
IC_CREDIT_PCT_RELAXATION_SCHEDULE: tuple[float, ...] = (0.03, 0.025, 0.02, 0.015, 0.01)
MIN_IC_ANNUALIZED_RETURN_PCT = 5.0
IC_RETURN_TOP_N = 5


def pop_iron_condor_short_pair(
    put_delta: float | None,
    call_delta: float | None,
) -> float:
    if put_delta is None or call_delta is None:
        return 0.0
    est = 1.0 - (abs(put_delta) + call_delta)
    return max(0.0, min(100.0, est * 100.0))


def iron_condor_short_delta_window(min_pop_pct: float) -> tuple[float, float]:
    """PoP-targeted absolute delta window for iron condor short legs."""
    target = pop_to_short_delta(min_pop_pct, short_legs=2)
    lo = max(0.02, target * 0.8)
    hi = min(0.40, max(target * 3.2, lo + 0.02))
    return lo, hi


def score_iron_condor_candidate(
    pop_pct: float,
    net_premium: float,
    max_loss: float,
    unit_span: float | None,
    dte: int | None,
) -> float:
    """Annualized carry on SPAN when available, else ROR-only proxy."""
    del pop_pct
    if unit_span and unit_span > 0 and dte is not None and dte > 0:
        return annualized_carry_percent_on_span(net_premium, dte, unit_span)
    return net_premium / max(max_loss, 1.0)


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


def score_iron_condor_ror(
    pop_pct: float,
    net_premium: float,
    max_loss: float,
    min_pop_pct: float,
    leg_quotes: list[QuoteRow],
) -> tuple[float, dict[str, float]]:
    """Credit proxy: ROR × liquidity × spread; PoP tiebreak only at floor."""
    ror = net_premium / max(max_loss, 1.0)
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
    higher_credit: float,
    higher_pop: float,
    higher_ror: float,
    lower_credit: float,
    lower_pop: float,
    lower_ror: float,
) -> str:
    """Explain how the higher-ranked condor compares to the adjacent lower rank."""
    if viewing_rank == higher_rank:
        lead = f"Ranked #{higher_rank} over #{lower_rank}:"
    else:
        lead = f"#{higher_rank} ranks above this variant:"

    details: list[str] = []
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
    if abs(higher_ror - lower_ror) >= 0.001:
        details.append(
            f"ROR #{higher_rank} {higher_ror:.3f} vs #{lower_rank} {lower_ror:.3f}"
        )
    if not details:
        details.append("higher composite ROR score")
    return f"{lead} {'; '.join(details)}."


def _ic_short_strikes_around_target(
    strikes: list[int],
    cache: dict[tuple[int, Right], QuoteRow],
    right: Right,
    target: float,
    *,
    inward: int = IC_SHORT_STRIKES_INWARD,
    outward: int = IC_SHORT_STRIKES_OUTWARD,
) -> list[int]:
    """Strikes on both sides of target delta: ATM-ward (more credit) and OTM-ward."""
    scored: list[tuple[float, int]] = []
    for s in strikes:
        q = cache.get((s, right))
        d = abs_delta(q)
        if not q or not q.liquid or d is None:
            continue
        scored.append((d, s))

    atmward = sorted(
        [(d, s) for d, s in scored if d >= target],
        key=lambda x: x[0],
    )[:inward]
    otmward = sorted(
        [(d, s) for d, s in scored if d <= target],
        key=lambda x: -x[0],
    )[:outward]

    out: list[int] = []
    seen: set[int] = set()
    for _, s in atmward + otmward:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def iron_condor_short_pairs(ctx: EngineContext) -> list[tuple[int, int]]:
    """Return (short_put, short_call) shortlists for iron condor optimization."""
    target = pop_to_short_delta(ctx.min_pop_pct, short_legs=2)
    short_puts = _ic_short_strikes_around_target(
        [s for s in ctx.liquid_pe_strikes if s < ctx.spot],
        ctx.cache,
        "Put",
        target,
    )
    short_calls = _ic_short_strikes_around_target(
        [s for s in ctx.liquid_ce_strikes if s > ctx.spot],
        ctx.cache,
        "Call",
        target,
    )

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
            if ctx.strategy_category == "income" and est_pop < ctx.min_pop_pct:
                continue
            out.append((sp, sc))
    return out


@dataclass
class IronCondorRejectionStats:
    """Tracks why symmetric iron condor combos were rejected during search."""

    combos_tried: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    samples: list[dict] = field(default_factory=list)
    evaluations: list[dict] = field(default_factory=list)
    pair_wing_plans: list[dict] = field(default_factory=list)

    def record_pair_wing_plan(
        self,
        short_put: int,
        short_call: int,
        wing_widths: list[int],
    ) -> None:
        self.pair_wing_plans.append(
            {
                "short_put": short_put,
                "short_call": short_call,
                "wing_widths_attempted": wing_widths,
            }
        )

    def record_evaluation(
        self,
        *,
        short_put: int,
        short_call: int,
        wing_width: int,
        long_put: int | None = None,
        long_call: int | None = None,
        outcome: str,
        reject_reason: str | None = None,
        pop_detail: PopDetail | None = None,
        credit: float | None = None,
        put_credit: float | None = None,
        call_credit: float | None = None,
    ) -> None:
        entry: dict[str, object] = {
            "short_put": short_put,
            "short_call": short_call,
            "wing_width": wing_width,
            "long_put": long_put,
            "long_call": long_call,
            "outcome": outcome,
            "reject_reason": reject_reason,
            "credit": round(credit, 4) if credit is not None else None,
            "put_credit": round(put_credit, 4) if put_credit is not None else None,
            "call_credit": round(call_credit, 4) if call_credit is not None else None,
        }
        if pop_detail is not None:
            entry.update(pop_detail.to_audit_dict())
        else:
            entry["pop_pct"] = None
            entry["pop_basis"] = None
        self.evaluations.append(entry)

    def record(self, reason: str, **detail: object) -> None:
        self.combos_tried += 1
        self.counts[reason] = self.counts.get(reason, 0) + 1
        if len(self.samples) < 25:
            self.samples.append({"reason": reason, **detail})

    def skip_message(self) -> str:
        if not self.counts:
            return "No iron condor candidates could be evaluated on the liquid chain."
        total = sum(self.counts.values())
        parts = ", ".join(
            f"{count} {reason}" for reason, count in sorted(self.counts.items(), key=lambda x: -x[1])
        )
        top_reason = max(self.counts.items(), key=lambda x: x[1])[0]
        if top_reason == "pop_floor":
            return (
                f"No iron condor meets minimum PoP within risk limits "
                f"({total} rejected: {parts})."
            )
        return f"No iron condor passed filters ({total} rejected: {parts})."


def passes_ic_wing_credit(
    put_credit: float,
    call_credit: float,
    wing_width: int,
    *,
    min_credit_pct_of_width: float = MIN_IC_CREDIT_PCT_OF_WIDTH,
) -> bool:
    """Per-spread credit floor plus soft total-credit vs wing width."""
    if put_credit < MIN_WING_CREDIT or call_credit < MIN_WING_CREDIT:
        return False
    total = put_credit + call_credit
    if total <= 0:
        return False
    return total >= min_credit_pct_of_width * wing_width


@dataclass(frozen=True)
class IronCondorCandidate:
    short_put: int
    short_call: int
    long_put: int
    long_call: int
    credit: float
    put_credit: float
    call_credit: float
    max_loss_u: float
    qty: int
    pop: float
    wing_width: int
    legs: list[TradeLeg]
    proxy_score: float
    net_collected: float
    final_score: float = 0.0
    score_factors: dict[str, float] = field(default_factory=dict)


def enumerate_symmetric_iron_condors(
    ctx: EngineContext,
    short_put: int,
    short_call: int,
    *,
    stats: IronCondorRejectionStats | None = None,
    min_credit_pct_of_width: float = MIN_IC_CREDIT_PCT_OF_WIDTH,
) -> list[IronCondorCandidate]:
    """All feasible symmetric-wing iron condors for a short put/call pair."""
    L = ctx.lot_size
    liquid_pe = set(ctx.liquid_pe_strikes)
    liquid_ce = set(ctx.liquid_ce_strikes)
    out: list[IronCondorCandidate] = []
    wing_widths = [mult * ctx.strike_step for mult in WING_WIDTH_MULTIPLIERS]
    if stats is not None:
        stats.record_pair_wing_plan(short_put, short_call, wing_widths)

    def _reject(
        reason: str,
        *,
        wing_width: int,
        long_put: int | None = None,
        long_call: int | None = None,
        pop_detail: PopDetail | None = None,
        credit: float | None = None,
        put_credit: float | None = None,
        call_credit: float | None = None,
        **detail: object,
    ) -> None:
        if stats is not None:
            stats.record(reason, short_put=short_put, short_call=short_call, wing_width=wing_width, **detail)
            stats.record_evaluation(
                short_put=short_put,
                short_call=short_call,
                wing_width=wing_width,
                long_put=long_put,
                long_call=long_call,
                outcome="rejected",
                reject_reason=reason,
                pop_detail=pop_detail,
                credit=credit,
                put_credit=put_credit,
                call_credit=call_credit,
            )

    def _accept(
        *,
        wing_width: int,
        long_put: int,
        long_call: int,
        pop_detail: PopDetail,
        credit: float,
        put_credit: float,
        call_credit: float,
    ) -> None:
        if stats is not None:
            stats.record_evaluation(
                short_put=short_put,
                short_call=short_call,
                wing_width=wing_width,
                long_put=long_put,
                long_call=long_call,
                outcome="accepted",
                reject_reason=None,
                pop_detail=pop_detail,
                credit=credit,
                put_credit=put_credit,
                call_credit=call_credit,
            )

    for mult in WING_WIDTH_MULTIPLIERS:
        w = mult * ctx.strike_step
        lp = short_put - w
        lc = short_call + w
        if lp not in liquid_pe or lc not in liquid_ce:
            _reject("illiquid_wing", wing_width=w, long_put=lp, long_call=lc)
            continue
        sp = ctx.cache.get((short_put, "Put"))
        sc = ctx.cache.get((short_call, "Call"))
        lpq = ctx.cache.get((lp, "Put"))
        lcq = ctx.cache.get((lc, "Call"))
        if not sp or not sc or not lpq or not lcq:
            _reject("missing_quote", wing_width=w, long_put=lp, long_call=lc)
            continue
        sp_prem = sp.best_bid_price or sp.ltp
        sc_prem = sc.best_bid_price or sc.ltp
        lp_prem = lpq.best_offer_price or lpq.ltp
        lc_prem = lcq.best_offer_price or lcq.ltp
        put_credit = sp_prem - lp_prem
        call_credit = sc_prem - lc_prem
        if put_credit <= 0:
            _reject(
                "debit_put_wing",
                wing_width=w,
                long_put=lp,
                long_call=lc,
                put_credit=put_credit,
                call_credit=call_credit,
            )
            continue
        if call_credit <= 0:
            _reject(
                "debit_call_wing",
                wing_width=w,
                long_put=lp,
                long_call=lc,
                put_credit=put_credit,
                call_credit=call_credit,
            )
            continue
        credit = put_credit + call_credit
        if not passes_ic_wing_credit(
            put_credit, call_credit, w, min_credit_pct_of_width=min_credit_pct_of_width
        ):
            _reject(
                "min_credit",
                wing_width=w,
                long_put=lp,
                long_call=lc,
                credit=credit,
                put_credit=put_credit,
                call_credit=call_credit,
                required=round(min_credit_pct_of_width * w, 4),
            )
            continue
        max_loss_u = w - credit
        if not passes_economic_prune(
            net_credit=credit,
            max_loss_per_unit=max_loss_u,
            max_loss_total=max_loss_u * L,
            max_loss_budget=ctx.max_loss_rupees,
        ):
            _reject(
                "max_loss_budget",
                wing_width=w,
                long_put=lp,
                long_call=lc,
                credit=credit,
                put_credit=put_credit,
                call_credit=call_credit,
                max_loss_per_lot=round(max_loss_u * L, 2),
                budget=ctx.max_loss_rupees,
            )
            continue
        qty = min_qty_for_one_lot(L)
        if qty < L:
            _reject("quantity", wing_width=w, long_put=lp, long_call=lc)
            continue
        legs = [
            TradeLeg("Put", "Sell", short_put, qty, sp_prem),
            TradeLeg("Put", "Buy", lp, qty, lp_prem),
            TradeLeg("Call", "Sell", short_call, qty, sc_prem),
            TradeLeg("Call", "Buy", lc, qty, lc_prem),
        ]
        pop_detail = pop_detail_for_legs(ctx, legs)
        pop = pop_detail.pop_pct
        if not meets_pop_floor(ctx, pop):
            _reject(
                "pop_floor",
                wing_width=w,
                long_put=lp,
                long_call=lc,
                pop_detail=pop_detail,
                credit=credit,
                put_credit=put_credit,
                call_credit=call_credit,
                floor=ctx.min_pop_pct,
            )
            continue
        _accept(
            wing_width=w,
            long_put=lp,
            long_call=lc,
            pop_detail=pop_detail,
            credit=credit,
            put_credit=put_credit,
            call_credit=call_credit,
        )
        net_collected = credit * qty
        leg_quotes: list[QuoteRow] = [sp, lpq, sc, lcq]
        max_loss_total = max_loss_u * qty
        final_score, score_factors = score_iron_condor_ror(
            pop, net_collected, max_loss_total, ctx.min_pop_pct, leg_quotes
        )
        proxy_score = net_collected / max(max_loss_total, 1.0)
        out.append(
            IronCondorCandidate(
                short_put=short_put,
                short_call=short_call,
                long_put=lp,
                long_call=lc,
                credit=credit,
                put_credit=put_credit,
                call_credit=call_credit,
                max_loss_u=max_loss_u,
                qty=qty,
                pop=pop,
                wing_width=w,
                legs=legs,
                proxy_score=proxy_score,
                net_collected=net_collected,
                final_score=final_score,
                score_factors=score_factors,
            )
        )

    return out


def evaluate_symmetric_iron_condor(
    ctx: EngineContext,
    short_put: int,
    short_call: int,
    *,
    strategy_id: str | None = None,
) -> IronCondorCandidate | None:
    """Best symmetric-wing iron condor for a short put/call pair, or None."""
    del strategy_id
    candidates = enumerate_symmetric_iron_condors(ctx, short_put, short_call)
    if not candidates:
        return None
    return max(candidates, key=lambda c: (c.final_score, c.net_collected, c.pop))


def iron_wings_symmetric(
    ctx: EngineContext,
    short_put: int,
    short_call: int,
    *,
    strategy_id: str | None = None,
) -> tuple[int, int, float, float, int, float] | None:
    """Used by iron butterfly — explicit sibling dependency on IC wing enumeration."""
    cand = evaluate_symmetric_iron_condor(
        ctx, short_put, short_call, strategy_id=strategy_id
    )
    if not cand:
        return None
    return (
        cand.long_put,
        cand.long_call,
        cand.credit,
        cand.max_loss_u,
        cand.qty,
        cand.pop,
    )


def prefetch_iron_condor_strikes(ctx: EngineContext) -> set[tuple[int, Right]]:
    """Strike/right pairs needed for iron condor (and iron butterfly wings)."""
    from icici_breeze_backend.app.services.options_strategy_engine.strategies.common import (
        prefetch_atm_pairs,
    )

    pairs = prefetch_atm_pairs(ctx)
    target = pop_to_short_delta(ctx.min_pop_pct, 2)
    short_puts = _ic_short_strikes_around_target(
        [s for s in ctx.liquid_pe_strikes if s < ctx.spot],
        ctx.cache,
        "Put",
        target,
    )
    short_calls = _ic_short_strikes_around_target(
        [s for s in ctx.liquid_ce_strikes if s > ctx.spot],
        ctx.cache,
        "Call",
        target,
    )
    for s in short_puts:
        pairs.add((s, "Put"))
        for mult in WING_WIDTH_MULTIPLIERS:
            pairs.add((s - mult * ctx.strike_step, "Put"))
    for s in short_calls:
        pairs.add((s, "Call"))
        for mult in WING_WIDTH_MULTIPLIERS:
            pairs.add((s + mult * ctx.strike_step, "Call"))
    return pairs


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
            "phase": "ic_candidate_span",
        },
    )
    span = parse_float((res.get("Success") or {}).get("span_margin_required"))
    ctx.unit_span_by_structure[struct_key] = span
    return span


def _best_strangle_short_pair(ctx: EngineContext) -> tuple[int, int] | None:
    """Short put/call pair that would pass short-strangle PoP, for IC seeding."""
    target = pop_to_short_delta(ctx.min_pop_pct, 2)
    L = ctx.lot_size
    ce_strikes = strikes_ranked_by_delta(
        all_liquid(ctx, "Call"),
        ctx.cache,
        "Call",
        target,
        strike_filter=lambda s: s > ctx.atm_strike,
    )
    pe_strikes = strikes_ranked_by_delta(
        all_liquid(ctx, "Put"),
        ctx.cache,
        "Put",
        target,
        strike_filter=lambda s: s < ctx.atm_strike,
    )
    best: tuple[float, int, int] | None = None

    for stp_c in ce_strikes:
        ce = ctx.cache.get((stp_c, "Call"))
        if not ce or not ce.liquid:
            continue
        for stp_p in pe_strikes:
            if stp_p >= stp_c:
                continue
            pe = ctx.cache.get((stp_p, "Put"))
            if not pe or not pe.liquid:
                continue
            prem_c = ce.best_bid_price or ce.ltp
            prem_p = pe.best_bid_price or pe.ltp
            qty = min_qty_for_one_lot(L)
            if qty < L:
                continue
            legs = [
                TradeLeg("Call", "Sell", stp_c, qty, prem_c),
                TradeLeg("Put", "Sell", stp_p, qty, prem_p),
            ]
            pop = pop_for_legs(ctx, legs)
            if pop < ctx.min_pop_pct:
                continue
            max_profit = (prem_c + prem_p) * qty
            score = score_credit_trade(pop, max_profit, float("inf"))
            if best is None or score > best[0]:
                best = (score, stp_p, stp_c)

    if best is None:
        return None
    return best[1], best[2]


def _merge_pairs(
    pairs: list[tuple[int, int]], seed: tuple[int, int] | None
) -> list[tuple[int, int]]:
    if seed is None:
        return pairs
    merged = [seed]
    for pair in pairs:
        if pair not in merged:
            merged.append(pair)
    return merged


def _collect_at_credit_threshold(
    ctx: EngineContext,
    pairs: list[tuple[int, int]],
    min_credit_pct_of_width: float,
    *,
    stats: IronCondorRejectionStats | None = None,
) -> list[IronCondorCandidate]:
    candidates: list[IronCondorCandidate] = []
    for sp, sc in pairs:
        candidates.extend(
            enumerate_symmetric_iron_condors(
                ctx,
                sp,
                sc,
                stats=stats,
                min_credit_pct_of_width=min_credit_pct_of_width,
            )
        )

    if candidates:
        return candidates

    target = pop_to_short_delta(ctx.min_pop_pct, 2)
    stp_sp = best_strike_near_delta(
        all_liquid(ctx, "Put"), ctx.cache, "Put", target, strike_filter=lambda s: s < ctx.spot
    )
    stp_sc = best_strike_near_delta(
        all_liquid(ctx, "Call"), ctx.cache, "Call", target, strike_filter=lambda s: s > ctx.spot
    )
    if stp_sp is None or stp_sc is None:
        return []
    return enumerate_symmetric_iron_condors(
        ctx,
        stp_sp,
        stp_sc,
        stats=stats,
        min_credit_pct_of_width=min_credit_pct_of_width,
    )


def _collect_candidates(
    ctx: EngineContext,
    pairs: list[tuple[int, int]],
    *,
    stats: IronCondorRejectionStats | None = None,
) -> tuple[list[IronCondorCandidate], float]:
    last_pct = IC_CREDIT_PCT_RELAXATION_SCHEDULE[-1]
    for pct in IC_CREDIT_PCT_RELAXATION_SCHEDULE:
        candidates = _collect_at_credit_threshold(ctx, pairs, pct, stats=None)
        if candidates:
            if stats is not None:
                _collect_at_credit_threshold(ctx, pairs, pct, stats=stats)
            return candidates, pct

    if stats is not None:
        _collect_at_credit_threshold(ctx, pairs, last_pct, stats=stats)
    return [], last_pct


def _ic_credit_search_rationale(min_credit_pct_used: float) -> str:
    if min_credit_pct_used < MIN_IC_CREDIT_PCT_OF_WIDTH:
        pct_display = min_credit_pct_used * 100
        return (
            f"Credit pct relaxed to {pct_display:g}% to find survivors; "
            "bidirectional short strikes × all wing widths; PoP hard floor; "
            "credit/annualized-return ranking."
        )
    return (
        "Bidirectional short strikes × all wing widths; PoP hard floor; "
        "credit/annualized-return ranking."
    )


def _pick_top_candidates(
    ctx: EngineContext,
    candidates: list[IronCondorCandidate],
    *,
    strategy_id: str,
    top_n: int = IC_RETURN_TOP_N,
) -> tuple[list[tuple[IronCondorCandidate, float]], list[dict]]:
    """Proxy-shortlist finalists, margin only those, then rank by SPAN-informed return."""
    dte = days_to_expiry(ctx.expiry_display)
    shortlist = sorted(
        candidates, key=lambda c: (c.final_score, c.net_collected, c.pop), reverse=True
    )[:top_n]

    span_scores: list[dict] = []
    scored: list[tuple[IronCondorCandidate, float]] = []

    for cand in shortlist:
        unit_span = _unit_span_margin(ctx, cand.legs, strategy_id=strategy_id)
        ann_return = score_iron_condor_candidate(
            cand.pop, cand.net_collected, cand.max_loss_u * cand.qty, unit_span, dte
        )
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
        scored.append((cand, ann_return))

    def rank_key(item: tuple[IronCondorCandidate, float]) -> tuple[float, float, float]:
        cand, ann = item
        return (ann, cand.net_collected, cand.pop)

    winners = sorted(scored, key=rank_key, reverse=True)
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

    stats = IronCondorRejectionStats() if ctx.audit else None
    pairs = iron_condor_short_pairs(ctx)
    seed_pair = _best_strangle_short_pair(ctx)
    pairs = _merge_pairs(pairs, seed_pair)
    candidates, min_credit_pct_used = _collect_candidates(ctx, pairs, stats=stats)

    if not candidates:
        skip_reason = stats.skip_message() if stats else (
            "No iron condor meets minimum PoP within risk limits."
        )
        if ctx.audit and stats is not None:
            ctx.audit.record_calculation(
                "Iron condor candidate search",
                {
                    "pairs_evaluated": len(pairs),
                    "survivors": 0,
                    "strangle_seed": list(seed_pair) if seed_pair else None,
                    "min_credit_pct_used": min_credit_pct_used,
                    "credit_thresholds_attempted": list(IC_CREDIT_PCT_RELAXATION_SCHEDULE),
                },
                {
                    "rejection_counts": stats.counts,
                    "combos_tried": stats.combos_tried,
                    "samples": stats.samples[:15],
                    "pair_wing_plans": stats.pair_wing_plans,
                    "candidates_evaluated": stats.evaluations,
                },
                rationale="No symmetric iron condor passed credit, risk, or PoP filters.",
            )
        return [skip(sid, name, skip_reason)]

    if ctx.audit:
        audit_outputs: dict = {
            "top_scores": [
                {
                    "short_put": c.short_put,
                    "short_call": c.short_call,
                    "wing_width": c.wing_width,
                    "credit": c.credit,
                    "pop": round(c.pop, 2),
                    "final_score": round(c.final_score, 4),
                }
                for c in sorted(candidates, key=lambda x: x.final_score, reverse=True)[:5]
            ],
        }
        if stats is not None:
            audit_outputs["rejection_counts"] = stats.counts
            audit_outputs["combos_tried"] = stats.combos_tried
            audit_outputs["rejection_samples"] = stats.samples[:10]
            audit_outputs["pair_wing_plans"] = stats.pair_wing_plans
            audit_outputs["candidates_evaluated"] = stats.evaluations
        ctx.audit.record_calculation(
            "Iron condor candidate search",
            {
                "pairs_evaluated": len(pairs),
                "survivors": len(candidates),
                "strangle_seed": list(seed_pair) if seed_pair else None,
                "min_credit_pct_used": min_credit_pct_used,
            },
            audit_outputs,
            rationale=_ic_credit_search_rationale(min_credit_pct_used),
        )

    winners, span_scores = _pick_top_candidates(ctx, candidates, strategy_id=sid)
    if not winners:
        return [skip(sid, name, "Could not resolve iron condor finalists.")]

    if ctx.audit:
        ctx.audit.record_calculation(
            "Iron condor SPAN refinement",
            {"finalists": len(span_scores)},
            {"scores": span_scores},
            rationale=(
                "Credit-proxy shortlist; finalists ranked by SPAN annualized return, "
                "then net credit; PoP tiebreak only."
            ),
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
                higher_rank=1,
                lower_rank=2,
                viewing_rank=1,
                higher_credit=cand.net_collected,
                higher_pop=cand.pop,
                higher_ror=cand.score_factors.get("ror", 0.0),
                lower_credit=runner.net_collected,
                lower_pop=runner.pop,
                lower_ror=runner.score_factors.get("ror", 0.0),
            )
        elif rank > 1:
            prev = winners[rank - 2][0]
            summary = build_ranking_summary(
                higher_rank=rank - 1,
                lower_rank=rank,
                viewing_rank=rank,
                higher_credit=prev.net_collected,
                higher_pop=prev.pop,
                higher_ror=prev.score_factors.get("ror", 0.0),
                lower_credit=cand.net_collected,
                lower_pop=cand.pop,
                lower_ror=cand.score_factors.get("ror", 0.0),
            )
        results.append(
            _candidate_to_result(
                ctx, cand, rank=rank, ann_return=ann_return, ranking_summary=summary
            )
        )

    return results
