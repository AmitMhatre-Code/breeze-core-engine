"""Audit logging helpers for strategy engine decisions."""
from __future__ import annotations

from typing import Any

from icici_breeze_backend.audit.strategy_builder_audit import quote_row_to_audit
from icici_breeze_backend.app.services.options_strategy_engine.types import EngineContext, QuoteRow, Right


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
) -> None:
    if ctx.audit:
        ctx.audit.record_calculation(name, inputs, outputs, formula=formula, rationale=rationale)
