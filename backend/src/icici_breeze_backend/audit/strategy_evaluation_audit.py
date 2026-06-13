"""Structured per-strategy evaluation audit for Strategy Builder."""
from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass, field
from typing import Any, Literal

AuditDetailLevel = Literal["summary", "debug"]

AUDIT_SCHEMA_VERSION = "2.0"
NEAR_MISSES_MAX_SUMMARY = 5

# Canonical funnel keys surfaced in audit JSON.
CANONICAL_FUNNEL_KEYS = frozenset(
    {
        "pop_floor",
        "economic_prune",
        "max_loss",
        "min_credit",
        "liquidity",
        "span_failure",
        "quantity",
        "delta_mismatch",
        "budget",
        "capital",
        "min_ann_return",
        "other",
    }
)

# Raw reject reason -> canonical funnel bucket.
REJECTION_REASON_MAP: dict[str, str] = {
    "missing_quote": "liquidity",
    "illiquid": "liquidity",
    "illiquid_wing": "liquidity",
    "liquidity": "liquidity",
    "no_credit": "min_credit",
    "debit_put_wing": "min_credit",
    "debit_call_wing": "min_credit",
    "min_wing_credit": "min_credit",
    "economic_prune": "economic_prune",
    "max_loss_budget": "max_loss",
    "pop_floor": "pop_floor",
    "quantity": "quantity",
    "span_failure": "span_failure",
    "delta_mismatch": "delta_mismatch",
    "budget": "budget",
    "capital": "capital",
    "below_min_ann_return": "min_ann_return",
    "not_finalist": "other",
}


def canonical_rejection_reason(reason: str | None) -> str:
    if not reason:
        return "other"
    return REJECTION_REASON_MAP.get(reason, "other")


@dataclass(frozen=True)
class PopPolicy:
    used_for_filtering: bool
    used_for_ranking: bool
    ignored: bool
    pop_weight: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "used_for_filtering": self.used_for_filtering,
            "used_for_ranking": self.used_for_ranking,
            "ignored": self.ignored,
            "pop_weight": self.pop_weight,
        }


# Income optimizers: PoP hard floor only — never used for ranking.
_INCOME_POP_POLICY = PopPolicy(
    used_for_filtering=True,
    used_for_ranking=False,
    ignored=False,
    pop_weight=None,
)
# Directional: PoP informational only — not used for filtering or ranking.
_DIRECTIONAL_POP_POLICY = PopPolicy(
    used_for_filtering=False,
    used_for_ranking=False,
    ignored=True,
    pop_weight=None,
)
# Volatility long structures: PoP computed for display, not gated or ranked.
_VOLATILITY_POP_POLICY = PopPolicy(
    used_for_filtering=False,
    used_for_ranking=False,
    ignored=True,
    pop_weight=None,
)

POP_POLICY_REGISTRY: dict[str, PopPolicy] = {
    "naked_ce_short": _INCOME_POP_POLICY,
    "naked_pe_short": _INCOME_POP_POLICY,
    "iron_condor": _INCOME_POP_POLICY,
    "iron_butterfly": _INCOME_POP_POLICY,
    "short_strangle": _INCOME_POP_POLICY,
    "short_straddle": _INCOME_POP_POLICY,
    "bull_put_spread": _INCOME_POP_POLICY,
    "bear_call_spread": _INCOME_POP_POLICY,
    "bull_call_spread": _DIRECTIONAL_POP_POLICY,
    "bear_put_spread": _DIRECTIONAL_POP_POLICY,
    "long_call": _DIRECTIONAL_POP_POLICY,
    "long_put": _DIRECTIONAL_POP_POLICY,
    "long_straddle": _VOLATILITY_POP_POLICY,
    "long_strangle": _VOLATILITY_POP_POLICY,
    "long_butterfly": _VOLATILITY_POP_POLICY,
    "long_condor": _VOLATILITY_POP_POLICY,
}


def pop_policy_for(strategy_id: str) -> PopPolicy:
    return POP_POLICY_REGISTRY.get(
        strategy_id,
        PopPolicy(used_for_filtering=False, used_for_ranking=False, ignored=True, pop_weight=None),
    )


