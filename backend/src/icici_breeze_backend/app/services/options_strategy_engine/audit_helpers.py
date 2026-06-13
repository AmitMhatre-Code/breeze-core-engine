"""Audit logging helpers for strategy engine decisions."""
from __future__ import annotations

import time
from typing import Any

from icici_breeze_backend.audit.strategy_builder_audit import quote_row_to_audit
from icici_breeze_backend.audit.strategy_evaluation_audit import StrategyAuditCollector
from icici_breeze_backend.app.services.options_strategy_engine.types import EngineContext, QuoteRow, Right


def begin_strategy_audit(ctx: EngineContext, strategy_id: str) -> StrategyAuditCollector | None:
    if not ctx.audit:
        return None
    collector = ctx.audit.begin_strategy_collector(strategy_id)
    collector.min_pop_pct = ctx.min_pop_pct
    ctx.audit_collector = collector
    collector.begin_generation()
    return collector


def end_strategy_audit(
    ctx: EngineContext,
    collector: StrategyAuditCollector | None,
    *,
    status: str,
    skip_reason: str | None = None,
) -> None:
    if collector is None:
        return
    collector.end_generation(ctx.audit.telemetry if ctx.audit else None)
    collector.end_ranking(ctx.audit.telemetry if ctx.audit else None)
    collector.set_status(status, skip_reason)
    if ctx.audit:
        ctx.audit.finish_strategy_collector(collector)
    ctx.audit_collector = None


def audit_decision(
    ctx: EngineContext,
    decision: str,
    outcome: str,
    rationale: str,
    details: dict[str, Any] | None = None,
) -> None:
    if ctx.audit:
        ctx.audit.record_decision(decision, outcome, rationale=rationale, details=details)


def audit_calc(
    ctx: EngineContext,
    name: str,
    inputs: dict[str, Any],
    outputs: dict[str, Any],
    *,
    formula: str | None = None,
    rationale: str | None = None,
    strategy_id: str | None = None,
) -> None:
    if ctx.audit:
        ctx.audit.record_calculation(
            name,
            inputs,
            outputs,
            formula=formula,
            rationale=rationale,
            strategy_id=strategy_id,
        )


class StrategyTiming:
    """Context manager for per-strategy execution timing."""

    def __init__(self, ctx: EngineContext, strategy_id: str) -> None:
        self._ctx = ctx
        self._strategy_id = strategy_id
        self._start = 0.0

    def __enter__(self) -> "StrategyTiming":
        self._start = time.perf_counter()
        return self

    def __exit__(self, *args: object) -> None:
        if self._ctx.audit:
            elapsed = (time.perf_counter() - self._start) * 1000
            self._ctx.audit.telemetry.strategy_execution_ms[self._strategy_id] = round(elapsed, 2)
