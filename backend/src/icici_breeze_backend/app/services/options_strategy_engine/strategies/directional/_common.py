"""Shared conviction-based engine for directional strategy calculators."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Literal

from icici_breeze_backend.app.services.options_strategy_engine.delta_anchor import (
    CONVICTION_PROFILES,
    DELTA_CANDIDATE_WINDOW,
    MIN_LIQUIDITY_SCORE,
    MAX_CANDIDATES_PER_CONVICTION,
    RiskRewardProfile,
    profile_deltas,
    strikes_near_delta,
)
from icici_breeze_backend.app.services.options_strategy_engine.helpers import skip
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
    Right,
    StrategyResult,
    TileMetric,
    TradeLeg,
)
from icici_breeze_backend.audit.strategy_evaluation_audit import (
    audit_collector_for,
    record_directional_profile_winner,
    record_directional_attempt,
    setup_directional_audit,
)

_DIRECTIONAL_STAGES = ("passed_liquidity", "passed_credit", "passed_constraints", "returned")
SpreadKind = Literal["bull_call", "bear_put"]


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
    profile_winners: list[dict] = field(default_factory=list)
    shortlist_scores: list[dict] = field(default_factory=list)


def _profile_label(profile: RiskRewardProfile) -> str:
    return profile.capitalize()


def _format_rupees(amount: float) -> str:
    return f"₹{amount:,.0f}"


def _liquidity_ok(q) -> bool:
    return q.liquid and q.liquidity_score >= MIN_LIQUIDITY_SCORE


def _record_reject(
    collector,
    audit_state: DirectionalAuditState | None,
    profile: RiskRewardProfile,
    reason: str,
    **fields,
) -> None:
    record_directional_attempt(collector, reject_reason=reason, conviction_profile=profile, **fields)


def evaluate_long_option(
    ctx: EngineContext,
    *,
    sid: str,
    right: Right,
    conviction_profile: RiskRewardProfile,
    collector,
    audit_state: DirectionalAuditState | None,
) -> ConvictionCandidate | None:
    long_target, _ = profile_deltas(conviction_profile)
    L = ctx.lot_size
    liquid = all_liquid(ctx, right)
    strike_pool = strikes_near_delta(
        liquid,
        ctx.cache,
        right,
        long_target,
        tolerance=DELTA_CANDIDATE_WINDOW,
    )[:MAX_CANDIDATES_PER_CONVICTION]

    raw_candidates: list[tuple[dict, list[TradeLeg], float, float, float, float, QuoteRow]] = []
    for stp in strike_pool:
        q = ctx.cache.get((stp, right))
        if q is None or not _liquidity_ok(q):
            _record_reject(collector, audit_state, conviction_profile, "liquidity", strike=stp)
            continue
        buy_prem = q.best_offer_price or q.ltp
        if buy_prem <= 0:
            _record_reject(collector, audit_state, conviction_profile, "no_credit", strike=stp)
            continue
        debit_lot = buy_prem * L
        qty = size_quantity_from_budgets(
            sid,
            debit_lot,
            debit_lot,
            margin_rupees=ctx.margin_rupees,
            max_loss_rupees=ctx.max_loss_rupees,
            lot_size=L,
            unit_short_lots=0,
            spot=ctx.spot,
            provision_elm=ctx.provision_elm,
        )
        if qty < L:
            _record_reject(collector, audit_state, conviction_profile, "quantity", strike=stp)
            continue
        max_loss = buy_prem * qty
        if max_loss > ctx.max_loss_rupees:
            _record_reject(
                collector,
                audit_state,
                conviction_profile,
                "budget",
                strike=stp,
                max_loss=max_loss,
            )
            continue
        legs = [TradeLeg(right, "Buy", stp, qty, buy_prem)]
        pop = pop_for_legs(ctx, legs)
        components = score_long_option_components(
            ctx, q, target_delta=long_target, premium_per_unit=buy_prem
        )
        abs_delta = abs(q.delta) if q.delta is not None else 0.0
        raw_candidates.append((components, legs, max_loss, pop, buy_prem, abs_delta, q))

    if audit_state is not None:
        audit_state.candidates_by_profile[conviction_profile] = len(raw_candidates)

    if not raw_candidates:
        return None

    prem_norm = normalize_min_max(c["premium_efficiency_raw"] for c, *_ in raw_candidates)
    best: ConvictionCandidate | None = None
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
                }
            )
        record_directional_attempt(
            collector,
            conviction_profile=conviction_profile,
            pop_pct=pop,
            strike=legs[0].strike,
            score_breakdown=breakdown,
            engine_score=score,
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
    return best


def evaluate_vertical_spread(
    ctx: EngineContext,
    *,
    sid: str,
    right: Right,
    spread_kind: SpreadKind,
    conviction_profile: RiskRewardProfile,
    collector,
    audit_state: DirectionalAuditState | None,
) -> ConvictionCandidate | None:
    long_target, short_target = profile_deltas(conviction_profile)
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

    long_strikes = strikes_near_delta(
        liquid, ctx.cache, right, long_target, tolerance=DELTA_CANDIDATE_WINDOW
    )
    short_strikes = strikes_near_delta(
        liquid, ctx.cache, right, short_target, tolerance=DELTA_CANDIDATE_WINDOW
    )

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
            qs = ctx.cache.get((stp_s, right))
            if qs is None or not _liquidity_ok(qs):
                _record_reject(
                    collector,
                    audit_state,
                    conviction_profile,
                    "liquidity",
                    long_strike=stp_l,
                    short_strike=stp_s,
                )
                continue
            sell_prem = qs.best_bid_price or qs.ltp
            net_per = buy_prem - sell_prem
            if net_per <= 0:
                _record_reject(
                    collector,
                    audit_state,
                    conviction_profile,
                    "no_credit",
                    long_strike=stp_l,
                    short_strike=stp_s,
                )
                continue
            max_loss_lot = net_per * L
            qty = size_quantity_from_budgets(
                sid,
                buy_prem * L,
                max_loss_lot,
                margin_rupees=ctx.margin_rupees,
                max_loss_rupees=ctx.max_loss_rupees,
                lot_size=L,
                unit_short_lots=1,
                spot=ctx.spot,
                provision_elm=ctx.provision_elm,
            )
            if qty < L:
                _record_reject(
                    collector,
                    audit_state,
                    conviction_profile,
                    "quantity",
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
            if max_loss > ctx.max_loss_rupees:
                _record_reject(
                    collector,
                    audit_state,
                    conviction_profile,
                    "budget",
                    long_strike=stp_l,
                    short_strike=stp_s,
                    max_loss=max_loss,
                )
                continue
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

    if audit_state is not None:
        audit_state.candidates_by_profile[conviction_profile] = len(raw_candidates)

    if not raw_candidates:
        return None

    rr_norm = normalize_min_max(c["reward_to_risk_raw"] for c, *_ in raw_candidates)
    cap_norm = normalize_min_max(c["capital_efficiency_raw"] for c, *_ in raw_candidates)
    best: ConvictionCandidate | None = None
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
                }
            )
        record_directional_attempt(
            collector,
            conviction_profile=conviction_profile,
            pop_pct=pop,
            long_strike=stp_l,
            short_strike=stp_s,
            score_breakdown=breakdown,
            engine_score=score,
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
    return best


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
        TileMetric(label="Capital Required", value=_format_rupees(candidate.premium_paid)),
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
    name = f"{base_name} ({_profile_label(profile)})"
    if is_spread:
        rr = f"{candidate.max_loss:.0f} : {candidate.max_gain:.0f}"
        hero, secondary = _spread_tile_metrics(candidate)
    else:
        rr = f"{candidate.max_loss:.0f} : Unlimited"
        hero, secondary = _long_option_tile_metrics(candidate)

    ranking_summary = (
        f"{_profile_label(profile)} conviction · "
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
        best = evaluate_long_option(
            ctx,
            sid=sid,
            right=right,
            conviction_profile=profile,
            collector=collector,
            audit_state=audit_state,
        )
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
            stages_passed=list(_DIRECTIONAL_STAGES),
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
        best = evaluate_vertical_spread(
            ctx,
            sid=sid,
            right=right,
            spread_kind=spread_kind,
            conviction_profile=profile,
            collector=collector,
            audit_state=audit_state,
        )
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
            stages_passed=list(_DIRECTIONAL_STAGES),
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

    pairs: set[tuple[int, Right]] = set()
    seen_targets: set[float] = set()
    for profile in CONVICTION_PROFILES:
        long_target, short_target = profile_deltas(profile)
        for target in (long_target, short_target):
            if target in seen_targets:
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