def pop_bucket_label(pop_pct: float, floor_pct: float, *, band_width: float = 2.0) -> str:
    """1% PoP bucket anchored at the user floor."""
    if pop_pct < floor_pct:
        return f"<{floor_pct:.0f}"
    offset = int((pop_pct - floor_pct) // 1.0)
    if offset >= band_width + 1:
        return f">={floor_pct + band_width + 1:.0f}"
    low = floor_pct + offset
    high = low + 1.0
    return f"{low:.0f}-{high:.0f}"


def candidate_id_for_legs(legs: list[Any]) -> str:
    """Stable ID from leg structure (right/side/strike)."""
    parts = [
        {"right": getattr(l, "right", l.get("right") if isinstance(l, dict) else None),
         "side": getattr(l, "side", l.get("side") if isinstance(l, dict) else None),
         "strike": getattr(l, "strike", l.get("strike") if isinstance(l, dict) else None)}
        for l in legs
    ]
    payload = json.dumps(sorted(parts, key=lambda x: (x["strike"], x["right"], x["side"])), sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def build_histogram(
    values: list[float],
    bucket_fn: Any,
) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            continue
        label = bucket_fn(v)
        out[label] = out.get(label, 0) + 1
    return dict(sorted(out.items()))


def credit_bucket(credit: float) -> str:
    if credit < 0:
        return "<0"
    step = 5.0
    low = int(credit // step) * step
    return f"{low:.0f}-{low + step:.0f}"


def ann_return_bucket(pct: float) -> str:
    if pct < 0:
        return "<0"
    if pct < 5:
        return "0-5"
    if pct < 10:
        return "5-10"
    if pct < 20:
        return "10-20"
    if pct < 50:
        return "20-50"
    return "50+"


def span_bucket(span: float) -> str:
    if span <= 0:
        return "0"
    step = 20_000.0
    low = int(span // step) * step
    return f"{int(low / 1000)}k-{int((low + step) / 1000)}k"


def build_rejection_funnel_by_pop_bucket(
    evaluations: list[dict[str, Any]],
    *,
    min_pop_pct: float,
    band_width: float = 2.0,
) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for ev in evaluations:
        if ev.get("outcome") != "rejected":
            continue
        reason = ev.get("reject_reason")
        if not reason:
            continue
        pop_pct = ev.get("pop_pct")
        if pop_pct is None:
            bucket = "unknown"
        else:
            bucket = pop_bucket_label(float(pop_pct), min_pop_pct, band_width=band_width)
        canonical = canonical_rejection_reason(str(reason))
        bucket_map = out.setdefault(bucket, {})
        bucket_map[canonical] = bucket_map.get(canonical, 0) + 1
    return {k: dict(sorted(v.items())) for k, v in sorted(out.items())}


@dataclass
class SessionTelemetry:
    """Session-level API and timing telemetry."""

    quote_latencies_ms: list[float] = field(default_factory=list)
    margin_latencies_ms: list[float] = field(default_factory=list)
    span_cache_hits: int = 0
    span_cache_misses: int = 0
    candidate_generation_ms: dict[str, float] = field(default_factory=dict)
    ranking_ms: dict[str, float] = field(default_factory=dict)
    strategy_execution_ms: dict[str, float] = field(default_factory=dict)

    def record_quote_latency(self, ms: float) -> None:
        self.quote_latencies_ms.append(ms)

    def record_margin_latency(self, ms: float) -> None:
        self.margin_latencies_ms.append(ms)

    def record_span_cache(self, *, hit: bool, count: int = 1) -> None:
        if hit:
            self.span_cache_hits += count
        else:
            self.span_cache_misses += count

    @staticmethod
    def _latency_summary(latencies: list[float]) -> dict[str, float]:
        if not latencies:
            return {"total": 0.0, "count": 0, "p50": 0.0, "p95": 0.0}
        sorted_l = sorted(latencies)
        n = len(sorted_l)
        p50 = sorted_l[n // 2]
        p95 = sorted_l[min(n - 1, int(n * 0.95))]
        return {
            "total": round(sum(sorted_l), 2),
            "count": n,
            "p50": round(p50, 2),
            "p95": round(p95, 2),
        }

    def to_dict(self, *, quote_calls: int, margin_calls: int, total_execution_ms: int) -> dict[str, Any]:
        return {
            "quote_calls": quote_calls,
            "quote_latency_ms": self._latency_summary(self.quote_latencies_ms),
            "margin_calls": margin_calls,
            "margin_latency_ms": self._latency_summary(self.margin_latencies_ms),
            "span_cache_hits": self.span_cache_hits,
            "span_cache_misses": self.span_cache_misses,
            "candidate_generation_ms": dict(self.candidate_generation_ms),
            "ranking_ms": dict(self.ranking_ms),
            "strategy_execution_ms": dict(self.strategy_execution_ms),
            "total_execution_ms": total_execution_ms,
        }


@dataclass
class StrategyAuditCollector:
    """Per-strategy accumulator for structured evaluation audit."""

    strategy_id: str = ""
    min_pop_pct: float = 0.0
    pop_band_width: float = 2.0
    detail_level: AuditDetailLevel = "summary"

    combos_tried: int = 0
    stage_counts: dict[str, int] = field(
        default_factory=lambda: {
            "generated": 0,
            "passed_liquidity": 0,
            "passed_credit": 0,
            "passed_constraints": 0,
            "passed_economic_prune": 0,
            "passed_pop": 0,
            "passed_capital": 0,
            "passed_loss": 0,
            "feasible": 0,
            "margin_refined": 0,
            "returned": 0,
        }
    )
    counts: dict[str, int] = field(default_factory=dict)
    samples: list[dict[str, Any]] = field(default_factory=list)
    evaluations: list[dict[str, Any]] = field(default_factory=list)
    pop_bucket_counts: dict[str, int] = field(default_factory=dict)
    survivors_by_pop_bucket: dict[str, int] = field(default_factory=dict)
    rejection_funnel_by_pop_bucket: dict[str, dict[str, int]] = field(default_factory=dict)

    # Metric samples for distributions (survivors / evaluated).
    _pop_samples: list[float] = field(default_factory=list)
    _credit_samples: list[float] = field(default_factory=list)
    _ann_return_samples: list[float] = field(default_factory=list)
    _span_samples: list[float] = field(default_factory=list)

    winners: list[dict[str, Any]] = field(default_factory=list)
    near_misses: list[dict[str, Any]] = field(default_factory=list)

    # Iron condor wing-plan trace (backward compatible with tests).
    pair_wing_plans: list[dict[str, Any]] = field(default_factory=list)

    # Directional conviction audit (optional).
    directional_audit: Any | None = None
    income_audit: dict[str, Any] | None = None
    _directional_stage_ids: dict[str, set[str]] = field(default_factory=dict)

    status: str = "pending"
    skip_reason: str | None = None

    _gen_start: float | None = None
    _rank_start: float | None = None

    def begin_generation(self) -> None:
        self._gen_start = time.perf_counter()

    def end_generation(self, telemetry: SessionTelemetry | None) -> None:
        if self._gen_start is not None and telemetry is not None:
            elapsed = (time.perf_counter() - self._gen_start) * 1000
            telemetry.candidate_generation_ms[self.strategy_id] = round(elapsed, 2)
        self._gen_start = None

    def begin_ranking(self) -> None:
        self._rank_start = time.perf_counter()

    def end_ranking(self, telemetry: SessionTelemetry | None) -> None:
        if self._rank_start is not None and telemetry is not None:
            elapsed = (time.perf_counter() - self._rank_start) * 1000
            telemetry.ranking_ms[self.strategy_id] = round(elapsed, 2)
        self._rank_start = None

    def record_generated(self) -> None:
        self.combos_tried += 1
        self.stage_counts["generated"] += 1

    def record_stage(self, stage: str) -> None:
        if stage in self.stage_counts:
            self.stage_counts[stage] += 1

    def record(
        self,
        reason: str,
        **detail: Any,
    ) -> None:
        self.counts[reason] = self.counts.get(reason, 0) + 1
        if len(self.samples) < 25:
            self.samples.append({"reason": reason, **detail})

    def record_evaluation(
        self,
        *,
        outcome: str,
        reject_reason: str | None = None,
        pop_pct: float | None = None,
        pop_detail: Any | None = None,
        credit: float | None = None,
        **fields: Any,
    ) -> None:
        entry: dict[str, Any] = {
            "outcome": outcome,
            "reject_reason": reject_reason,
            "credit": round(credit, 4) if credit is not None else None,
            **fields,
        }
        if pop_detail is not None and hasattr(pop_detail, "to_audit_dict"):
            entry.update(pop_detail.to_audit_dict())
            pop_pct = pop_detail.pop_pct
        elif pop_pct is not None:
            entry["pop_pct"] = round(pop_pct, 4)
        else:
            entry["pop_pct"] = None

        self.evaluations.append(entry)

        if pop_pct is not None and self.min_pop_pct > 0:
            bucket = pop_bucket_label(float(pop_pct), self.min_pop_pct, band_width=self.pop_band_width)
            self.pop_bucket_counts[bucket] = self.pop_bucket_counts.get(bucket, 0) + 1
            self._pop_samples.append(float(pop_pct))
            if outcome == "accepted":
                self.survivors_by_pop_bucket[bucket] = (
                    self.survivors_by_pop_bucket.get(bucket, 0) + 1
                )
            elif reject_reason:
                canonical = canonical_rejection_reason(reject_reason)
                bucket_map = self.rejection_funnel_by_pop_bucket.setdefault(bucket, {})
                bucket_map[canonical] = bucket_map.get(canonical, 0) + 1

        if credit is not None:
            self._credit_samples.append(float(credit))

    def record_survivor_metrics(
        self,
        *,
        pop_pct: float | None = None,
        credit: float | None = None,
        ann_return_pct: float | None = None,
        unit_span: float | None = None,
    ) -> None:
        if pop_pct is not None:
            self._pop_samples.append(float(pop_pct))
        if credit is not None:
            self._credit_samples.append(float(credit))
        if ann_return_pct is not None:
            self._ann_return_samples.append(float(ann_return_pct))
        if unit_span is not None and unit_span > 0:
            self._span_samples.append(float(unit_span))

    def record_winner(
        self,
        *,
        candidate_id: str,
        legs: list[Any],
        metrics: dict[str, Any],
        stages_passed: list[str],
        ranks: dict[str, int],
        soft_filter_violations: list[str] | None = None,
        increment_returned: bool = True,
    ) -> None:
        self.winners.append(
            {
                "candidate_id": candidate_id,
                "legs": [
                    {
                        "right": getattr(l, "right", None),
                        "side": getattr(l, "side", None),
                        "strike": getattr(l, "strike", None),
                    }
                    for l in legs
                ],
                "metrics": metrics,
                "stages_passed": stages_passed,
                "ranks": ranks,
                "soft_filter_violations": soft_filter_violations or [],
            }
        )
        if increment_returned:
            self.stage_counts["returned"] += 1

    def record_near_miss(
        self,
        *,
        candidate_id: str,
        metrics: dict[str, Any],
        rejection_reason: str,
        context: str | None = None,
    ) -> None:
        self.near_misses.append(
            {
                "candidate_id": candidate_id,
                "metrics": metrics,
                "rejection_reason": rejection_reason,
                "context": context,
            }
        )

    def set_status(self, status: str, skip_reason: str | None = None) -> None:
        self.status = status
        self.skip_reason = skip_reason

    def skip_message(self) -> str:
        """Human-readable skip reason from rejection counts (optimizer strategies)."""
        if not self.counts:
            return f"No {self.strategy_id.replace('_', ' ')} candidates could be evaluated on the liquid chain."
        total = sum(self.counts.values())
        parts = ", ".join(
            f"{count} {reason}"
            for reason, count in sorted(self.counts.items(), key=lambda x: -x[1])
        )
        top_reason = max(self.counts.items(), key=lambda x: x[1])[0]
        label = self.strategy_id.replace("_", " ")
        if top_reason == "pop_floor":
            return (
                f"No {label} meets minimum PoP within risk limits "
                f"({total} rejected: {parts})."
            )
        return f"No {label} passed filters ({total} rejected: {parts})."

    def rejection_funnel(self) -> dict[str, int]:
        funnel: dict[str, int] = {}
        for reason, count in self.counts.items():
            canonical = canonical_rejection_reason(reason)
            funnel[canonical] = funnel.get(canonical, 0) + count
        return dict(sorted(funnel.items(), key=lambda x: -x[1]))

    def distributions(self) -> dict[str, dict[str, int]]:
        return {
            "pop_pct": build_histogram(
                self._pop_samples,
                lambda v: pop_bucket_label(v, self.min_pop_pct, band_width=self.pop_band_width),
            ),
            "net_credit": build_histogram(self._credit_samples, credit_bucket),
            "annualized_return_pct": build_histogram(self._ann_return_samples, ann_return_bucket),
            "unit_span": build_histogram(self._span_samples, span_bucket),
        }

    def to_dict(self) -> dict[str, Any]:
        near_misses = self.near_misses
        if self.detail_level == "summary":
            near_misses = near_misses[:NEAR_MISSES_MAX_SUMMARY]

        out: dict[str, Any] = {
            "strategy_summary": {
                **self.stage_counts,
                "status": self.status,
                "skip_reason": self.skip_reason,
            },
            "pop_policy": pop_policy_for(self.strategy_id).to_dict(),
            "rejection_funnel": self.rejection_funnel(),
            "rejection_funnel_by_pop_bucket": dict(
                sorted(self.rejection_funnel_by_pop_bucket.items())
            ),
            "distributions": self.distributions(),
            "winners": self.winners,
            "near_misses": near_misses,
        }
        if self.income_audit is not None:
            out["income_audit"] = self.income_audit
        if self.directional_audit is not None:
            da = self.directional_audit
            out["conviction_config"] = da.get("conviction_config")
            out["candidates_by_profile"] = da.get("candidates_by_profile", {})
            out["profile_audits"] = da.get("profile_audits", [])
            out["profile_winners"] = da.get("profile_winners", [])
            if self.detail_level == "debug":
                out["shortlist_scores"] = da.get("shortlist_scores", [])
        if self.detail_level == "debug":
            out["candidate_traces"] = self.evaluations
            out["rejection_samples"] = self.samples[:25]
            out["raw_rejection_counts"] = dict(self.counts)
            out["combos_tried"] = self.combos_tried
        return out


def build_shared_engine_config() -> dict[str, Any]:
    from icici_breeze_backend.app.services.options_strategy_engine.types import (
        MAX_CANDIDATES_PER_STRATEGY,
        POP_TOLERANCE_PCT,
        TOP_K_SHORT_STRIKES,
        TOP_M_WING_STRIKES,
    )
    from icici_breeze_backend.app.services.options_strategy_engine.pruning import (
        DEFAULT_WING_WIDTH_MULTIPLIERS,
    )

    return {
        "TOP_K_SHORT_STRIKES": TOP_K_SHORT_STRIKES,
        "TOP_M_WING_STRIKES": TOP_M_WING_STRIKES,
        "MAX_CANDIDATES_PER_STRATEGY": MAX_CANDIDATES_PER_STRATEGY,
        "POP_TOLERANCE_PCT": POP_TOLERANCE_PCT,
        "DEFAULT_WING_WIDTH_MULTIPLIERS": list(DEFAULT_WING_WIDTH_MULTIPLIERS),
    }


def strategy_config_snapshot(strategy_id: str) -> dict[str, Any]:
    """Return module-level tuning constants for a strategy."""
    snapshots: dict[str, dict[str, Any]] = {}

    def _bps():
        from icici_breeze_backend.app.services.options_strategy_engine.strategies.income import (
            bull_put_spread as m,
        )
        from icici_breeze_backend.app.services.options_strategy_engine.strategies.income._common import (
            BADGE_CAPITAL,
            BADGE_INCOME,
            BADGE_MARGIN,
            SPAN_SHORTLIST_N,
            pop_band,
        )
        return {
            "SPAN_SHORTLIST_N": SPAN_SHORTLIST_N,
            "pop_band_fn": "adaptive",
            "WING_WIDTH_MULTIPLIERS": list(m.WING_WIDTH_MULTIPLIERS),
            "OBJECTIVE_BADGES": [BADGE_INCOME, BADGE_CAPITAL, BADGE_MARGIN],
            "default_pop_band_at_65": pop_band(65.0),
        }

    def _bcs():
        from icici_breeze_backend.app.services.options_strategy_engine.strategies.income import (
            bear_call_spread as m,
        )
        from icici_breeze_backend.app.services.options_strategy_engine.strategies.income._common import (
            SPAN_SHORTLIST_N,
        )
        return {
            "SPAN_SHORTLIST_N": SPAN_SHORTLIST_N,
            "WING_WIDTH_MULTIPLIERS": list(m.WING_WIDTH_MULTIPLIERS),
        }

    def _ic():
        from icici_breeze_backend.app.services.options_strategy_engine.strategies.income import (
            iron_condor as m,
        )
        from icici_breeze_backend.app.services.options_strategy_engine.strategies.income._common import (
            SPAN_SHORTLIST_N,
        )
        return {
            "SPAN_SHORTLIST_N": SPAN_SHORTLIST_N,
            "MIN_WING_CREDIT": m.MIN_WING_CREDIT,
            "MIN_IC_CREDIT_PCT_OF_WIDTH": m.MIN_IC_CREDIT_PCT_OF_WIDTH,
            "WING_WIDTH_MULTIPLIERS": list(m.WING_WIDTH_MULTIPLIERS),
        }

    def _ss():
        from icici_breeze_backend.app.services.options_strategy_engine.strategies.income._common import (
            NAKED_ANCHOR_TOP_K,
            SPAN_SHORTLIST_N,
        )
        return {
            "SPAN_SHORTLIST_N": SPAN_SHORTLIST_N,
            "NAKED_ANCHOR_TOP_K": NAKED_ANCHOR_TOP_K,
        }

    def _directional():
        from icici_breeze_backend.app.services.options_strategy_engine.delta_anchor import (
            CONVICTION_PROFILES,
            DELTA_TOLERANCE,
            DELTA_TOLERANCE_SEQUENCE,
            MAX_CANDIDATES_PER_CONVICTION,
            MIN_LIQUIDITY_SCORE,
            conviction_delta_templates,
        )
        return {
            "CONVICTION_PROFILES": list(CONVICTION_PROFILES),
            "DELTA_TOLERANCE": DELTA_TOLERANCE,
            "DELTA_TOLERANCE_SEQUENCE": list(DELTA_TOLERANCE_SEQUENCE),
            "MAX_CANDIDATES_PER_CONVICTION": MAX_CANDIDATES_PER_CONVICTION,
            "MIN_LIQUIDITY_SCORE": MIN_LIQUIDITY_SCORE,
            "delta_templates": conviction_delta_templates(),
        }

    builders = {
        "bull_put_spread": _bps,
        "bear_call_spread": _bcs,
        "iron_condor": _ic,
        "short_strangle": _ss,
        "bull_call_spread": _directional,
        "bear_put_spread": _directional,
        "long_call": _directional,
        "long_put": _directional,
    }
    builder = builders.get(strategy_id)
    if builder:
        return builder()
    return {}


def build_configuration_snapshot(
    request: dict[str, Any],
    strategy_ids: list[str],
) -> dict[str, Any]:
    strategies = {sid: strategy_config_snapshot(sid) for sid in strategy_ids}
    return {
        "request": request,
        "shared_engine": build_shared_engine_config(),
        "strategies": strategies,
    }


def audit_collector_for(ctx: Any) -> StrategyAuditCollector | None:
    """Return active per-strategy collector from engine context."""
    return getattr(ctx, "audit_collector", None)


def record_simple_attempt(
    collector: StrategyAuditCollector | None,
    *,
    reject_reason: str | None = None,
    pop_pct: float | None = None,
    **fields: Any,
) -> None:
    if collector is None:
        return
    collector.record_generated()
    if reject_reason:
        collector.record(reject_reason, **fields)
        collector.record_evaluation(
            outcome="rejected",
            reject_reason=reject_reason,
            pop_pct=pop_pct,
            **fields,
        )
    else:
        collector.record_stage("passed_liquidity")


def record_simple_winner(
    collector: StrategyAuditCollector | None,
    legs: list[Any],
    *,
    metrics: dict[str, Any],
    stages_passed: list[str] | None = None,
) -> None:
    if collector is None:
        return
    stages = stages_passed or ["passed_liquidity", "passed_pop", "returned"]
    for stage in stages:
        if stage != "returned":
            collector.record_stage(stage)
    collector.record_winner(
        candidate_id=candidate_id_for_legs(legs),
        legs=legs,
        metrics=metrics,
        stages_passed=stages,
        ranks={"final": 1},
    )
    collector.record_survivor_metrics(
        pop_pct=metrics.get("pop_pct"),
        credit=metrics.get("net_credit"),
        ann_return_pct=metrics.get("annualized_return_pct"),
    )


def setup_directional_audit(collector: StrategyAuditCollector | None) -> Any | None:
    """Initialize conviction audit state on the per-strategy collector."""
    if collector is None:
        return None
    from icici_breeze_backend.app.services.options_strategy_engine.delta_anchor import (
        CONVICTION_PROFILES,
        DELTA_TOLERANCE,
        DELTA_TOLERANCE_SEQUENCE,
        conviction_delta_templates,
    )
    from icici_breeze_backend.app.services.options_strategy_engine.strategies.directional._common import (
        DirectionalAuditState,
    )

    state = DirectionalAuditState()
    collector.directional_audit = {
        "conviction_config": {
            "profiles": list(CONVICTION_PROFILES),
            "delta_templates": conviction_delta_templates(),
            "delta_tolerance": DELTA_TOLERANCE,
            "delta_tolerance_sequence": list(DELTA_TOLERANCE_SEQUENCE),
        },
        "candidates_by_profile": state.candidates_by_profile,
        "profile_audits": state.profile_audits,
        "profile_winners": state.profile_winners,
        "shortlist_scores": state.shortlist_scores,
    }
    return state


def _record_directional_stage_once(
    collector: StrategyAuditCollector,
    *,
    candidate_id: str,
    stage: str,
) -> None:
    seen = collector._directional_stage_ids.setdefault(stage, set())
    if candidate_id in seen:
        return
    seen.add(candidate_id)
    collector.record_stage(stage)
    if stage == "generated":
        collector.combos_tried += 1


def record_directional_candidate_stage(
    collector: StrategyAuditCollector | None,
    *,
    candidate_id: str,
    stage: str,
    conviction_profile: str,
    reject_reason: str | None = None,
    pop_pct: float | None = None,
    **fields: Any,
) -> None:
    """Record a directional funnel stage once per unique candidate (cross-profile dedupe)."""
    if collector is None:
        return
    _record_directional_stage_once(collector, candidate_id=candidate_id, stage=stage)
    payload = {"conviction_profile": conviction_profile, "candidate_id": candidate_id, **fields}
    if reject_reason:
        collector.record(reject_reason, **payload)
        collector.record_evaluation(
            outcome="rejected",
            reject_reason=reject_reason,
            pop_pct=pop_pct,
            **payload,
        )
    elif stage == "passed_constraints":
        collector.record_evaluation(
            outcome="accepted",
            pop_pct=pop_pct,
            **payload,
        )


def record_directional_profile_winner(
    collector: StrategyAuditCollector | None,
    legs: list[Any],
    *,
    conviction_profile: str,
    metrics: dict[str, Any],
) -> None:
    if collector is None:
        return
    cid = candidate_id_for_legs(legs)
    _record_directional_stage_once(collector, candidate_id=cid, stage="returned")
    collector.record_winner(
        candidate_id=cid,
        legs=legs,
        metrics={**metrics, "conviction_profile": conviction_profile},
        stages_passed=["passed_liquidity", "passed_credit", "passed_constraints", "returned"],
        ranks={"final": 1, "conviction_profile": conviction_profile},
        increment_returned=False,
    )
    collector.record_survivor_metrics(
        pop_pct=metrics.get("pop_pct"),
        credit=metrics.get("net_credit"),
        ann_return_pct=metrics.get("annualized_return_pct"),
    )


def setup_income_audit(collector: StrategyAuditCollector | None) -> dict[str, Any] | None:
    """Initialize income-specific audit block on collector."""
    if collector is None:
        return None
    state: dict[str, Any] = {
        "search_behaviour": {
            "initial_pop_band": collector.pop_band_width,
            "final_pop_band": collector.pop_band_width,
            "expansion_attempts": 0,
            "full_chain_exhausted": False,
        },
        "objective_champions": [],
    }
    collector.income_audit = state
    return state


def record_income_search_behaviour(
    collector: StrategyAuditCollector | None,
    *,
    initial_pop_band: float,
    final_pop_band: float,
    expansion_attempts: int,
    full_chain_exhausted: bool,
) -> None:
    if collector is None or collector.income_audit is None:
        return
    collector.income_audit["search_behaviour"] = {
        "initial_pop_band": initial_pop_band,
        "final_pop_band": final_pop_band,
        "expansion_attempts": expansion_attempts,
        "full_chain_exhausted": full_chain_exhausted,
    }


def record_income_champion(
    collector: StrategyAuditCollector | None,
    *,
    candidate_id: str,
    badges: list[str],
    net_credit: float,
    annualized_return_pct: float,
    margin: float,
    pop: float,
) -> None:
    if collector is None or collector.income_audit is None:
        return
    collector.income_audit["objective_champions"].append(
        {
            "candidate_id": candidate_id,
            "badges": badges,
            "net_credit": round(net_credit, 4),
            "annualized_return_pct": round(annualized_return_pct, 2),
            "margin": round(margin, 2),
            "pop": round(pop, 2),
        }
    )

