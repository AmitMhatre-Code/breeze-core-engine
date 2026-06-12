"""Dual-constraint position sizing (Gemini §6, OpenAI §11)."""
from __future__ import annotations

import math

from icici_breeze_backend.app.services.options_strategy_engine.helpers import elm_addon, floor_lots
from icici_breeze_backend.app.services.options_strategy_engine.types import UNDEFINED_RISK_STRATEGIES


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
