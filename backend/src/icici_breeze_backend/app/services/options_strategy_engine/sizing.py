"""Dual-constraint position sizing (Gemini §6, OpenAI §11)."""
from __future__ import annotations

import math

from icici_breeze_backend.app.services.options_strategy_engine.helpers import elm_addon, floor_lots, net_premium
from icici_breeze_backend.app.services.options_strategy_engine.types import (
    UNDEFINED_RISK_STRATEGIES,
    StrategyResult,
    TradeLeg,
)


def size_lots(
    strategy_id: str,
    unit_span_margin: float,
    unit_max_loss: float,
    *,
    margin_rupees: float,
    max_loss_rupees: float,
    lot_size: int,
    leg_count: int,
    spot: float,
    provision_elm: bool,
) -> int:
    """Return number of lots (not contracts)."""
    short_lots_equiv = max(1, leg_count // 2) if leg_count > 1 else 1
    unit_elm = elm_addon(spot, lot_size, short_lots_equiv, provision_elm)
    total_unit_margin = unit_span_margin + unit_elm

    if total_unit_margin > 0:
        n_margin = int(margin_rupees // total_unit_margin)
    else:
        n_margin = 0

    if strategy_id in UNDEFINED_RISK_STRATEGIES:
        return max(0, n_margin)

    if unit_max_loss > 0:
        n_risk = int(max_loss_rupees // unit_max_loss)
    else:
        n_risk = 0

    return max(0, min(n_margin, n_risk))


def size_quantity_from_budgets(
    strategy_id: str,
    per_lot_margin_estimate: float,
    per_lot_max_loss: float,
    *,
    margin_rupees: float,
    max_loss_rupees: float,
    lot_size: int,
    leg_count: int,
    spot: float,
    provision_elm: bool,
) -> int:
    """Return contract quantity snapped to lot_size multiples."""
    lots = size_lots(
        strategy_id,
        per_lot_margin_estimate,
        per_lot_max_loss,
        margin_rupees=margin_rupees,
        max_loss_rupees=max_loss_rupees,
        lot_size=lot_size,
        leg_count=leg_count,
        spot=spot,
        provision_elm=provision_elm,
    )
    return lots * lot_size


def size_quantity_loss_only(
    max_loss_rupees: float,
    max_loss_per_lot: float,
    lot_size: int,
) -> int:
    return floor_lots(max_loss_rupees, max_loss_per_lot, lot_size)


def size_quantity_margin_only(
    margin_rupees: float,
    margin_per_lot: float,
    lot_size: int,
) -> int:
    return floor_lots(margin_rupees, margin_per_lot, lot_size)


def min_qty_for_one_lot(lot_size: int) -> int:
    return lot_size


def structural_margin_key(legs: list[TradeLeg]) -> tuple:
    return tuple(sorted((leg.right, leg.side, leg.strike) for leg in legs))


def legs_at_lots(legs: list[TradeLeg], lot_size: int, lots: int = 1) -> list[TradeLeg]:
    qty = lots * lot_size
    return [
        TradeLeg(leg.right, leg.side, leg.strike, qty, leg.premium_per_unit)
        for leg in legs
    ]


def unit_max_loss_per_lot(result: StrategyResult, lot_size: int) -> float:
    if result.max_loss is None or not result.legs or lot_size <= 0:
        return 0.0
    old_base = result.legs[0].quantity
    if old_base <= 0:
        return 0.0
    return result.max_loss / old_base * lot_size


def _update_risk_reward_after_scale(result: StrategyResult, scale: float) -> None:
    if result.risk_reward_ratio is None:
        return
    if result.max_loss is None:
        if result.net_premium is not None:
            result.risk_reward_ratio = f"Unlimited : {result.net_premium:.0f}"
        return
    parts = result.risk_reward_ratio.split(":")
    if len(parts) != 2:
        return
    try:
        gain = float(parts[1].strip()) * scale
        result.risk_reward_ratio = f"{result.max_loss:.0f} : {gain:.0f}"
    except ValueError:
        if result.net_premium is not None and result.net_premium > 0:
            result.risk_reward_ratio = f"{result.max_loss:.0f} : {result.net_premium:.0f}"


def rescale_result_to_lots(result: StrategyResult, *, lot_size: int, lots: int) -> None:
    """Scale leg quantities to `lots`, preserving per-leg quantity ratios."""
    if not result.legs or lot_size <= 0 or lots < 1:
        return
    L = lot_size
    old_base = result.legs[0].quantity
    if old_base <= 0:
        return
    ratios = [leg.quantity / old_base for leg in result.legs]
    new_base = lots * L
    scale = new_base / old_base
    for leg, ratio in zip(result.legs, ratios):
        leg_qty = int(round(new_base * ratio))
        leg.quantity = max(L, (leg_qty // L) * L) if leg_qty > 0 else 0
    if result.max_loss is not None:
        result.max_loss = round(result.max_loss * scale, 2)
    result.net_premium = net_premium(result.legs)
    _update_risk_reward_after_scale(result, scale)
