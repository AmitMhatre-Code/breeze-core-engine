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
from icici_breeze_backend.app.services.options_strategy_engine.margin_async_fetch import (
    MarginFetchRequest,
    fetch_margins_concurrent,
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
from icici_breeze_backend.app.services.options_strategy_engine.strategies.common import all_liquid, make_result
from icici_breeze_backend.app.services.options_strategy_engine.strategies.income._common import (
    IncomeSearchState,
    adaptive_short_strikes,
    iter_pop_band_expansions,
    passes_capital_gate,
    pop_band,
    pop_for_short_strike,
    record_feasible,
    run_income_champion_pipeline,
    setup_income_collector,
)
from icici_breeze_backend.app.services.options_strategy_engine.types import (
    EngineContext,
    QuoteRow,
    Right,
    StrategyResult,
    TradeLeg,
)

# --- Iron condor tuning constants (owned by this module) ---

WING_WIDTH_MULTIPLIERS: tuple[int, ...] = (1, 2, 3, 4)
IC_SHORT_STRIKES_INWARD = 7
IC_SHORT_STRIKES_OUTWARD = 3
IC_TOP_K_SHORT_STRIKES = IC_SHORT_STRIKES_INWARD + IC_SHORT_STRIKES_OUTWARD
IC_POP_BAND_WIDTH_PCT = 2.0
IC_SHORT_STRIKES_MAX_ATM = 12
IC_SHORT_STRIKES_MAX_PER_WING = 12
MIN_WING_CREDIT = 0.05
MIN_IC_CREDIT_PCT_OF_WIDTH = 0.03
# Deprecated for collection; kept for backward-compatible tests/docs.
IC_CREDIT_PCT_RELAXATION_SCHEDULE: tuple[float, ...] = (0.03, 0.025, 0.02, 0.015, 0.01)
MIN_IC_ANNUALIZED_RETURN_PCT = 5.0
IC_RETURN_TOP_N = 3

IronCondorRejectionStats = StrategyAuditCollector

_STAGES_PASSED = (
    "passed_liquidity",
    "passed_credit",
    "passed_economic_prune",
    "passed_pop",
    "margin_refined",
    "returned",
)


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
    factors = {
        "ror": round(ror, 6),
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


def _ic_pop_band_delta_bounds(min_pop_pct: float) -> tuple[float, float]:
    """Per-wing abs-delta range for [min_pop, min_pop + band] on symmetric shorts."""
    floor_delta = pop_to_short_delta(min_pop_pct, short_legs=2)
    ceil_delta = pop_to_short_delta(min_pop_pct + IC_POP_BAND_WIDTH_PCT, short_legs=2)
    return ceil_delta, floor_delta


def _ic_pop_bucket(pop_pct: float, floor_pct: float) -> str:
    return pop_bucket_label(pop_pct, floor_pct, band_width=IC_POP_BAND_WIDTH_PCT)


def _audit_collector(ctx: EngineContext) -> StrategyAuditCollector | None:
    if ctx.audit_collector is not None:
        c = ctx.audit_collector
        c.min_pop_pct = ctx.min_pop_pct
        c.pop_band_width = IC_POP_BAND_WIDTH_PCT
        return c
    return None


def _record_ic_pair_wing_plan(
    stats: IronCondorRejectionStats | None,
    short_put: int,
    short_call: int,
    wing_widths: list[int],
) -> None:
    if stats is None:
        return
    if not hasattr(stats, "pair_wing_plans"):
        stats.pair_wing_plans = []  # type: ignore[attr-defined]
    stats.pair_wing_plans.append(  # type: ignore[attr-defined]
        {
            "short_put": short_put,
            "short_call": short_call,
            "wing_widths_attempted": wing_widths,
        }
    )


def _pop_short_strangle_pair(
    ctx: EngineContext,
    short_put: int,
    short_call: int,
) -> float:
    """Breakeven PoP for a short put + short call pair (no wings)."""
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
    hi = min_pop_pct + IC_POP_BAND_WIDTH_PCT
    mid = lo + IC_POP_BAND_WIDTH_PCT / 2.0
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


def _ic_short_strikes_for_pop_band(
    ctx: EngineContext,
    strikes: list[int],
    right: Right,
    min_pop_pct: float,
    *,
    opposite_strikes: list[int] | None = None,
) -> list[int]:
    """PoP-band shortlist: OTM band strikes plus ATM-ward expansion toward floor credit."""
    cache = ctx.cache
    ceil_delta, floor_delta = _ic_pop_band_delta_bounds(min_pop_pct)
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
    for _, s in atm_candidates[:IC_SHORT_STRIKES_MAX_ATM]:
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
            if len(selected) >= IC_SHORT_STRIKES_MAX_ATM + IC_TOP_K_SHORT_STRIKES:
                break

    if not selected:
        for _, s in sorted(scored, key=lambda x: abs(x[0] - floor_delta))[:IC_TOP_K_SHORT_STRIKES]:
            if s not in seen:
                selected.append(s)
    return selected[:IC_SHORT_STRIKES_MAX_PER_WING]


def _ic_short_strikes_around_target(
    strikes: list[int],
    cache: dict[tuple[int, Right], QuoteRow],
    right: Right,
    target: float,
    *,
    inward: int = IC_SHORT_STRIKES_INWARD,
    outward: int = IC_SHORT_STRIKES_OUTWARD,
) -> list[int]:
    """Legacy delta-count shortlist; prefer _ic_short_strikes_for_pop_band for IC search."""
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


def iron_condor_short_pairs(
    ctx: EngineContext,
    *,
    search_state: IncomeSearchState | None = None,
) -> list[tuple[int, int]]:
    """PoP-aware short put/call pairs with adaptive band expansion."""
    pe_strikes = [s for s in ctx.liquid_pe_strikes if s < ctx.spot]
    ce_strikes = [s for s in ctx.liquid_ce_strikes if s > ctx.spot]
    initial = pop_band(ctx.min_pop_pct)
    if search_state is not None:
        search_state.initial_pop_band = initial

    pair_set: set[tuple[int, int]] = set()
    for expansion, (floor_pop, ceiling_pop) in enumerate(iter_pop_band_expansions(ctx.min_pop_pct)):
        del floor_pop
        puts = adaptive_short_strikes(
            ctx, pe_strikes, "Put", spot_filter=lambda s: s < ctx.spot
        )
        calls = adaptive_short_strikes(
            ctx, ce_strikes, "Call", spot_filter=lambda s: s > ctx.spot
        )
        if not puts:
            puts = [
                s for s in pe_strikes
                if ctx.min_pop_pct <= pop_for_short_strike(ctx, s, "Put") <= ceiling_pop
            ]
        if not calls:
            calls = [
                s for s in ce_strikes
                if ctx.min_pop_pct <= pop_for_short_strike(ctx, s, "Call") <= ceiling_pop
            ]
        for sp in puts:
            for sc in calls:
                if sp < sc:
                    pair_set.add((sp, sc))
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


def passes_hard_wing_credit(put_credit: float, call_credit: float) -> bool:
    """Hard per-spread credit floor (structural validity)."""
    if put_credit < MIN_WING_CREDIT or call_credit < MIN_WING_CREDIT:
        return False
    return put_credit + call_credit > 0


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
    below_preferred_credit: bool = False


def _ic_rank_key(
    cand: IronCondorCandidate,
    *,
    ann_return: float = 0.0,
) -> tuple[float, float, float, float]:
    """Net credit, ann return, liquidity×spread, PoP (all DESC)."""
    liq = cand.score_factors.get("liquidity_weight", 0.5)
    spread = cand.score_factors.get("spread_weight", 0.5)
    return (cand.net_collected, ann_return, liq * spread, cand.pop)


def _proxy_ann_return(cand: IronCondorCandidate, dte: int | None) -> float:
    return score_iron_condor_candidate(
        cand.pop,
        cand.net_collected,
        cand.max_loss_u * cand.qty,
        None,
        dte,
    )


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
        stats.min_pop_pct = ctx.min_pop_pct
        _record_ic_pair_wing_plan(stats, short_put, short_call, wing_widths)

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
        below_preferred_credit: bool,
        preferred_credit_met: bool,
    ) -> None:
        if stats is not None:
            stats.record_stage("passed_pop")
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
                below_preferred_credit=below_preferred_credit,
                preferred_credit_met=preferred_credit_met,
            )
            stats.record_survivor_metrics(pop_pct=pop_detail.pop_pct, credit=credit)

    for mult in WING_WIDTH_MULTIPLIERS:
        if stats is not None:
            stats.record_generated()
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
        if stats is not None:
            stats.record_stage("passed_liquidity")
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
        if stats is not None:
            stats.record_stage("passed_credit")
        credit = put_credit + call_credit
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
        if stats is not None:
            stats.record_stage("passed_economic_prune")
            stats.record_stage("passed_loss")
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
        if not passes_hard_wing_credit(put_credit, call_credit):
            _reject(
                "min_wing_credit",
                wing_width=w,
                long_put=lp,
                long_call=lc,
                pop_detail=pop_detail,
                credit=credit,
                put_credit=put_credit,
                call_credit=call_credit,
            )
            continue
        if not passes_capital_gate(
            ctx,
            strategy_id="iron_condor",
            legs=legs,
            unit_max_loss=max_loss_u,
            margin_estimate=max_loss_u * L,
        ):
            _reject(
                "capital",
                wing_width=w,
                long_put=lp,
                long_call=lc,
                credit=credit,
            )
            continue
        preferred_credit_met = passes_ic_wing_credit(
            put_credit, call_credit, w, min_credit_pct_of_width=min_credit_pct_of_width
        )
        below_preferred_credit = not preferred_credit_met
        if stats is not None:
            stats.record_evaluation(
                short_put=short_put,
                short_call=short_call,
                wing_width=w,
                long_put=lp,
                long_call=lc,
                outcome="accepted",
                reject_reason=None,
                pop_detail=pop_detail,
                credit=credit,
                put_credit=put_credit,
                call_credit=call_credit,
                below_preferred_credit=below_preferred_credit,
                preferred_credit_met=preferred_credit_met,
            )
        record_feasible(stats, pop_detail=pop_detail, credit=credit, passed_capital=True)
        net_collected = credit * qty
        leg_quotes: list[QuoteRow] = [sp, lpq, sc, lcq]
        max_loss_total = max_loss_u * qty
        final_score, score_factors = score_iron_condor_ror(
            pop, net_collected, max_loss_total, ctx.min_pop_pct, leg_quotes
        )
        score_factors["preferred_credit_met"] = float(preferred_credit_met)
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
                below_preferred_credit=below_preferred_credit,
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
    dte = days_to_expiry(ctx.expiry_display)
    return max(
        candidates,
        key=lambda c: (c.net_collected, _proxy_ann_return(c, dte)),
    )


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
    pe_strikes = [s for s in ctx.liquid_pe_strikes if s < ctx.spot]
    ce_strikes = [s for s in ctx.liquid_ce_strikes if s > ctx.spot]
    short_puts = _ic_short_strikes_for_pop_band(ctx, pe_strikes, "Put", ctx.min_pop_pct)
    short_calls = _ic_short_strikes_for_pop_band(ctx, ce_strikes, "Call", ctx.min_pop_pct)
    for _ in range(3):
        new_puts = _ic_short_strikes_for_pop_band(
            ctx,
            pe_strikes,
            "Put",
            ctx.min_pop_pct,
            opposite_strikes=short_calls,
        )
        new_calls = _ic_short_strikes_for_pop_band(
            ctx,
            ce_strikes,
            "Call",
            ctx.min_pop_pct,
            opposite_strikes=new_puts,
        )
        if new_puts == short_puts and new_calls == short_calls:
            break
        short_puts, short_calls = new_puts, new_calls
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
    candidates = _collect_at_credit_threshold(
        ctx,
        pairs,
        MIN_IC_CREDIT_PCT_OF_WIDTH,
        stats=stats,
    )
    return candidates, MIN_IC_CREDIT_PCT_OF_WIDTH


