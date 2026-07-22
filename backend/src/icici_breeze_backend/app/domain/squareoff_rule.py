"""Request/response schemas for Strategy Group (SG) profit/loss square-off rules.

One record == one Strategy Group instance: every open leg for a (stock_code,
expiry_display). Exactly one is non-terminal per key at a time (DB-enforced, see
`db/squareoff_rules_migrate`). `Active` (an SG with no PB/SL) is derived, not stored --
it is simply the absence of a non-terminal record.
"""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

# `triggered` is an internal sub-second transient (guards double-fire) and renders as
# Armed. `fire_failed` is retired -- it folded into `reset`, which carries a reason.
SquareOffRuleStatus = Literal[
    "armed", "triggered", "fired", "completed", "reset", "disarmed"
]

# Derived from the live order book + positions, never stored (see `hazard_tier`).
#   settled       - no live orphaned exit orders; nothing at stake
#   orders_live   - live orphans that still correctly close open legs
#   contra_risk   - an orphan whose leg is already closed: filling OPENS a new position
ResetHazardTier = Literal["settled", "orders_live", "contra_risk"]


class ArmSquareOffRuleRequest(BaseModel):
    stock_code: str
    expiry_date: str = Field(..., description="Display format DD-Mon-YYYY")
    exchange_code: str = "NFO"
    profit_target_pnl: float = Field(..., gt=0)
    loss_limit_pnl: float = Field(..., gt=0)
    target_premium_pct: int = Field(
        ..., ge=1, le=20, description="% above/below LTP for the profit-booking exit limit order"
    )
    stop_loss_premium_pct: int = Field(
        ..., ge=1, le=20, description="% above/below LTP for the stop-loss exit limit order"
    )


class SquareOffRuleLegResult(BaseModel):
    scrip_key: str
    stock_code: str
    strike_price: str
    right: str
    quantity: str
    status: Literal["success", "partial", "failed"]
    error: Optional[str] = None
    order_id: Optional[str] = None
    """Legacy singular field, kept for rules fired before order_ids existed (only ever one order per leg then)."""
    order_ids: List[str] = Field(default_factory=list)
    action: Optional[str] = None
    price: Optional[str] = None

    @model_validator(mode="after")
    def _normalize_order_ids(self):
        if not self.order_ids and self.order_id:
            self.order_ids = [self.order_id]
        return self


class SquareOffRuleLiveLeg(BaseModel):
    """One currently-open leg of an armed group rule's (stock_code,
    expiry_display) bucket, joined live from the P&L engine's position
    registry — not persisted with the rule itself. `action` is the
    position's own entry side (BUY/SELL), unlike `SquareOffRuleLegResult
    .action` which is the closing order's (inverted) side."""

    scrip_key: str
    stock_code: str
    strike_price: float
    right: str
    quantity: int
    action: str
    average_price: float


class SquareOffRuleOrphanOrder(BaseModel):
    """One of this SG's exit orders that is still live at the exchange after the SG was
    Reset. Derived from the order book, never stored.

    Reset withdraws future automation; it does not retract orders already placed (see
    `strategy_group_lifecycle`). So these keep working, and the user has to be told --
    especially `opens_contra_position`, where the leg is already closed and a fill would
    open a brand-new position rather than close anything."""

    order_id: str
    stock_code: str
    strike_price: str
    right: str
    action: str
    quantity: str
    price: Optional[str] = None
    opens_contra_position: bool = False


class SquareOffRuleRecord(BaseModel):
    id: str
    stock_code: str
    expiry_display: str
    exchange_code: str
    profit_target_pnl: float
    loss_limit_pnl: float
    target_premium_pct: int
    stop_loss_premium_pct: int
    status: SquareOffRuleStatus
    leg_results: Optional[List[SquareOffRuleLegResult]] = None
    live_legs: Optional[List[SquareOffRuleLiveLeg]] = None
    created_at: Optional[str] = None
    fired_at: Optional[str] = None
    resolved_at: Optional[str] = None
    """When the SG reached Completed/Reset."""
    reset_reason: Optional[str] = None
    """Why monitoring stopped, in the user's own terms. Required for every Reset --
    spec sections 8/9/10 all mandate an explanation."""
    legs_snapshot: Optional[dict[str, int]] = None
    """scrip_key -> quantity, captured at arm time. Powers composition-drift detection
    and is quantity-sensitive: the thresholds were set against a specific exposure."""
    hazard_tier: Optional[ResetHazardTier] = None
    """Reset rows only. Derived server-side per request -- the frontend renders, it never
    joins order book against positions itself."""
    orphan_orders: Optional[List[SquareOffRuleOrphanOrder]] = None
    """Reset rows only. This SG's still-live exit orders."""
    rearm_blocked: bool = False
    """True while any orphan is live. Same condition that blocks dismissal -- the UI must
    not be able to lie about live risk, and re-arming while an exit order rests would
    stack a duplicate on top of it."""


class SquareOffRuleListResponse(BaseModel):
    rules: List[SquareOffRuleRecord]
