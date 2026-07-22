"""Split strategy results into recommended vs relaxed compliance buckets."""
from __future__ import annotations

from icici_breeze_backend.app.services.options_strategy_engine.types import (
    UNDEFINED_RISK_STRATEGIES,
    EngineContext,
    StrategyResult,
)


def split_and_segregate_results(
    results: list[StrategyResult],
    ctx: EngineContext,
) -> tuple[list[StrategyResult], list[StrategyResult]]:
    """Partition ok results into recommended vs relaxed; move undefined-risk when capped."""
    recommended: list[StrategyResult] = []
    relaxed: list[StrategyResult] = []
    segregate_undefined = not ctx.allow_infinite_loss and ctx.max_loss_rupees is not None

    for res in results:
        if res.status != "ok" or not res.legs:
            recommended.append(res)
            continue
        if res.compliance == "relaxed":
            relaxed.append(res)
            continue
        if segregate_undefined and res.strategy_id in UNDEFINED_RISK_STRATEGIES:
            res.compliance = "relaxed"
            violations = list(res.constraint_violations or [])
            if "infinite_loss" not in violations:
                violations.append("infinite_loss")
            res.constraint_violations = violations
            if not res.ranking_summary:
                res.ranking_summary = (
                    "Unlimited-loss structure shown separately because a max-loss limit is set."
                )
            relaxed.append(res)
            continue
        recommended.append(res)

    return recommended, relaxed