def _build_pop_audit_summary(
    stats: IronCondorRejectionStats,
    candidates: list[IronCondorCandidate],
    min_pop_pct: float,
) -> dict[str, object]:
    below_total = sum(1 for c in candidates if c.below_preferred_credit)
    survivors_by_bucket: dict[str, int] = {}
    below_preferred_by_bucket: dict[str, int] = {}
    for cand in candidates:
        bucket = _ic_pop_bucket(cand.pop, min_pop_pct)
        survivors_by_bucket[bucket] = survivors_by_bucket.get(bucket, 0) + 1
        if cand.below_preferred_credit:
            below_preferred_by_bucket[bucket] = (
                below_preferred_by_bucket.get(bucket, 0) + 1
            )
    return {
        "pop_distribution": dict(sorted(stats.pop_bucket_counts.items())),
        "survivors_by_pop_bucket": dict(sorted(survivors_by_bucket.items())),
        "below_preferred_credit_by_pop_bucket": dict(
            sorted(below_preferred_by_bucket.items())
        ),
        "min_credit_rejections_by_pop_bucket": dict(
            sorted(
                {
                    bucket: funnel.get("min_credit", 0)
                    for bucket, funnel in stats.rejection_funnel_by_pop_bucket.items()
                    if funnel.get("min_credit", 0) > 0
                }.items()
            )
        ),
        "below_preferred_credit_total": below_total,
        "pop_band_target": [min_pop_pct, min_pop_pct + IC_POP_BAND_WIDTH_PCT],
    }


