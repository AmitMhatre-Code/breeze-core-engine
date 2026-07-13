"""Request/response schemas for group-level profit/loss square-off rules."""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

SquareOffRuleStatus = Literal["armed", "triggered", "fired", "fire_failed", "disarmed"]


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
    created_at: Optional[str] = None
    fired_at: Optional[str] = None


class SquareOffRuleListResponse(BaseModel):
    rules: List[SquareOffRuleRecord]
