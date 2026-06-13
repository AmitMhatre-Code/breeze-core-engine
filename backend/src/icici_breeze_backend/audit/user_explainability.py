"""User-facing explainability report derived from Strategy Builder audit data."""
from __future__ import annotations

import re
from typing import Any

from icici_breeze_backend.app.services.options_strategy_engine.strategies.income._common import (
    BADGE_CAPITAL,
    BADGE_INCOME,
    BADGE_MARGIN,
)
from icici_breeze_backend.app.services.options_strategy_engine.types import STRATEGY_CATALOG
from icici_breeze_backend.audit.strategy_evaluation_audit import canonical_rejection_reason

USER_REPORT_SCHEMA_VERSION = "1.0"
MAX_INSIGHTS = 8
POP_NEAR_FLOOR_BAND = 2.0

STRATEGY_NAME_BY_ID: dict[str, str] = dict(STRATEGY_CATALOG)

BADGE_RATIONALE: dict[str, str] = {
    BADGE_INCOME: (
        "Highest net credit among SPAN-scored candidates meeting your minimum annual return."
    ),
    BADGE_CAPITAL: (
        "Highest annualized return on SPAN margin among feasible candidates."
    ),
    BADGE_MARGIN: (
        "Lowest SPAN margin requirement among feasible candidates."
    ),
}

FUNNEL_LABELS: list[tuple[str, str]] = [
    ("candidates_generated", "Candidates generated"),
    ("passed_pop", "Passed PoP"),
    ("passed_capital_loss_liquidity", "Passed capital/loss/liquidity checks"),
    ("margin_verified", "Margin verified"),
    ("recommended", "Recommended"),
]

CAPITAL_LIQUIDITY_STAGES = (
    "passed_liquidity",
    "passed_credit",
    "passed_constraints",
    "passed_economic_prune",
    "passed_capital",
    "passed_loss",
)


def build_user_explainability_report(
    *,
    request: dict[str, Any],
    strategy_evaluations: dict[str, dict[str, Any]],
    trades: list[dict[str, Any]],
    summary: dict[str, Any],
) -> dict[str, Any]:
    """Build a user-friendly explainability report from audit + trade output."""
    min_pop = float(request.get("min_pop_pct") or 0)
    min_roi = float(request.get("min_ann_return_pct") or 0)
    margin_lacs = float(request.get("margin_lacs") or 0)
    max_loss_lacs = float(request.get("max_loss_lacs") or 0)
    category = str(request.get("strategy_category") or "")

    ok_trades = [t for t in trades if t.get("status") == "ok"]
    skipped_trades = [t for t in trades if t.get("status") == "skipped"]
    recommended_ids = sorted({str(t["strategy_id"]) for t in ok_trades if t.get("strategy_id")})

    executive = _build_executive_summary(
        request=request,
        category=category,
        margin_lacs=margin_lacs,
        max_loss_lacs=max_loss_lacs,
        min_pop=min_pop,
        min_roi=min_roi,
        strategy_evaluations=strategy_evaluations,
        ok_trades=ok_trades,
        recommended_ids=recommended_ids,
        skipped_trades=skipped_trades,
        summary=summary,
    )
    why_this = _build_why_this(ok_trades, strategy_evaluations)
    why_not = _build_why_not(
        skipped_trades,
        strategy_evaluations,
        min_pop=min_pop,
        min_roi=min_roi,
        margin_lacs=margin_lacs,
        max_loss_lacs=max_loss_lacs,
    )
    what_if = _build_what_if_insights(
        ok_trades=ok_trades,
        skipped_trades=skipped_trades,
        strategy_evaluations=strategy_evaluations,
        min_pop=min_pop,
        min_roi=min_roi,
        margin_lacs=margin_lacs,
        max_loss_lacs=max_loss_lacs,
    )

    return {
        "user_report_schema_version": USER_REPORT_SCHEMA_VERSION,
        "executive_summary": executive,
        "why_this": why_this,
        "why_not": why_not,
        "what_if_insights": what_if,
    }