def _ic_credit_search_rationale(min_credit_pct_used: float) -> str:
    del min_credit_pct_used
    return (
        "PoP-band strike shortlist; PoP hard floor; soft preferred credit annotation; "
        "credit/annualized-return ranking."
    )


async def _pick_top_candidates(
    ctx: EngineContext,
    candidates: list[IronCondorCandidate],
    *,
    strategy_id: str,
    stats: StrategyAuditCollector | None = None,
    top_n: int = IC_RETURN_TOP_N,
) -> tuple[list[tuple[IronCondorCandidate, float]], list[dict]]:
    """Credit-first shortlist, margin only those, then rank by SPAN-informed return."""
    if stats is not None:
        stats.begin_ranking()
    dte = days_to_expiry(ctx.expiry_display)
    shortlist = sorted(
        candidates,
        key=lambda c: _ic_rank_key(c, ann_return=_proxy_ann_return(c, dte)),
        reverse=True,
    )[:top_n]

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
                phase="ic_candidate_span",
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
    scored: list[tuple[IronCondorCandidate, float]] = []

    for credit_rank, cand in enumerate(shortlist, start=1):
        unit_span = _unit_span_margin(ctx, cand.legs, strategy_id=strategy_id)
        if unit_span <= 0 and stats is not None:
            stats.record(
                "span_failure",
                short_put=cand.short_put,
                short_call=cand.short_call,
            )
        ann_return = score_iron_condor_candidate(
            cand.pop, cand.net_collected, cand.max_loss_u * cand.qty, unit_span, dte
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

    winners = sorted(
        scored,
        key=lambda item: _ic_rank_key(item[0], ann_return=item[1]),
        reverse=True,
    )

    winner_keys = {id(c) for c, _ in winners}
    if stats is not None:
        for final_rank, (cand, ann_return) in enumerate(winners, start=1):
            credit_rank = next(
                i for i, sc in enumerate(span_scores, start=1)
                if sc["short_put"] == cand.short_put
                and sc["short_call"] == cand.short_call
                and sc["wing_width"] == cand.wing_width
            )
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
                    "below_preferred_credit": cand.below_preferred_credit,
                },
                stages_passed=list(_STAGES_PASSED),
                ranks={"credit": credit_rank, "span": final_rank, "final": final_rank},
                soft_filter_violations=(
                    ["below_preferred_credit"] if cand.below_preferred_credit else None
                ),
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
                context="Credit shortlist finalist did not rank in top return set.",
            )
        stats.end_ranking(ctx.audit.telemetry if ctx.audit else None)

    return winners, span_scores


