"""Resize proposed strategy legs to margin and max-loss budgets using one-lot SPAN."""
from __future__ import annotations

from typing import Any

from icici_breeze_backend.app.services.options_strategy_engine.helpers import (
    legs_to_margin_input,
    short_lots_in_legs,
)
from icici_breeze_backend.app.services.options_strategy_engine.margin_async_fetch import (
    MarginFetchRequest,
    fetch_margins_concurrent,
)
from icici_breeze_backend.app.services.options_strategy_engine.sizing import (
    legs_at_lots,
    rescale_result_to_lots,
    size_lots,
    structural_margin_key,
    unit_max_loss_per_lot,
)
from icici_breeze_backend.app.services.options_strategy_engine.types import EngineContext, StrategyResult


async def resize_results_to_budgets(
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
    L = ctx.lot_size

    margin_requests: list[MarginFetchRequest] = []
    for result in results:
        if result.status != "ok" or not result.legs:
            continue
        struct_key = structural_margin_key(result.legs)
        if struct_key in ctx.unit_span_by_structure:
            continue
        one_lot_legs = legs_at_lots(result.legs, L, lots=1)
        margin_input = legs_to_margin_input(
            one_lot_legs, stock_code, exchange_code, expiry_display
        )
        margin_requests.append(
            MarginFetchRequest(
                cache_key=struct_key,
                margin_input=margin_input,
                strategy_id=result.strategy_id,
                phase="unit_span_sizing",
            )
        )

    if margin_requests:
        if ctx.progress is not None:
            ctx.progress.add_units(
                len(margin_requests),
                phase="margins",
                message=f"Calculating margins (0/{len(margin_requests)})…",
            )
        spans = await fetch_margins_concurrent(
            proc,
            user_id,
            exchange_code,
            margin_requests,
            audit=audit,
            existing_cache=ctx.unit_span_by_structure,
            progress=ctx.progress,
        )
        ctx.unit_span_by_structure.update(spans)

    for result in results:
        if result.status != "ok" or not result.legs:
            continue

        struct_key = structural_margin_key(result.legs)
        unit_max_loss = unit_max_loss_per_lot(result, L)
        unit_short_lots = short_lots_in_legs(result.legs, L)

        if unit_short_lots == 0:
            if unit_max_loss <= 0:
                result.status = "skipped"
                result.skip_reason = "Could not determine max loss per lot."
                result.legs = []
                continue
            n_margin = int(ctx.margin_rupees // unit_max_loss)
            n_risk = int(ctx.max_loss_rupees // unit_max_loss)
            lots = max(0, min(n_margin, n_risk))
            if lots < 1:
                result.status = "skipped"
                result.skip_reason = "Insufficient margin or max-loss budget for one lot."
                result.legs = []
                continue
            rescale_result_to_lots(result, lot_size=L, lots=lots)
            if audit:
                audit.record_calculation(
                    f"Premium sizing ({result.strategy_id})",
                    {
                        "unit_max_loss_per_lot": unit_max_loss,
                        "margin_rupees": ctx.margin_rupees,
                        "max_loss_rupees": ctx.max_loss_rupees,
                    },
                    {"lots": lots, "quantity": lots * L},
                    rationale="Long-only sizing: min(margin, max_loss) using premium per lot.",
                )
            continue

        unit_span = ctx.unit_span_by_structure.get(struct_key, 0.0)
        if unit_span <= 0:
            result.status = "skipped"
            result.skip_reason = "Could not resolve SPAN margin for one lot."
            result.legs = []
            continue

        lots = size_lots(
            result.strategy_id,
            unit_span,
            unit_max_loss,
            margin_rupees=ctx.margin_rupees,
            max_loss_rupees=ctx.max_loss_rupees,
            lot_size=L,
            unit_short_lots=unit_short_lots,
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
                    "unit_short_lots": unit_short_lots,
                    "unit_max_loss_per_lot": unit_max_loss,
                    "margin_rupees": ctx.margin_rupees,
                    "max_loss_rupees": ctx.max_loss_rupees,
                },
                {"lots": lots, "quantity": lots * L},
                rationale="Dual-constraint sizing: min(margin, max_loss) using one-lot SPAN.",
            )
