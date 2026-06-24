"""Strategy Builder API request/response schemas."""
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field


class StrategyBuilderLegIn(BaseModel):
    """One option leg for margin or execution."""

    stock_code: str
    exchange_code: str = "NFO"
    expiry_date: str
    product_type: str = "Options"
    right: Literal["Call", "Put"]
    strike_price: str
    quantity: str
    price: str = "0"
    action: Literal["Buy", "Sell"]
    aggressive_limit: bool = False


class StrategyBuilderMarginRequest(BaseModel):
    legs: List[StrategyBuilderLegIn] = Field(min_length=1, max_length=12)


class StrategyBuilderExecuteLeg(StrategyBuilderLegIn):
    """Leg with optional per-leg idempotency key."""

    idempotency_key: Optional[str] = None


class StrategyBuilderExecuteRequest(BaseModel):
    legs: List[StrategyBuilderExecuteLeg] = Field(min_length=1, max_length=12)


class StrategyBuilderLegResult(BaseModel):
    index: int
    success: bool
    idempotency_key: Optional[str] = None
    messages: List[dict[str, Any]] = Field(default_factory=list)
    error: Optional[str] = None


class StrategyBuilderExecuteResponse(BaseModel):
    legs: List[StrategyBuilderLegResult]
    placed_count: int
    failed_count: int


class StrategyBuilderUnderlyingsResponse(BaseModel):
    underlyings: List[dict[str, Any]] = Field(default_factory=list)


class StrategyBuilderChainResponse(BaseModel):
    model_config = {"extra": "allow"}
    Status: int
    Error: Optional[str] = None
    Success: Optional[dict[str, Any]] = None


class StrategyBuilderMarginResponse(BaseModel):
    model_config = {"extra": "allow"}
    Status: int
    Error: Optional[str] = None
    Success: Optional[dict[str, Any]] = None
