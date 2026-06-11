"""Options strategy builder (v2) request/response schemas."""
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field


class ProposeTradesRequest(BaseModel):
    exchange_code: str = "NFO"
    stock_code: str
    expiry_date: str
    margin_lacs: float = Field(gt=0)
    max_loss_lacs: float = Field(gt=0)
    min_pop_pct: float = Field(default=65, ge=1, le=99)
    provision_elm: bool = False


class ProposedTradeLegOut(BaseModel):
    right: Literal["Call", "Put"]
    side: Literal["Buy", "Sell"]
    strike: int
    quantity: int
    premium_per_unit: float
    ltp: Optional[float] = None
    best_bid_price: Optional[float] = None
    best_offer_price: Optional[float] = None
    total_buy_qty: Optional[int] = None
    total_sell_qty: Optional[int] = None
    buy_sell_ratio: Optional[float | str] = None


class ProposedTradeOut(BaseModel):
    strategy_id: str
    strategy_name: str
    status: Literal["ok", "skipped"]
    skip_reason: Optional[str] = None
    structure_modified: bool = False
    net_premium: Optional[float] = None
    max_loss: Optional[float] = None
    annualized_return_pct: Optional[float] = None
    risk_reward_ratio: Optional[str] = None
    span_margin: Optional[float] = None
    pop_pct: Optional[float] = None
    legs: List[ProposedTradeLegOut] = Field(default_factory=list)


class ProposeTradesSuccess(BaseModel):
    spot_price: Optional[float] = None
    lot_size: int
    expiry_display: str
    atm_iv: Optional[float] = None
    structure_modified: bool = False
    trades: List[ProposedTradeOut] = Field(default_factory=list)
    audit_session_id: Optional[str] = None


class ProposeTradesResponse(BaseModel):
    model_config = {"extra": "allow"}
    Status: int
    Error: Optional[str] = None
    Success: Optional[ProposeTradesSuccess | dict[str, Any]] = None