def strategy_display_name(strategy_id: str, trade: dict[str, Any] | None = None) -> str:
    if trade and trade.get("strategy_name"):
        return str(trade["strategy_name"])
    return STRATEGY_NAME_BY_ID.get(strategy_id, strategy_id.replace("_", " ").title())


def _build_executive_summary(
    *,
    request: dict[str, Any],
    category: str,
    margin_lacs: float,
    max_loss_lacs: float,
    min_pop: float,
    min_roi: float,
    strategy_evaluations: dict[str, dict[str, Any]],
    ok_trades: list[dict[str, Any]],
    recommended_ids: list[str],
    skipped_trades: list[dict[str, Any]],
    summary: dict[str, Any],
) -> dict[str, Any]:
    evaluated_count = len(strategy_evaluations) or len(summary.get("strategies_ok", [])) + len(
        summary.get("strategies_skipped", [])
    )

    skipped_out: list[dict[str, Any]] = []
    for trade in skipped_trades:
        sid = str(trade.get("strategy_id") or "")
        ev = strategy_evaluations.get(sid, {})
        primary = _primary_skip_reason(ev, trade.get("skip_reason"))
        skipped_out.append(
            {
                "strategy_id": sid,
                "strategy_name": strategy_display_name(sid, trade),
                "primary_reason": primary,
                "summary": _skip_one_liner(
                    strategy_display_name(sid, trade),
                    primary,
                    ev,
                    trade.get("skip_reason"),
                    min_pop=min_pop,
                    min_roi=min_roi,
                    margin_lacs=margin_lacs,
                    max_loss_lacs=max_loss_lacs,
                ),
            }
        )

    user_inputs: dict[str, Any] = {
        "strategy_category": category,
        "margin_lacs": margin_lacs,
        "max_loss_lacs": max_loss_lacs,
    }
    if category == "income":
        user_inputs["min_pop_pct"] = min_pop
        user_inputs["min_ann_return_pct"] = min_roi

    return {
        "user_inputs": user_inputs,
        "strategies_evaluated": evaluated_count,
        "strategies_recommended": [
            {
                "strategy_id": sid,
                "strategy_name": strategy_display_name(
                    sid,
                    next((t for t in ok_trades if t.get("strategy_id") == sid), None),
                ),
            }
            for sid in recommended_ids
        ],
        "strategies_skipped": skipped_out,
    }


