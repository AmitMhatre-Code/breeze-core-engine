"""Strategy-level hedging request/response schemas."""
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

RiskProfile = Literal["net_short_call", "net_short_put", "multi_leg_reopt", "defined"]


class StrategyHedgeRequest(BaseModel):
    stock_code: str
    expiry_date: str = Field(..., description="Display format DD-Mon-YYYY")
    user_max_loss_preference: float = Field(..., gt=0)
    exchange_code: str = "NFO"


class StrategyHedgeCandidate(BaseModel):
    strike_price: int
    right: Literal["Call", "Put"]
    ltp: float
    net_premium_cost: float
    estimated_margin_relief: float
    max_loss_estimate: float
    score: float
    action: Literal["Buy"] = "Buy"
    hedge_quantity: int
    short_strike: int
    hedge_type: str = Field(..., description="e.g. bear_call_spread_wing, bull_put_spread_wing")


class StrategyHedgeBucketSummary(BaseModel):
    strategy_delta: float
    strategy_gamma: float
    net_premium_cash: float
    risk_profile: RiskProfile
    spot_price: float
    days_to_expiry: int
    lot_size: int


class StrategyHedgeResponse(BaseModel):
    summary: StrategyHedgeBucketSummary
    candidates: List[StrategyHedgeCandidate]