def _candidate_to_result(
    ctx: EngineContext,
    cand: IronCondorCandidate,
    rank: int,
    ann_return: float,
    badges: list[str],
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
        score_breakdown={
            **cand.score_factors,
            **(
                {"below_preferred_credit": 1.0}
                if cand.below_preferred_credit
                else {}
            ),
        },
        badges=badges,
    )
    result.annualized_return_pct = round(ann_return, 2)
    return result


async def calc_iron_condor(ctx: EngineContext) -> list[StrategyResult]:
    sid, name = "iron_condor", "Iron Condor"
    if ctx.halted:
        return [skip(sid, name, ctx.halt_reason or "Market halted")]

    stats = setup_income_collector(ctx)
    search_state = IncomeSearchState(initial_pop_band=pop_band(ctx.min_pop_pct), final_pop_band=0.0)
    pairs = iron_condor_short_pairs(ctx, search_state=search_state)
    seed_pair = _best_strangle_short_pair(ctx)
    pairs = _merge_pairs(pairs, seed_pair)
    candidates, min_credit_pct_used = _collect_candidates(ctx, pairs, stats=stats)
    if stats is not None:
        stats.end_generation(ctx.audit.telemetry if ctx.audit else None)

    if not candidates:
        skip_reason = stats.skip_message() if stats else (
            "No iron condor meets minimum PoP within risk limits."
        )
        return [skip(sid, name, skip_reason)]

    results = await run_income_champion_pipeline(
        ctx,
        candidates,
        strategy_id=sid,
        strategy_name=name,
        stats=stats,
        to_result=_candidate_to_result,
        span_phase="ic_candidate_span",
        search_state=search_state,
    )

    if not results:
        return [
            skip(
                sid,
                name,
                f"No iron condor meets minimum annualized return "
                f"{ctx.min_ann_return_pct:.1f}%.",
            )
        ]

    if ctx.audit:
        audit_calc(
            ctx,
            "Iron condor objective champions",
            {
                "pairs_evaluated": len(pairs),
                "feasible": len(candidates),
                "returned": len(results),
                "preferred_credit_pct": min_credit_pct_used,
            },
            {"search_state": search_state.__dict__},
            rationale=_ic_credit_search_rationale(min_credit_pct_used),
            strategy_id=sid,
        )

    return results