def _build_why_this(
    ok_trades: list[dict[str, Any]],
    strategy_evaluations: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    by_strategy: dict[str, list[dict[str, Any]]] = {}
    for trade in ok_trades:
        sid = str(trade.get("strategy_id") or "")
        by_strategy.setdefault(sid, []).append(trade)

    out: list[dict[str, Any]] = []
    for sid in sorted(by_strategy.keys()):
        trades_for_sid = by_strategy[sid]
        ev = strategy_evaluations.get(sid, {})
        pop_policy = ev.get("pop_policy") or {}
        pop_ignored = bool(pop_policy.get("ignored"))
        funnel = _build_funnel(ev, pop_ignored=pop_ignored)

        returned: list[dict[str, Any]] = []
        for trade in sorted(
            trades_for_sid,
            key=lambda t: (t.get("variant_rank") or 0, t.get("strategy_name") or ""),
        ):
            badges = list(trade.get("badges") or [])
            badge_explanations = [
                {"badge": b, "rationale": BADGE_RATIONALE.get(b, "")} for b in badges if b
            ]
            returned.append(
                {
                    "strategy_name": strategy_display_name(sid, trade),
                    "variant_rank": trade.get("variant_rank"),
                    "conviction_profile": trade.get("conviction_profile"),
                    "badges": badges,
                    "badge_explanations": badge_explanations,
                    "metrics": _trade_metrics(trade),
                    "ranking_summary": trade.get("ranking_summary"),
                }
            )

        out.append(
            {
                "strategy_id": sid,
                "strategy_name": strategy_display_name(sid, trades_for_sid[0]),
                "funnel": funnel,
                "pop_filter_note": (
                    "PoP is informational for this strategy type; not used as a filter."
                    if pop_ignored
                    else None
                ),
                "returned_trades": returned,
            }
        )
    return out


def _build_funnel(ev: dict[str, Any], *, pop_ignored: bool) -> list[dict[str, Any]]:
    summary = ev.get("strategy_summary") or {}
    stages: list[dict[str, Any]] = []

    def _count(key: str) -> int | str:
        if key == "passed_pop" and pop_ignored:
            return "not_applied"
        if key == "passed_capital_loss_liquidity":
            vals = [int(summary.get(s, 0) or 0) for s in CAPITAL_LIQUIDITY_STAGES]
            return min(vals) if vals else 0
        return int(summary.get(key, 0) or 0)

    stage_key_map = {
        "candidates_generated": "generated",
        "passed_pop": "passed_pop",
        "passed_capital_loss_liquidity": None,
        "margin_verified": "margin_refined",
        "recommended": "returned",
    }

    for label_key, label in FUNNEL_LABELS:
        raw_key = stage_key_map[label_key]
        if label_key == "passed_capital_loss_liquidity":
            count = _count(label_key)
        elif raw_key:
            count = int(summary.get(raw_key, 0) or 0)
            if label_key == "passed_pop" and pop_ignored:
                count = "not_applied"
        else:
            count = 0
        stages.append({"stage": label_key, "label": label, "count": count})
    return stages


def _trade_metrics(trade: dict[str, Any]) -> dict[str, Any]:
    return {
        "pop_pct": _round_metric(trade.get("pop_pct")),
        "net_credit": _round_metric(trade.get("net_premium")),
        "annualized_return_pct": _round_metric(trade.get("annualized_return_pct")),
        "margin": _round_metric(trade.get("span_margin")),
    }


def _round_metric(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _build_why_not(
    skipped_trades: list[dict[str, Any]],
    strategy_evaluations: dict[str, dict[str, Any]],
    *,
    min_pop: float,
    min_roi: float,
    margin_lacs: float,
    max_loss_lacs: float,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for trade in skipped_trades:
        sid = str(trade.get("strategy_id") or "")
        name = strategy_display_name(sid, trade)
        ev = strategy_evaluations.get(sid, {})
        primary = _primary_skip_reason(ev, trade.get("skip_reason"))
        explanation = _skip_explanation(
            name=name,
            primary=primary,
            ev=ev,
            skip_reason=trade.get("skip_reason"),
            min_pop=min_pop,
            min_roi=min_roi,
            margin_lacs=margin_lacs,
            max_loss_lacs=max_loss_lacs,
        )
        out.append(
            {
                "strategy_id": sid,
                "strategy_name": name,
                "primary_reason": primary,
                "explanation": explanation,
                "funnel": _build_funnel(
                    ev,
                    pop_ignored=bool((ev.get("pop_policy") or {}).get("ignored")),
                ),
            }
        )
    return out


def _primary_skip_reason(ev: dict[str, Any], skip_reason: Any) -> str:
    funnel = ev.get("rejection_funnel") or {}
    if funnel:
        return max(funnel.items(), key=lambda x: x[1])[0]
    near = (ev.get("near_misses") or [])
    if near:
        return canonical_rejection_reason(near[0].get("rejection_reason"))
    if skip_reason:
        for token in ("pop", "PoP", "annual", "margin", "capital", "loss", "liquid"):
            if token.lower() in str(skip_reason).lower():
                if "pop" in str(skip_reason).lower():
                    return "pop_floor"
                if "annual" in str(skip_reason).lower() or "return" in str(skip_reason).lower():
                    return "min_ann_return"
                if "margin" in str(skip_reason).lower() or "capital" in str(skip_reason).lower():
                    return "capital"
                if "loss" in str(skip_reason).lower():
                    return "max_loss"
                if "liquid" in str(skip_reason).lower():
                    return "liquidity"
    return "other"


def _best_pop_below_floor(ev: dict[str, Any], min_pop: float) -> float | None:
    best: float | None = None
    for nm in ev.get("near_misses") or []:
        metrics = nm.get("metrics") or {}
        pop = metrics.get("pop_pct")
        if pop is not None:
            pf = float(pop)
            if pf < min_pop and (best is None or pf > best):
                best = pf
    for winner in ev.get("winners") or []:
        metrics = winner.get("metrics") or {}
        pop = metrics.get("pop_pct")
        if pop is not None:
            pf = float(pop)
            if pf < min_pop and (best is None or pf > best):
                best = pf

    bucket_map = ev.get("rejection_funnel_by_pop_bucket") or {}
    floor_prefix = f"<{min_pop:.0f}"
    if floor_prefix in bucket_map and bucket_map[floor_prefix]:
        return min_pop - 0.1

    dist = (ev.get("distributions") or {}).get("pop_pct") or {}
    for label in dist:
        if label.startswith("<"):
            try:
                val = float(label.lstrip("<"))
                if best is None or val > best:
                    best = val
            except ValueError:
                pass
    return round(best, 1) if best is not None else None


def _best_ann_return_below_floor(ev: dict[str, Any], min_roi: float) -> float | None:
    best: float | None = None
    for nm in ev.get("near_misses") or []:
        if nm.get("rejection_reason") != "below_min_ann_return":
            continue
        metrics = nm.get("metrics") or {}
        roi = metrics.get("annualized_return_pct")
        if roi is not None:
            rf = float(roi)
            if rf < min_roi and (best is None or rf > best):
                best = rf
        ctx = nm.get("context") or ""
        match = re.search(r"Best annualized return\s+([\d.]+)%", ctx)
        if match:
            rf = float(match.group(1))
            if rf < min_roi and (best is None or rf > best):
                best = rf
    return round(best, 2) if best is not None else None


def _skip_one_liner(
    name: str,
    primary: str,
    ev: dict[str, Any],
    skip_reason: Any,
    *,
    min_pop: float,
    min_roi: float,
    margin_lacs: float,
    max_loss_lacs: float,
) -> str:
    return _skip_explanation(
        name=name,
        primary=primary,
        ev=ev,
        skip_reason=skip_reason,
        min_pop=min_pop,
        min_roi=min_roi,
        margin_lacs=margin_lacs,
        max_loss_lacs=max_loss_lacs,
    )


def _skip_explanation(
    *,
    name: str,
    primary: str,
    ev: dict[str, Any],
    skip_reason: Any,
    min_pop: float,
    min_roi: float,
    margin_lacs: float,
    max_loss_lacs: float,
) -> str:
    if primary == "pop_floor":
        best = _best_pop_below_floor(ev, min_pop)
        if best is not None:
            return (
                f"{name} was not recommended because its best PoP of {best:.1f}% "
                f"was below your required {min_pop:.0f}%."
            )
        return (
            f"{name} was evaluated, but none of the candidates met your "
            f"minimum PoP requirement of {min_pop:.0f}%."
        )
    if primary == "min_ann_return":
        best = _best_ann_return_below_floor(ev, min_roi)
        if best is not None:
            return (
                f"{name} had feasible candidates, but none met your minimum "
                f"{min_roi:.1f}% annual return (best was {best:.1f}%)."
            )
        return (
            f"{name} had feasible candidates, but none met your minimum "
            f"{min_roi:.1f}% annual return requirement."
        )
    if primary in ("capital", "budget"):
        return f"{name} candidates exceeded your ₹{margin_lacs:g}L capital budget."
    if primary in ("max_loss", "economic_prune"):
        return f"{name} structures exceeded your ₹{max_loss_lacs:g}L max-loss limit."
    if primary == "liquidity":
        return f"{name} was evaluated, but liquid strikes did not produce viable structures."
    if skip_reason:
        return str(skip_reason)
    generated = int((ev.get("strategy_summary") or {}).get("generated", 0) or 0)
    if generated == 0:
        return f"{name} was evaluated, but no candidates could be generated on the liquid chain."
    return f"{name} was not recommended after evaluation."


def _build_what_if_insights(
    *,
    ok_trades: list[dict[str, Any]],
    skipped_trades: list[dict[str, Any]],
    strategy_evaluations: dict[str, dict[str, Any]],
    min_pop: float,
    min_roi: float,
    margin_lacs: float,
    max_loss_lacs: float,
) -> list[dict[str, Any]]:
    insights: list[dict[str, Any]] = []
    seen_messages: set[str] = set()

    def _add(insight: dict[str, Any]) -> None:
        msg = insight.get("message") or ""
        if not msg or msg in seen_messages or len(insights) >= MAX_INSIGHTS:
            return
        seen_messages.add(msg)
        insights.append(insight)

    for trade in skipped_trades:
        sid = str(trade.get("strategy_id") or "")
        name = strategy_display_name(sid, trade)
        ev = strategy_evaluations.get(sid, {})
        primary = _primary_skip_reason(ev, trade.get("skip_reason"))
        pop_policy = ev.get("pop_policy") or {}

        if primary == "pop_floor" and pop_policy.get("used_for_filtering"):
            best = _best_pop_below_floor(ev, min_pop)
            if best is not None:
                _add(
                    {
                        "constraint": "min_pop_pct",
                        "current_value": min_pop,
                        "suggested_change": round(best - 0.1, 1),
                        "affected_strategies": [sid],
                        "message": (
                            f"Reducing your PoP threshold below {best:.1f}% "
                            f"would make {name} eligible."
                        ),
                    }
                )

        if primary == "min_ann_return":
            best = _best_ann_return_below_floor(ev, min_roi)
            if best is not None:
                _add(
                    {
                        "constraint": "min_ann_return_pct",
                        "current_value": min_roi,
                        "suggested_change": round(best, 1),
                        "affected_strategies": [sid],
                        "message": (
                            f"Lowering your minimum annual return below {best:.1f}% "
                            f"would make {name} eligible."
                        ),
                    }
                )

        if primary in ("capital", "budget"):
            _add(
                {
                    "constraint": "margin_lacs",
                    "current_value": margin_lacs,
                    "suggested_change": None,
                    "affected_strategies": [sid],
                    "message": (
                        f"Increasing your capital budget above ₹{margin_lacs:g}L "
                        f"may make {name} eligible."
                    ),
                }
            )

        if primary in ("max_loss", "economic_prune"):
            _add(
                {
                    "constraint": "max_loss_lacs",
                    "current_value": max_loss_lacs,
                    "suggested_change": None,
                    "affected_strategies": [sid],
                    "message": (
                        f"Increasing your max-loss limit above ₹{max_loss_lacs:g}L "
                        f"may make {name} eligible."
                    ),
                }
            )

    for trade in ok_trades:
        sid = str(trade.get("strategy_id") or "")
        name = strategy_display_name(sid, trade)
        pop = trade.get("pop_pct")
        if pop is None:
            continue
        pop_f = float(pop)
        badges = trade.get("badges") or []
        badge_label = badges[0] if badges else name
        if min_pop > 0 and pop_f >= min_pop and (pop_f - min_pop) <= POP_NEAR_FLOOR_BAND:
            threshold = round(pop_f + 0.1, 1)
            _add(
                {
                    "constraint": "min_pop_pct",
                    "current_value": min_pop,
                    "suggested_change": threshold,
                    "affected_strategies": [sid],
                    "message": (
                        f"Increasing your PoP threshold above {threshold:.1f}% "
                        f"would eliminate the {badge_label} {name}."
                    ),
                }
            )

        if pop_f > min_pop + POP_NEAR_FLOOR_BAND:
            _add(
                {
                    "constraint": "min_pop_pct",
                    "current_value": min_pop,
                    "suggested_change": round(pop_f + 0.1, 1),
                    "affected_strategies": [sid],
                    "message": (
                        f"Increasing your PoP threshold above {pop_f:.1f}% "
                        f"would eliminate the {badge_label} {name}."
                    ),
                }
            )

    return insights


def split_report_into_levels(report: dict[str, Any]) -> dict[str, Any]:
    """Split a full user report into persisted Level 1–3 slices."""
    return {
        "schema_version": USER_REPORT_SCHEMA_VERSION,
        "level_1": report["executive_summary"],
        "level_2": {
            "why_this": report["why_this"],
            "why_not": report["why_not"],
        },
        "level_3": report["what_if_insights"],
    }


def levels_from_user_explainability(user_explainability: dict[str, Any]) -> dict[str, Any]:
    """Derive level slices from stored user_explainability block."""
    if "level_1" in user_explainability:
        return user_explainability
    return split_report_into_levels(user_explainability)


def _synthetic_trades_from_audit_doc(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Build minimal trade list from audit summary + strategy_evaluations winners."""
    trades: list[dict[str, Any]] = []
    summary = doc.get("summary") or {}
    evaluations = doc.get("strategy_evaluations") or {}

    for item in summary.get("strategies_skipped") or []:
        if not isinstance(item, dict):
            continue
        sid = item.get("strategy_id")
        if not sid:
            continue
        trades.append(
            {
                "strategy_id": sid,
                "strategy_name": strategy_display_name(str(sid)),
                "status": "skipped",
                "skip_reason": item.get("skip_reason"),
            }
        )

    ok_ids = set(summary.get("strategies_ok") or [])
    for sid in ok_ids:
        sid_str = str(sid)
        ev = evaluations.get(sid_str) or {}
        winners = ev.get("winners") or []
        if winners:
            for idx, winner in enumerate(winners):
                metrics = winner.get("metrics") or {}
                trades.append(
                    {
                        "strategy_id": sid_str,
                        "strategy_name": strategy_display_name(sid_str),
                        "status": "ok",
                        "badges": metrics.get("badges") or [],
                        "pop_pct": metrics.get("pop_pct"),
                        "net_premium": metrics.get("net_collected") or metrics.get("net_credit"),
                        "annualized_return_pct": metrics.get("annualized_return_pct"),
                        "span_margin": metrics.get("margin"),
                        "variant_rank": idx + 1 if len(winners) > 1 else None,
                    }
                )
        else:
            trades.append(
                {
                    "strategy_id": sid_str,
                    "strategy_name": strategy_display_name(sid_str),
                    "status": "ok",
                }
            )

    return trades


def resolve_explainability_from_audit_doc(doc: dict[str, Any]) -> dict[str, Any] | None:
    """Return Level 1–3 slices from audit doc, rebuilding if necessary."""
    stored_levels = doc.get("explainability_levels")
    if isinstance(stored_levels, dict) and stored_levels.get("level_1") is not None:
        return stored_levels

    user_explainability = doc.get("user_explainability")
    if isinstance(user_explainability, dict):
        return levels_from_user_explainability(user_explainability)

    evaluations = doc.get("strategy_evaluations")
    if not evaluations:
        return None

    request = doc.get("request") or {}
    summary = doc.get("summary") or {}
    trades = _synthetic_trades_from_audit_doc(doc)
    report = build_user_explainability_report(
        request=request,
        strategy_evaluations=evaluations,
        trades=trades,
        summary=summary,
    )
    return split_report_into_levels(report)


def explainability_available_for_doc(doc: dict[str, Any]) -> bool:
    """True when Level 1–3 can be loaded or rebuilt from this audit doc."""
    if doc.get("explainability_levels") or doc.get("user_explainability"):
        return True
    return bool(doc.get("strategy_evaluations"))
