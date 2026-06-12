"""Resize proposed strategy legs to margin and max-loss budgets using one-lot SPAN."""
from __future__ import annotations

from typing import Any

from icici_breeze_backend.app.services.options_strategy_engine.helpers import (
    legs_to_margin_input,
    parse_float,
)
from icici_breeze_backend.app.services.options_strategy_engine.sizing import (
    legs_at_lots,
    rescale_result_to_lots,
    size_lots,
    structural_margin_key,
    unit_max_loss_per_lot,
)
from icici_breeze_backend.app.services.options_strategy_engine.types import EngineContext, StrategyResult


def resize_results_to_budgets(
    proc: Any,
    user_id: str,
    exchange_code: str,
    stock_code: str,
    expiry_display: str,
    results: list[StrategyResult],
    ctx: EngineContext,
    audit: Any | None = None,
) -> None:
    """Size each ok trade from one-lot SPAN margin and dual margin/max-loss budgets."""
    unit_span_cache: dict[tuple, float] = {}
    L = ctx.lot_size

    for result in results:
        if result.status != "ok" or not result.legs:
            continue

        struct_key = structural_margin_key(result.legs)
        if struct_key not in unit_span_cache:
            one_lot_legs = legs_at_lots(result.legs, L, lots=1)
            margin_input = legs_to_margin_input(
                one_lot_legs, stock_code, exchange_code, expiry_display
            )
            res = proc.strategy_builder_margin(
                user_id,
                exchange_code,
                margin_input,
                audit=audit,
                audit_context={
                    "strategy_id": result.strategy_id,
                    "legs": margin_input,
                    "phase": "unit_span_sizing",
                },
            )
            unit_span_cache[struct_key] = parse_float(
                (res.get("Success") or {}).get("span_margin_required")
            )

        unit_span = unit_span_cache[struct_key]
        if unit_span <= 0:
            result.status = "skipped"
            result.skip_reason = "Could not resolve SPAN margin for one lot."
            result.legs = []
            continue

        unit_max_loss = unit_max_loss_per_lot(result, L)
        lots = size_lots(
            result.strategy_id,
            unit_span,
            unit_max_loss,
            margin_rupees=ctx.margin_rupees,
            max_loss_rupees=ctx.max_loss_rupees,
            lot_size=L,
            leg_count=len(result.legs),
            spot=ctx.spot,
            provision_elm=ctx.provision_elm,
        )
        if lots < 1:
            result.status = "skipped"
            result.skip_reason = "Insufficient margin or max-loss budget for one lot at SPAN."
            result.legs = []
            continue

        rescale_result_to_lots(result, lot_size=L, lots=lots)
        if audit:
            audit.record_calculation(
                f"SPAN sizing ({result.strategy_id})",
                {
                    "unit_span_margin": unit_span,
                    "unit_max_loss_per_lot": unit_max_loss,
                    "margin_rupees": ctx.margin_rupees,
                    "max_loss_rupees": ctx.max_loss_rupees,
                },
                {"lots": lots, "quantity": lots * L},
                rationale="Dual-constraint sizing: min(margin, max_loss) using one-lot SPAN.",
            )
