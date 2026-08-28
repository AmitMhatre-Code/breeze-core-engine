"""Resize proposed strategy legs to margin and max-loss budgets using one-lot SPAN.

Portfolio-aware (incremental) margin netting -- see
docs/strategy-builder-portfolio-margin-plan.md (D1-D10) -- adds a second,
netted one-lot probe alongside the existing standalone one-lot probe. When a
structure's netted incremental margin differs meaningfully from its
standalone margin (i.e. it actually overlaps the user's open positions), a
two-point secant against a second anchor point replaces the plain
`budget // unit_span` division, because incremental margin is NOT linear in
lot count -- it saturates once the offset from the existing position is used
up. Structures that don't overlap the book take the exact pre-netting path,
so a build with no open positions (or with `net_against_positions` off) is
byte-identical to before this change.
"""
from __future__ import annotations

import math
from typing import Any

from icici_breeze_backend.app.services.options_strategy_engine.helpers import (
    legs_to_margin_input,
    net_premium,
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

# A structure whose netted one-lot incremental margin is within this fraction of
# its standalone one-lot margin is treated as not overlapping the user's open
# positions at all -- skips the secant entirely and takes the exact pre-netting
# path. Keeps the common case (most candidates don't touch the existing book)
# to a single extra API call (the shared batch probe) rather than two.
_NO_OVERLAP_TOLERANCE_FRACTION = 0.02


def _secant_lots_for_budget(
    n1: int, i1: float, n2: int, i2: float, elm_per_lot: float, budget: float
) -> float | None:
    """Solve N in incr(N) + N*elm_per_lot <= budget, linearly interpolating
    incr() between the two known anchor points (n1, i1) and (n2, i2). ELM
    itself is exactly linear in lots (a flat per-short-lot charge), so only
    the incremental-margin term is approximated. This is a two-point
    approximation, not a convergence loop -- the caller's shrink check
    (attach_margins_and_returns) is the actual safety net against
    over-sizing, so precision here matters less than never raising.

    Returns `math.inf` when the margin+ELM slope is non-positive -- margin
    never binds as lots increase, so some other constraint (max-loss or
    premium outlay) must be the one that determines lot count. Returns
    `None` when the two anchor points imply DECREASING incremental margin as
    lots increase, which violates the documented invariant (incremental is
    monotonically non-decreasing in N) -- almost certainly a stale or noisy
    anchor probe; the caller falls back to the standalone path entirely
    rather than trust an approximation built on a broken premise.
    """
    if n2 == n1:
        return float(max(1, n1))
    slope = (i2 - i1) / (n2 - n1)
    if slope < 0:
        return None
    denom = slope + elm_per_lot
    if denom <= 0:
        return math.inf
    n = (budget - i1 + slope * n1) / denom
    return max(1.0, min(math.floor(n), float(n2)))


async def _netted_incremental_at_lots(
    proc: Any,
    user_id: str,
    exchange_code: str,
    stock_code: str,
    expiry_display: str,
    ctx: EngineContext,
    legs: list,
    lots: int,
) -> float | None:
    """One-off netted margin fetch at an arbitrary lot count -- used for the
    secant's second anchor point, which (unlike the batched one-lot probes)
    is only known once the one-lot incremental result is in hand."""
    import asyncio

    legs_n = legs_at_lots(legs, ctx.lot_size, lots=lots)
    margin_input = legs_to_margin_input(legs_n, stock_code, exchange_code, expiry_display)
    res = await asyncio.to_thread(
        proc.strategy_builder_margin,
        user_id,
        exchange_code,
        margin_input,
        existing_legs=ctx.netting_legs,
        existing_span_value=ctx.existing_span,
        netting_position_count=len(ctx.positions.rows) if ctx.positions is not None else 0,
    )
    if not isinstance(res, dict) or res.get("Status") != 200:
        return None
    try:
        return float((res.get("Success") or {}).get("span_margin_required"))
    except (TypeError, ValueError):
        return None


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
    netting_active = ctx.netting_available and bool(ctx.netting_legs)

    # --- Batch 1: standalone one-lot SPAN (unchanged from pre-netting). ---
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

    # --- Batch 2 (netting only): netted one-lot incremental SPAN. Only for
    # margin-bound (short-lot) candidates -- long-only sizing never touches
    # SPAN at all. Stored in a SEPARATE dict from unit_span_by_structure: the
    # two numbers answer different questions (standalone vs netted) for the
    # same structural key, and conflating them would silently corrupt sizing. ---
    if netting_active:
        netted_requests: list[MarginFetchRequest] = []
        netting_position_count = len(ctx.positions.rows) if ctx.positions is not None else 0
        for result in results:
            if result.status != "ok" or not result.legs:
                continue
            if short_lots_in_legs(result.legs, L) == 0:
                continue
            struct_key = structural_margin_key(result.legs)
            if struct_key in ctx.unit_incremental_by_structure:
                continue
            one_lot_legs = legs_at_lots(result.legs, L, lots=1)
            margin_input = legs_to_margin_input(
                one_lot_legs, stock_code, exchange_code, expiry_display
            )
            netted_requests.append(
                MarginFetchRequest(
                    cache_key=struct_key,
                    margin_input=margin_input,
                    strategy_id=result.strategy_id,
                    phase="unit_incremental_sizing",
                    existing_legs=ctx.netting_legs,
                    existing_span_value=ctx.existing_span,
                    netting_position_count=netting_position_count,
                )
            )
        if netted_requests:
            if ctx.progress is not None:
                ctx.progress.add_units(
                    len(netted_requests),
                    phase="margins",
                    message=f"Netting margins against open positions (0/{len(netted_requests)})…",
                )
            failed: set[tuple] = set()
            incrementals = await fetch_margins_concurrent(
                proc,
                user_id,
                exchange_code,
                netted_requests,
                audit=audit,
                existing_cache=ctx.unit_incremental_by_structure,
                progress=ctx.progress,
                failed_keys=failed,
            )
            # Only cache genuinely successful probes -- a failed one must stay
            # absent (not 0.0) so the decision pass below correctly treats it
            # as "netting unresolved for this structure" rather than silently
            # reading a failure as a fully-offset (incr=0.0) structure, which
            # would size lots against a benefit that was never verified (D7).
            ctx.unit_incremental_by_structure.update(
                {k: v for k, v in incrementals.items() if k not in failed}
            )

    # --- Decide, per margin-bound structure, whether netting actually
    # applies (meaningful overlap with the book) and if so what second
    # anchor lot count the secant needs. Structures needing an anchor are
    # batched into one more concurrent fetch before the final sizing pass,
    # so this stays a bounded number of round trips regardless of how many
    # candidates overlap the book. ---
    anchor_plan: dict[tuple, dict[str, Any]] = {}
    anchor_requests: list[MarginFetchRequest] = []
    if netting_active:
        netting_position_count = len(ctx.positions.rows) if ctx.positions is not None else 0
        for result in results:
            if result.status != "ok" or not result.legs:
                continue
            if short_lots_in_legs(result.legs, L) == 0:
                continue
            struct_key = structural_margin_key(result.legs)
            unit_span = ctx.unit_span_by_structure.get(struct_key, 0.0)
            if unit_span <= 0:
                continue
            incr1 = ctx.unit_incremental_by_structure.get(struct_key)
            if incr1 is None:
                continue  # netted probe failed for this structure -- falls back below
            if abs(incr1 - unit_span) <= _NO_OVERLAP_TOLERANCE_FRACTION * max(unit_span, 1.0):
                continue  # no meaningful overlap -- takes the standalone path

            unit_legs = legs_at_lots(result.legs, L, lots=1)
            elm1 = _elm_for_unit_legs(ctx, unit_legs)
            budget = ctx.margin_rupees
            if incr1 + elm1 <= 0:
                n_anchor = max(2, int(budget // max(unit_span + elm1, 1.0)))
            else:
                n_opt = int(budget // (incr1 + elm1))
                if n_opt <= 1:
                    anchor_plan[struct_key] = {
                        "struct_key": struct_key,
                        "incr1": incr1,
                        "elm1": elm1,
                        "direct_lots": max(0, n_opt),
                    }
                    continue
                n_anchor = n_opt

            anchor_key = (struct_key, "anchor", n_anchor)
            anchor_plan[struct_key] = {
                "struct_key": struct_key,
                "incr1": incr1,
                "elm1": elm1,
                "anchor_lots": n_anchor,
            }
            anchor_requests.append(
                MarginFetchRequest(
                    cache_key=anchor_key,
                    margin_input=legs_to_margin_input(
                        legs_at_lots(result.legs, L, lots=n_anchor),
                        stock_code,
                        exchange_code,
                        expiry_display,
                    ),
                    strategy_id=result.strategy_id,
                    phase="netted_anchor_sizing",
                    existing_legs=ctx.netting_legs,
                    existing_span_value=ctx.existing_span,
                    netting_position_count=netting_position_count,
                )
            )

    anchor_results: dict[tuple, float] = {}
    if anchor_requests:
        if ctx.progress is not None:
            ctx.progress.add_units(
                len(anchor_requests),
                phase="margins",
                message=f"Netting margins against open positions (0/{len(anchor_requests)})…",
            )
        anchor_failed: set[tuple] = set()
        raw_anchor_results = await fetch_margins_concurrent(
            proc,
            user_id,
            exchange_code,
            anchor_requests,
            audit=audit,
            progress=ctx.progress,
            failed_keys=anchor_failed,
        )
        # Same reasoning as the one-lot netted batch above: a failed anchor
        # probe must stay absent, not surface as a bogus incr=0.0.
        anchor_results = {
            k: v for k, v in raw_anchor_results.items() if k not in anchor_failed
        }

    for result in results:
        if result.status != "ok" or not result.legs:
            continue

        struct_key = structural_margin_key(result.legs)
        unit_max_loss = unit_max_loss_per_lot(result, L)
        unit_short_lots = short_lots_in_legs(result.legs, L)
        unit_legs = legs_at_lots(result.legs, L, lots=1)

        if unit_short_lots == 0:
            if unit_max_loss <= 0:
                result.status = "skipped"
                result.skip_reason = "Could not determine max loss per lot."
                result.legs = []
                continue
            n_margin = int(ctx.margin_rupees // unit_max_loss)
            if ctx.max_loss_rupees is None:
                lots = max(0, n_margin)
            else:
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

        plan = anchor_plan.get(struct_key)
        lots = None
        if plan is not None:
            lots = _resolve_netted_lots(
                plan, anchor_results, unit_max_loss, unit_legs, ctx,
            )

        if lots is None:
            # No netting plan (no overlap, or a probe failed) -- the exact
            # pre-netting path, byte-identical when netting isn't active.
            lots = size_lots(
                result.strategy_id,
                unit_span,
                unit_max_loss,
                margin_rupees=ctx.margin_rupees,
                max_loss_rupees=ctx.max_loss_rupees,
                lot_size=L,
                unit_legs=unit_legs,
                spot=ctx.spot,
                provision_elm=ctx.provision_elm,
                is_index=ctx.is_index,
                previous_close=ctx.previous_close,
                same_day_expiry=ctx.same_day_expiry,
            )
            netted_this_result = False
        else:
            netted_this_result = True

        if lots < 1:
            result.status = "skipped"
            result.skip_reason = (
                "Insufficient margin or max-loss budget for one lot after netting."
                if netted_this_result
                else "Insufficient margin or max-loss budget for one lot at SPAN."
            )
            result.legs = []
            continue

        rescale_result_to_lots(result, lot_size=L, lots=lots)
        result.netted_against_positions = netted_this_result
        if audit:
            audit.record_calculation(
                f"SPAN sizing ({result.strategy_id})",
                {
                    "unit_span_margin": unit_span,
                    "unit_short_lots": unit_short_lots,
                    "unit_max_loss_per_lot": unit_max_loss,
                    "margin_rupees": ctx.margin_rupees,
                    "max_loss_rupees": ctx.max_loss_rupees,
                    "netted_against_positions": netted_this_result,
                },
                {"lots": lots, "quantity": lots * L},
                rationale=(
                    "Dual-constraint sizing: min(margin, max_loss) using netted "
                    "incremental one-lot SPAN."
                    if netted_this_result
                    else "Dual-constraint sizing: min(margin, max_loss) using one-lot SPAN."
                ),
            )


def _elm_for_unit_legs(ctx: EngineContext, unit_legs: list) -> float:
    from icici_breeze_backend.app.services.options_strategy_engine.helpers import elm_addon

    return elm_addon(
        ctx.spot,
        ctx.lot_size,
        unit_legs,
        provision_elm=ctx.provision_elm,
        is_index=ctx.is_index,
        previous_close=ctx.previous_close,
        same_day_expiry=ctx.same_day_expiry,
    )


def _resolve_netted_lots(
    plan: dict[str, Any],
    anchor_results: dict[tuple, float],
    unit_max_loss: float,
    unit_legs: list,
    ctx: EngineContext,
) -> int | None:
    """Combine the margin-side secant result with the risk and premium-outlay
    constraints. Returns None when netting could not be resolved for this
    structure (caller falls back to the standalone path)."""
    incr1 = plan["incr1"]
    elm1 = plan["elm1"]

    if "direct_lots" in plan:
        margin_lots: float = plan["direct_lots"]
    else:
        struct_key = plan["struct_key"]
        n_anchor = plan["anchor_lots"]
        incr_anchor = anchor_results.get((struct_key, "anchor", n_anchor))
        if incr_anchor is None:
            return None
        solved = _secant_lots_for_budget(1, incr1, n_anchor, incr_anchor, elm1, ctx.margin_rupees)
        if solved is None:
            # Monotonicity violated -- don't trust this structure's netting
            # at all; caller falls back to the standalone path entirely.
            return None
        margin_lots = solved

    risk_lots: float = math.inf
    if ctx.max_loss_rupees is not None and unit_max_loss > 0:
        risk_lots = int(ctx.max_loss_rupees // unit_max_loss)

    nd1 = max(0.0, -net_premium(unit_legs))
    premium_lots: float = math.inf
    if nd1 > 0:
        premium_lots = int(ctx.margin_rupees // nd1)

    bound = min(margin_lots, risk_lots, premium_lots)
    if bound == math.inf:
        return None
    return max(0, int(bound))
