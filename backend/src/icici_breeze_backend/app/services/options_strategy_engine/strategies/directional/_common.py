"""Shared conviction-based engine for directional strategy calculators."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Callable, Literal

from icici_breeze_backend.app.services.options_strategy_engine.delta_anchor import (
    CONVICTION_PROFILES,
    DELTA_TOLERANCE_SEQUENCE,
    MIN_LIQUIDITY_SCORE,
    MAX_CANDIDATES_PER_CONVICTION,
    RiskRewardProfile,
    profile_deltas,
    strikes_near_delta,
)
from icici_breeze_backend.app.services.options_strategy_engine.helpers import (
    format_indian_money_compact,
    skip,
)
from icici_breeze_backend.app.services.options_strategy_engine.pop import pop_for_legs
from icici_breeze_backend.app.services.options_strategy_engine.sizing import size_quantity_from_budgets
from icici_breeze_backend.app.services.options_strategy_engine.strategies.common import all_liquid, make_result
from icici_breeze_backend.app.services.options_strategy_engine.strategies.directional.scoring import (
    finalize_long_option_score,
    finalize_spread_score,
    normalize_min_max,
    score_long_option_components,
    score_spread_components,
)
from icici_breeze_backend.app.services.options_strategy_engine.types import (
    EngineContext,
    QuoteRow,
    Right,
    StrategyResult,
    TileMetric,
    TradeLeg,
)
from icici_breeze_backend.audit.strategy_evaluation_audit import (
    audit_collector_for,
    candidate_id_for_legs,
    record_directional_candidate_stage,
    record_directional_profile_winner,
    setup_directional_audit,
)

SpreadKind = Literal["bull_call", "bear_put"]
ProfileStatus = Literal["success", "skipped"]
_DEBIT_SPREAD_STRATEGY_IDS = frozenset({"bull_call_spread", "bear_put_spread"})
_LONG_OPTION_STRATEGY_IDS = frozenset({"long_call", "long_put"})
_SKIP_REASON_NO_CANDIDATES = "no_candidates_after_max_delta_tolerance"


@dataclass
class ProfileFunnelStats:
    generated: int = 0
    passed_liquidity: int = 0
    passed_valid_debit: int = 0
    passed_constraints: int = 0


@dataclass
class ProfileAuditRecord:
    conviction_profile: str
    initial_delta_tolerance: float
    final_delta_tolerance: float
    widening_attempts: int
    generated: int = 0
    passed_liquidity: int = 0
    passed_valid_debit: int = 0
    passed_constraints: int = 0
    returned: int = 0
    status: ProfileStatus = "skipped"
    skip_reason: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ConvictionCandidate:
    legs: list[TradeLeg]
    max_loss: float
    max_gain: float
    pop: float
    score: float
    score_breakdown: dict[str, float]
    conviction_profile: RiskRewardProfile
    long_target: float
    short_target: float | None = None
    abs_delta: float | None = None
    premium_paid: float = 0.0


@dataclass
class DirectionalAuditState:
    candidates_by_profile: dict[str, int] = field(default_factory=dict)
    profile_audits: list[dict] = field(default_factory=list)
    profile_winners: list[dict] = field(default_factory=list)
    shortlist_scores: list[dict] = field(default_factory=list)


def _format_rupees(amount: float) -> str:
    return format_indian_money_compact(amount)


def _conviction_kind_for_sid(sid: str) -> Literal["long_option", "spread"]:
    return "long_option" if sid in _LONG_OPTION_STRATEGY_IDS else "spread"


def _long_candidate_id(right: Right, strike: int) -> str:
    return candidate_id_for_legs([TradeLeg(right, "Buy", strike, 1, 0.0)])


def _spread_candidate_id(right: Right, long_strike: int, short_strike: int) -> str:
    return candidate_id_for_legs(
        [
            TradeLeg(right, "Buy", long_strike, 1, 0.0),
            TradeLeg(right, "Sell", short_strike, 1, 0.0),
        ]
    )


def _parse_max_gain_from_rr(risk_reward_ratio: str | None) -> float | None:
    if not risk_reward_ratio:
        return None
    parts = risk_reward_ratio.split(":")
    if len(parts) != 2:
        return None
    gain_str = parts[1].strip()
    if gain_str.lower() == "unlimited":
        return None
    try:
        return float(gain_str)
    except ValueError:
        return None


def _spread_max_gain_from_legs(legs: list[TradeLeg]) -> float | None:
    if len(legs) < 2:
        return None
    buy = next((leg for leg in legs if leg.side == "Buy"), None)
    sell = next((leg for leg in legs if leg.side == "Sell"), None)
    if buy is None or sell is None:
        return None
    width = abs(buy.strike - sell.strike)
    net_per = buy.premium_per_unit - sell.premium_per_unit
    return (width - net_per) * buy.quantity


def refresh_directional_tile_metrics(result: StrategyResult) -> None:
    """Rebuild hero/secondary tiles from post-resize legs, loss, and margin fields."""
    if result.conviction_profile is None or result.status != "ok":
        return

    is_spread = result.strategy_id in _DEBIT_SPREAD_STRATEGY_IDS or (
        len(result.legs) == 2 and any(leg.side == "Sell" for leg in result.legs)
    )
    pop = result.pop_pct or 0.0

    if is_spread:
        max_loss = result.max_loss or 0.0
        max_gain = _parse_max_gain_from_rr(result.risk_reward_ratio)
        if max_gain is None:
            max_gain = _spread_max_gain_from_legs(result.legs) or 0.0
        premium = (
            abs(result.net_premium)
            if result.net_premium is not None
            else max_loss
        )
        span = result.span_margin or 0.0
        elm = result.elm_requirement or 0.0
        capital_total = premium + span + elm
        result.hero_metric = TileMetric(
            label="Reward : Risk",
            value=f"1 : {max_gain / max(max_loss, 1):.2f}",
        )
        result.secondary_metrics = [
            TileMetric(label="Max Gain", value=_format_rupees(max_gain)),
            TileMetric(label="Max Loss", value=_format_rupees(max_loss)),
            TileMetric(label="Premium", value=_format_rupees(premium)),
            TileMetric(label="SPAN", value=_format_rupees(span) if span > 0 else "—"),
            TileMetric(label="ELM", value=_format_rupees(elm) if elm > 0 else "—"),
            TileMetric(label="Capital Required", value=_format_rupees(capital_total)),
            TileMetric(label="Est. PoP", value=f"{pop:.1f}%"),
        ]
        return

    max_loss = result.max_loss or 0.0
    premium = abs(result.net_premium) if result.net_premium is not None else max_loss
    delta_label = next(
        (m.value for m in result.secondary_metrics if m.label == "Move Sensitivity"),
        "—",
    )
    result.hero_metric = TileMetric(
        label="Capital at Risk",
        value=_format_rupees(max_loss),
    )
    result.secondary_metrics = [
        TileMetric(label="Move Sensitivity", value=delta_label),
        TileMetric(label="Premium Paid", value=_format_rupees(premium)),
        TileMetric(label="Est. PoP", value=f"{pop:.1f}%"),
    ]


def _liquidity_ok(q) -> bool:
    return q.liquid and q.liquidity_score >= MIN_LIQUIDITY_SCORE


def _promote_stage(
    collector,
    *,
    candidate_id: str,
    stage: str,
    profile: RiskRewardProfile,
    pop_pct: float | None = None,
    **fields,
) -> None:
    record_directional_candidate_stage(
        collector,
        candidate_id=candidate_id,
        stage=stage,
        conviction_profile=profile,
        pop_pct=pop_pct,
        **fields,
    )


def _record_reject(
    collector,
    *,
    candidate_id: str,
    profile: RiskRewardProfile,
    reject_reason: str,
    **fields,
) -> None:
    if collector is None:
        return
    payload = {"conviction_profile": profile, "candidate_id": candidate_id, **fields}
    collector.record(reject_reason, **payload)
    collector.record_evaluation(
        outcome="rejected",
        reject_reason=reject_reason,
        **payload,
    )


def _build_profile_audit(
    profile: RiskRewardProfile,
    *,
    initial_tol: float,
    final_tol: float,
    widening_attempts: int,
    funnel: ProfileFunnelStats,
    returned: int,
    status: ProfileStatus,
    skip_reason: str | None = None,
) -> ProfileAuditRecord:
    return ProfileAuditRecord(
        conviction_profile=profile,
        initial_delta_tolerance=initial_tol,
        final_delta_tolerance=final_tol,
        widening_attempts=widening_attempts,
        generated=funnel.generated,
        passed_liquidity=funnel.passed_liquidity,
        passed_valid_debit=funnel.passed_valid_debit,
        passed_constraints=funnel.passed_constraints,
        returned=returned,
        status=status,
        skip_reason=skip_reason,
    )


def evaluate_long_option(
    ctx: EngineContext,
    *,
    sid: str,
    right: Right,
    conviction_profile: RiskRewardProfile,
    collector,
    audit_state: DirectionalAuditState | None,
) -> tuple[ConvictionCandidate | None, ProfileAuditRecord]:
    long_target, _ = profile_deltas(conviction_profile, kind="long_option")
    L = ctx.lot_size
    liquid = all_liquid(ctx, right)
    initial_tol = DELTA_TOLERANCE_SEQUENCE[0]
    final_tol = initial_tol
    widening_attempts = 0
    last_funnel = ProfileFunnelStats()
    best: ConvictionCandidate | None = None

    for attempt_idx, tol in enumerate(DELTA_TOLERANCE_SEQUENCE):
        final_tol = tol
        widening_attempts = attempt_idx
        strike_pool = strikes_near_delta(
            liquid,
            ctx.cache,
            right,
            long_target,
            tolerance=tol,
        )[:MAX_CANDIDATES_PER_CONVICTION]

        funnel = ProfileFunnelStats()
        raw_candidates: list[tuple[dict, list[TradeLeg], float, float, float, float, QuoteRow]] = []
        for stp in strike_pool:
            cid = _long_candidate_id(right, stp)
            funnel.generated += 1
            _promote_stage(collector, candidate_id=cid, stage="generated", profile=conviction_profile, strike=stp)

            q = ctx.cache.get((stp, right))
            if q is None or not _liquidity_ok(q):
                _record_reject(
                    collector,
                    candidate_id=cid,
                    profile=conviction_profile,
                    reject_reason="liquidity",
                    strike=stp,
                )
                continue
            funnel.passed_liquidity += 1
            _promote_stage(collector, candidate_id=cid, stage="passed_liquidity", profile=conviction_profile, strike=stp)

            buy_prem = q.best_offer_price or q.ltp
            if buy_prem <= 0:
                _record_reject(
                    collector,
                    candidate_id=cid,
                    profile=conviction_profile,
                    reject_reason="no_credit",
                    strike=stp,
                )
                continue
            funnel.passed_valid_debit += 1
            _promote_stage(collector, candidate_id=cid, stage="passed_credit", profile=conviction_profile, strike=stp)

            debit_lot = buy_prem * L
            qty = size_quantity_from_budgets(
                sid,
                debit_lot,
                debit_lot,
                margin_rupees=ctx.margin_rupees,
                max_loss_rupees=ctx.effective_max_loss_budget(),
                lot_size=L,
                unit_short_lots=0,
                spot=ctx.spot,
                provision_elm=ctx.provision_elm,
            )
            if qty < L:
                _record_reject(
                    collector,
                    candidate_id=cid,
                    profile=conviction_profile,
                    reject_reason="quantity",
                    strike=stp,
                )
                continue

            max_loss = buy_prem * qty
            if ctx.max_loss_rupees is not None and max_loss > ctx.max_loss_rupees:
                _record_reject(
                    collector,
                    candidate_id=cid,
                    profile=conviction_profile,
                    reject_reason="budget",
                    strike=stp,
                    max_loss=max_loss,
                )
                continue

            funnel.passed_constraints += 1
            legs = [TradeLeg(right, "Buy", stp, qty, buy_prem)]
            _promote_stage(
                collector,
                candidate_id=cid,
                stage="passed_constraints",
                profile=conviction_profile,
                strike=stp,
            )
            pop = pop_for_legs(ctx, legs)
            components = score_long_option_components(
                ctx, q, target_delta=long_target, premium_per_unit=buy_prem
            )
            abs_delta = abs(q.delta) if q.delta is not None else 0.0
            raw_candidates.append((components, legs, max_loss, pop, buy_prem, abs_delta, q))

        last_funnel = funnel
        if raw_candidates:
            if audit_state is not None:
                audit_state.candidates_by_profile[conviction_profile] = len(raw_candidates)

            prem_norm = normalize_min_max(c["premium_efficiency_raw"] for c, *_ in raw_candidates)
            for idx, (components, legs, max_loss, pop, buy_prem, abs_delta, _q) in enumerate(raw_candidates):
                score, breakdown = finalize_long_option_score(
                    components, premium_efficiency_norm=prem_norm[idx]
                )
                if audit_state is not None:
                    audit_state.shortlist_scores.append(
                        {
                            "conviction_profile": conviction_profile,
                            "strike": legs[0].strike,
                            "score": score,
                            "score_breakdown": breakdown,
                            "pop_pct": round(pop, 2),
                            "delta_tolerance": tol,
                        }
                    )
                cand = ConvictionCandidate(
                    legs=legs,
                    max_loss=max_loss,
                    max_gain=float("inf"),
                    pop=pop,
                    score=score,
                    score_breakdown=breakdown,
                    conviction_profile=conviction_profile,
                    long_target=long_target,
                    abs_delta=abs_delta,
                    premium_paid=buy_prem * legs[0].quantity,
                )
                if best is None or cand.score > best.score:
                    best = cand
            break

    if best is None:
        audit = _build_profile_audit(
            conviction_profile,
            initial_tol=initial_tol,
            final_tol=final_tol,
            widening_attempts=widening_attempts,
            funnel=last_funnel,
            returned=0,
            status="skipped",
            skip_reason=_SKIP_REASON_NO_CANDIDATES,
        )
        return None, audit

    audit = _build_profile_audit(
        conviction_profile,
        initial_tol=initial_tol,
        final_tol=final_tol,
        widening_attempts=widening_attempts,
        funnel=last_funnel,
        returned=1,
        status="success",
    )
    return best, audit


def evaluate_vertical_spread(
    ctx: EngineContext,
    *,
    sid: str,
    right: Right,
    spread_kind: SpreadKind,
    conviction_profile: RiskRewardProfile,
    collector,
    audit_state: DirectionalAuditState | None,
) -> tuple[ConvictionCandidate | None, ProfileAuditRecord]:
    long_target, short_target = profile_deltas(conviction_profile, kind="spread")
    L = ctx.lot_size
    liquid = all_liquid(ctx, right)

    if spread_kind == "bull_call":
        long_filter: Callable[[int], bool] = lambda s: s <= ctx.atm_strike
        short_filter: Callable[[int], bool] = lambda s: s > ctx.atm_strike
        long_below_short = lambda lo, hi: lo < hi
    else:
        long_filter = lambda s: s >= ctx.atm_strike
        short_filter = lambda s: s < ctx.atm_strike
        long_below_short = lambda lo, hi: lo > hi

    initial_tol = DELTA_TOLERANCE_SEQUENCE[0]
    final_tol = initial_tol
    widening_attempts = 0
    last_funnel = ProfileFunnelStats()
    best: ConvictionCandidate | None = None

    for attempt_idx, tol in enumerate(DELTA_TOLERANCE_SEQUENCE):
        final_tol = tol
        widening_attempts = attempt_idx
        long_strikes = strikes_near_delta(
            liquid, ctx.cache, right, long_target, tolerance=tol
        )
        short_strikes = strikes_near_delta(
            liquid, ctx.cache, right, short_target, tolerance=tol
        )

        funnel = ProfileFunnelStats()
        raw_candidates: list[
            tuple[dict, list[TradeLeg], float, float, float, float, int, int]
        ] = []
        combos = 0
        for stp_l in long_strikes:
            if not long_filter(stp_l):
                continue
            ql = ctx.cache.get((stp_l, right))
            if ql is None or not _liquidity_ok(ql):
                continue
            buy_prem = ql.best_offer_price or ql.ltp
            for stp_s in short_strikes:
                if combos >= MAX_CANDIDATES_PER_CONVICTION:
                    break
                if not short_filter(stp_s):
                    continue
                if not long_below_short(stp_l, stp_s):
                    continue
                combos += 1
                cid = _spread_candidate_id(right, stp_l, stp_s)
                funnel.generated += 1
                _promote_stage(
                    collector,
                    candidate_id=cid,
                    stage="generated",
                    profile=conviction_profile,
                    long_strike=stp_l,
                    short_strike=stp_s,
                )

                qs = ctx.cache.get((stp_s, right))
                if qs is None or not _liquidity_ok(qs):
                    _record_reject(
                        collector,
                        candidate_id=cid,
                        profile=conviction_profile,
                        reject_reason="liquidity",
                        long_strike=stp_l,
                        short_strike=stp_s,
                    )
                    continue
                funnel.passed_liquidity += 1
                _promote_stage(
                    collector,
                    candidate_id=cid,
                    stage="passed_liquidity",
                    profile=conviction_profile,
                    long_strike=stp_l,
                    short_strike=stp_s,
                )

                sell_prem = qs.best_bid_price or qs.ltp
                net_per = buy_prem - sell_prem
                if net_per <= 0:
                    _record_reject(
                        collector,
                        candidate_id=cid,
                        profile=conviction_profile,
                        reject_reason="no_credit",
                        long_strike=stp_l,
                        short_strike=stp_s,
                    )
                    continue
                funnel.passed_valid_debit += 1
                _promote_stage(
                    collector,
                    candidate_id=cid,
                    stage="passed_credit",
                    profile=conviction_profile,
                    long_strike=stp_l,
                    short_strike=stp_s,
                )

                max_loss_lot = net_per * L
                qty = size_quantity_from_budgets(
                    sid,
                    buy_prem * L,
                    max_loss_lot,
                    margin_rupees=ctx.margin_rupees,
                    max_loss_rupees=ctx.effective_max_loss_budget(),
                    lot_size=L,
                    unit_short_lots=1,
                    spot=ctx.spot,
                    provision_elm=ctx.provision_elm,
                )
                if qty < L:
                    _record_reject(
                        collector,
                        candidate_id=cid,
                        profile=conviction_profile,
                        reject_reason="quantity",
                        long_strike=stp_l,
                        short_strike=stp_s,
                    )
                    continue

                width = abs(stp_l - stp_s)
                legs = [
                    TradeLeg(right, "Buy", stp_l, qty, buy_prem),
                    TradeLeg(right, "Sell", stp_s, qty, sell_prem),
                ]
                max_loss = net_per * qty
                if ctx.max_loss_rupees is not None and max_loss > ctx.max_loss_rupees:
                    _record_reject(
                        collector,
                        candidate_id=cid,
                        profile=conviction_profile,
                        reject_reason="budget",
                        long_strike=stp_l,
                        short_strike=stp_s,
                        max_loss=max_loss,
                    )
                    continue

                funnel.passed_constraints += 1
                _promote_stage(
                    collector,
                    candidate_id=cid,
                    stage="passed_constraints",
                    profile=conviction_profile,
                    long_strike=stp_l,
                    short_strike=stp_s,
                )
                max_gain = (width - net_per) * qty
                debit_paid = max_loss
                pop = pop_for_legs(ctx, legs)
                components = score_spread_components(
                    ql,
                    qs,
                    long_target=long_target,
                    short_target=short_target,
                    max_gain=max_gain,
                    max_loss=max_loss,
                    debit_paid=debit_paid,
                )
                raw_candidates.append(
                    (components, legs, max_loss, max_gain, pop, debit_paid, stp_l, stp_s)
                )

        last_funnel = funnel
        if raw_candidates:
            if audit_state is not None:
                audit_state.candidates_by_profile[conviction_profile] = len(raw_candidates)

            rr_norm = normalize_min_max(c["reward_to_risk_raw"] for c, *_ in raw_candidates)
            cap_norm = normalize_min_max(c["capital_efficiency_raw"] for c, *_ in raw_candidates)
            for idx, (components, legs, max_loss, max_gain, pop, debit_paid, stp_l, stp_s) in enumerate(
                raw_candidates
            ):
                score, breakdown = finalize_spread_score(
                    components,
                    reward_risk_norm=rr_norm[idx],
                    capital_eff_norm=cap_norm[idx],
                )
                if audit_state is not None:
                    audit_state.shortlist_scores.append(
                        {
                            "conviction_profile": conviction_profile,
                            "long_strike": stp_l,
                            "short_strike": stp_s,
                            "score": score,
                            "score_breakdown": breakdown,
                            "pop_pct": round(pop, 2),
                            "delta_tolerance": tol,
                        }
                    )
                cand = ConvictionCandidate(
                    legs=legs,
                    max_loss=max_loss,
                    max_gain=max_gain,
                    pop=pop,
                    score=score,
                    score_breakdown=breakdown,
                    conviction_profile=conviction_profile,
                    long_target=long_target,
                    short_target=short_target,
                    premium_paid=debit_paid,
                )
                if best is None or cand.score > best.score:
                    best = cand
            break

    if best is None:
        audit = _build_profile_audit(
            conviction_profile,
            initial_tol=initial_tol,
            final_tol=final_tol,
            widening_attempts=widening_attempts,
            funnel=last_funnel,
            returned=0,
            status="skipped",
            skip_reason=_SKIP_REASON_NO_CANDIDATES,
        )
        return None, audit

    audit = _build_profile_audit(
        conviction_profile,
        initial_tol=initial_tol,
        final_tol=final_tol,
        widening_attempts=widening_attempts,
        funnel=last_funnel,
        returned=1,
        status="success",
    )
    return best, audit


def _long_option_tile_metrics(candidate: ConvictionCandidate) -> tuple[TileMetric, list[TileMetric]]:
    hero = TileMetric(
        label="Capital at Risk",
        value=_format_rupees(candidate.max_loss),
    )
    delta_label = f"Δ {candidate.abs_delta:.2f}" if candidate.abs_delta is not None else "—"
    secondary = [
        TileMetric(label="Move Sensitivity", value=delta_label),
        TileMetric(label="Premium Paid", value=_format_rupees(candidate.premium_paid)),
        TileMetric(label="Est. PoP", value=f"{candidate.pop:.1f}%"),
    ]
    return hero, secondary


def _spread_tile_metrics(candidate: ConvictionCandidate) -> tuple[TileMetric, list[TileMetric]]:
    rr = f"1 : {candidate.max_gain / max(candidate.max_loss, 1):.2f}"
    hero = TileMetric(label="Reward : Risk", value=rr)
    secondary = [
        TileMetric(label="Max Gain", value=_format_rupees(candidate.max_gain)),
        TileMetric(label="Max Loss", value=_format_rupees(candidate.max_loss)),
        TileMetric(label="Premium", value=_format_rupees(candidate.premium_paid)),
        TileMetric(label="SPAN", value="—"),
        TileMetric(label="ELM", value="—"),
        TileMetric(
            label="Capital Required",
            value=_format_rupees(candidate.premium_paid),
        ),
        TileMetric(label="Est. PoP", value=f"{candidate.pop:.1f}%"),
    ]
    return hero, secondary


def build_directional_result(
    ctx: EngineContext,
    candidate: ConvictionCandidate,
    *,
    sid: str,
    base_name: str,
    is_spread: bool,
) -> StrategyResult:
    profile = candidate.conviction_profile
    name = base_name
    if is_spread:
        rr = f"{candidate.max_loss:.0f} : {candidate.max_gain:.0f}"
        hero, secondary = _spread_tile_metrics(candidate)
    else:
        rr = f"{candidate.max_loss:.0f} : Unlimited"
        hero, secondary = _long_option_tile_metrics(candidate)

    ranking_summary = (
        f"Δ target {candidate.long_target:.2f}"
        + (f"/{candidate.short_target:.2f}" if candidate.short_target is not None else "")
        + f" · score {candidate.score:.2f}"
    )

    return make_result(
        ctx,
        sid,
        name,
        candidate.legs,
        max_loss=candidate.max_loss,
        rr=rr,
        pop=candidate.pop,
        net_premium_val=-candidate.premium_paid,
        engine_score=candidate.score,
        ranking_summary=ranking_summary,
        score_breakdown=candidate.score_breakdown,
        conviction_profile=profile,
        hero_metric=hero,
        secondary_metrics=secondary,
    )


def run_long_option_profiles(
    ctx: EngineContext,
    *,
    sid: str,
    base_name: str,
    right: Right,
) -> list[StrategyResult]:
    if ctx.halted:
        return [skip(sid, base_name, ctx.halt_reason or "Market halted")]
    collector = audit_collector_for(ctx)
    audit_state = setup_directional_audit(collector)
    results: list[StrategyResult] = []
    for profile in CONVICTION_PROFILES:
        best, profile_audit = evaluate_long_option(
            ctx,
            sid=sid,
            right=right,
            conviction_profile=profile,
            collector=collector,
            audit_state=audit_state,
        )
        if audit_state is not None:
            audit_state.profile_audits.append(profile_audit.to_dict())
        if best is None:
            continue
        if audit_state is not None:
            audit_state.profile_winners.append(
                {
                    "conviction_profile": profile,
                    "score_breakdown": best.score_breakdown,
                    "engine_score": best.score,
                    "pop_pct": round(best.pop, 2),
                }
            )
        record_directional_profile_winner(
            collector,
            best.legs,
            conviction_profile=profile,
            metrics={
                "pop_pct": best.pop,
                "max_loss": best.max_loss,
                "net_credit": -best.premium_paid,
                "engine_score": best.score,
                "score_breakdown": best.score_breakdown,
            },
        )
        results.append(
            build_directional_result(ctx, best, sid=sid, base_name=base_name, is_spread=False)
        )
    if not results:
        return [skip(sid, base_name, f"No {base_name.lower()} meets conviction targets and budgets.")]
    return results


def run_spread_profiles(
    ctx: EngineContext,
    *,
    sid: str,
    base_name: str,
    right: Right,
    spread_kind: SpreadKind,
) -> list[StrategyResult]:
    if ctx.halted:
        return [skip(sid, base_name, ctx.halt_reason or "Market halted")]
    collector = audit_collector_for(ctx)
    audit_state = setup_directional_audit(collector)
    results: list[StrategyResult] = []
    for profile in CONVICTION_PROFILES:
        best, profile_audit = evaluate_vertical_spread(
            ctx,
            sid=sid,
            right=right,
            spread_kind=spread_kind,
            conviction_profile=profile,
            collector=collector,
            audit_state=audit_state,
        )
        if audit_state is not None:
            audit_state.profile_audits.append(profile_audit.to_dict())
        if best is None:
            continue
        if audit_state is not None:
            audit_state.profile_winners.append(
                {
                    "conviction_profile": profile,
                    "score_breakdown": best.score_breakdown,
                    "engine_score": best.score,
                    "pop_pct": round(best.pop, 2),
                }
            )
        record_directional_profile_winner(
            collector,
            best.legs,
            conviction_profile=profile,
            metrics={
                "pop_pct": best.pop,
                "max_loss": best.max_loss,
                "max_gain": best.max_gain,
                "net_credit": -best.premium_paid,
                "engine_score": best.score,
                "score_breakdown": best.score_breakdown,
            },
        )
        results.append(
            build_directional_result(ctx, best, sid=sid, base_name=base_name, is_spread=True)
        )
    if not results:
        return [skip(sid, base_name, f"No {base_name.lower()} meets conviction targets and budgets.")]
    return results


def prefetch_all_conviction_strikes(
    ctx: EngineContext,
    right: Right,
    *,
    include_spread_window: bool = False,
    strategy_id: str | None = None,
) -> set[tuple[int, Right]]:
    from icici_breeze_backend.app.services.options_strategy_engine.anchors import (
        build_anchor_index,
        max_steps_for_strategy,
        strikes_in_window,
    )
    from icici_breeze_backend.app.services.options_strategy_engine.strategies.common import (
        estimate_strike_for_abs_delta,
    )

    kind = _conviction_kind_for_sid(strategy_id) if strategy_id else "spread"
    pairs: set[tuple[int, Right]] = set()
    seen_targets: set[float] = set()
    for profile in CONVICTION_PROFILES:
        long_target, short_target = profile_deltas(profile, kind=kind)
        for target in (long_target, short_target):
            if target <= 0 or target in seen_targets:
                continue
            seen_targets.add(target)
            strike = estimate_strike_for_abs_delta(ctx, right, target)
            if strike is not None:
                pairs.add((strike, right))
    if include_spread_window and strategy_id:
        anchors = build_anchor_index(ctx.strikes, ctx.spot, ctx.strike_step)
        for strike in strikes_in_window(
            ctx.strikes, anchors, right, max_steps_for_strategy(strategy_id)
        ):
            pairs.add((strike, right))
    return pairs
