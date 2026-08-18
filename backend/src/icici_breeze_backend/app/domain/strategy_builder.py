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
    margin_source: Optional[Literal["breeze_api", "exchange_baseline"]] = None
    baseline_only: bool = False
    spot: Optional[float] = None
    iv: Optional[float] = None
    time_years: Optional[float] = None
    # Portfolio-aware (incremental) margin netting against the user's open positions
    # in the same scrip -- see docs/strategy-builder-portfolio-margin-plan.md (D1-D10).
    net_against_positions: bool = True


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
    """Success also carries (when computable): elm_requirement (float, whole-basket ELM),
    elm_is_index (bool), elm_approximate (bool, true when the stock flat-rate tier or a
    previous-close-lookup fallback was used) — see processor.strategy_builder_margin."""

    model_config = {"extra": "allow"}
    Status: int
    Error: Optional[str] = None
    Success: Optional[dict[str, Any]] = None


class SpanBaselineContract(BaseModel):
    margin_per_lot: float
    lot_size: int


class SpanBaselineSheetResponse(BaseModel):
    found: bool
    contracts: dict[str, SpanBaselineContract] = Field(default_factory=dict)
    source_date: Optional[str] = None
    source_file: Optional[str] = None


class SpanPortfolioMarginLeg(BaseModel):
    strike_price: str
    right: Literal["Call", "Put"]
    action: Literal["Buy", "Sell"]
    quantity: str


class SpanPortfolioMarginRequest(BaseModel):
    exchange_code: str = "NFO"
    stock_code: str
    expiry_date: str
    legs: List[SpanPortfolioMarginLeg] = Field(min_length=1, max_length=12)
    spot: Optional[float] = None
    iv: Optional[float] = None
    time_years: Optional[float] = None


class SpanPortfolioMarginSuccess(BaseModel):
    span_margin_required: Optional[float] = None
    scanning_risk: Optional[float] = None
    net_option_value: Optional[float] = None
    margin_benefit: Optional[float] = None
    per_leg_standalone: dict[str, float] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)


class SpanPortfolioMarginResponse(BaseModel):
    Status: int
    Error: Optional[str] = None
    Success: Optional[SpanPortfolioMarginSuccess] = None
