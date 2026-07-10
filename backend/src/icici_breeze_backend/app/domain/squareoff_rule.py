"""Request/response schemas for group-level profit/loss square-off rules."""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

SquareOffRuleStatus = Literal["armed", "fired", "fire_failed", "disarmed"]


class ArmSquareOffRuleRequest(BaseModel):
    stock_code: str
    expiry_date: str = Field(..., description="Display format DD-Mon-YYYY")
    exchange_code: str = "NFO"
    profit_target_pnl: float = Field(..., gt=0)
    loss_limit_pnl: float = Field(..., gt=0)


class SquareOffRuleLegResult(BaseModel):
    scrip_key: str
    stock_code: str
    strike_price: str
    right: str
    quantity: str
    status: Literal["success", "failed"]
    error: Optional[str] = None


class SquareOffRuleRecord(BaseModel):
    id: str
    stock_code: str
    expiry_display: str
    exchange_code: str
    profit_target_pnl: float
    loss_limit_pnl: float
    status: SquareOffRuleStatus
    leg_results: Optional[List[SquareOffRuleLegResult]] = None
    created_at: Optional[str] = None
    fired_at: Optional[str] = None


class SquareOffRuleListResponse(BaseModel):
    rules: List[SquareOffRuleRecord]
