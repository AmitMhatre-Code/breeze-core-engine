"""Options strategy builder (v2) request/response schemas."""
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

StrategyCategory = Literal["income", "bullish", "bearish", "volatility"]
RiskRewardProfile = Literal["conservative", "moderate", "aggressive"]


class ProposeTradesRequest(BaseModel):
    exchange_code: str = "NFO"
    stock_code: str
    expiry_date: str
    margin_lacs: float = Field(gt=0)
    max_loss_lacs: Optional[float] = Field(default=None, gt=0)
    allow_infinite_loss: bool = False
    min_pop_pct: float = Field(default=65, ge=1, le=99)
    min_ann_return_pct: float = Field(default=5.0, ge=0, le=100)
    provision_elm: bool = False
    strategy_category: StrategyCategory
    risk_reward_profile: Optional[RiskRewardProfile] = None
    audit_detail_level: Literal["summary", "debug"] = "summary"

    @model_validator(mode="after")
    def _validate_max_loss_mode(self) -> "ProposeTradesRequest":
        if self.allow_infinite_loss and self.max_loss_lacs is not None:
            raise ValueError("Specify either max_loss_lacs or allow_infinite_loss, not both.")
        if not self.allow_infinite_loss and self.max_loss_lacs is None:
            raise ValueError("max_loss_lacs is required unless allow_infinite_loss is true.")
        return self


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


class TileMetricOut(BaseModel):
    label: str
    value: str


class ProposedTradeOut(BaseModel):
    strategy_id: str
    strategy_name: str
    status: Literal["ok", "skipped"]
    skip_reason: Optional[str] = None
    structure_modified: bool = False
    net_premium: Optional[float] = None
    max_loss: Optional[float] = None
    max_profit: Optional[float] = None
    annualized_return_pct: Optional[float] = None
    risk_reward_ratio: Optional[str] = None
    span_margin: Optional[float] = None
    elm_requirement: Optional[float] = None
    # Portfolio-aware (incremental) margin netting -- see
    # docs/strategy-builder-portfolio-margin-plan.md (D1-D10). `span_margin`
    # above IS the incremental figure (may be <= 0) when `netted_against_positions`
    # is true; `standalone_span_margin` preserves the pre-netting number and
    # `positions_margin_benefit` is standalone minus incremental (floored at 0).
    # NOT the same as any pre-existing "margin_benefit" field elsewhere in this
    # API -- that name is taken by a different, intra-structure quantity.
    standalone_span_margin: Optional[float] = None
    positions_margin_benefit: Optional[float] = None
    netted_against_positions: bool = False
    margin_released: bool = False
    pop_pct: Optional[float] = None
    legs: List[ProposedTradeLegOut] = Field(default_factory=list)
    variant_rank: Optional[int] = None
    engine_score: Optional[float] = None
    ranking_summary: Optional[str] = None
    score_breakdown: Optional[dict[str, float]] = None
    conviction_profile: Optional[RiskRewardProfile] = None
    hero_metric: Optional[TileMetricOut] = None
    secondary_metrics: List[TileMetricOut] = Field(default_factory=list)
    badges: List[str] = Field(default_factory=list)
    compliance: Literal["recommended", "relaxed"] = "recommended"
    constraint_violations: List[str] = Field(default_factory=list)


class UserInputsSummaryOut(BaseModel):
    strategy_category: str
    margin_lacs: float
    max_loss_lacs: Optional[float] = None
    allow_infinite_loss: bool = False
    min_pop_pct: Optional[float] = None
    min_ann_return_pct: Optional[float] = None


class StrategyRefOut(BaseModel):
    strategy_id: str
    strategy_name: str


class SkippedStrategySummaryOut(BaseModel):
    strategy_id: str
    strategy_name: str
    primary_reason: str
    summary: str


class ExecutiveSummaryOut(BaseModel):
    user_inputs: UserInputsSummaryOut
    strategies_evaluated: int
    strategies_recommended: List[StrategyRefOut] = Field(default_factory=list)
    strategies_skipped: List[SkippedStrategySummaryOut] = Field(default_factory=list)
    generation_duration_seconds: Optional[float] = None


class FunnelStageOut(BaseModel):
    stage: str
    label: str
    count: int | str


class BadgeExplanationOut(BaseModel):
    badge: str
    rationale: str


class TradeMetricsOut(BaseModel):
    pop_pct: Optional[float] = None
    net_credit: Optional[float] = None
    annualized_return_pct: Optional[float] = None
    margin: Optional[float] = None


class ReturnedTradeExplanationOut(BaseModel):
    strategy_name: str
    variant_rank: Optional[int] = None
    conviction_profile: Optional[RiskRewardProfile] = None
    badges: List[str] = Field(default_factory=list)
    badge_explanations: List[BadgeExplanationOut] = Field(default_factory=list)
    metrics: TradeMetricsOut
    ranking_summary: Optional[str] = None


class WhyThisStrategyOut(BaseModel):
    strategy_id: str
    strategy_name: str
    funnel: List[FunnelStageOut] = Field(default_factory=list)
    pop_filter_note: Optional[str] = None
    returned_trades: List[ReturnedTradeExplanationOut] = Field(default_factory=list)


class WhyNotStrategyOut(BaseModel):
    strategy_id: str
    strategy_name: str
    primary_reason: str
    explanation: str
    funnel: List[FunnelStageOut] = Field(default_factory=list)


class WhatIfInsightOut(BaseModel):
    constraint: str
    current_value: Optional[float] = None
    suggested_change: Optional[float] = None
    affected_strategies: List[str] = Field(default_factory=list)
    message: str


class UserExplainabilityReportOut(BaseModel):
    user_report_schema_version: str
    executive_summary: ExecutiveSummaryOut
    why_this: List[WhyThisStrategyOut] = Field(default_factory=list)
    why_not: List[WhyNotStrategyOut] = Field(default_factory=list)
    what_if_insights: List[WhatIfInsightOut] = Field(default_factory=list)


class ProposeTradesSuccess(BaseModel):
    spot_price: Optional[float] = None
    lot_size: int
    expiry_display: str
    atm_iv: Optional[float] = None
    structure_modified: bool = False
    trades: List[ProposedTradeOut] = Field(default_factory=list)
    relaxed_trades: List[ProposedTradeOut] = Field(default_factory=list)
    audit_session_id: Optional[str] = None
    user_report: Optional[UserExplainabilityReportOut] = None
    # D7: set when this build's open-positions fetch (or the live M(P) call)
    # failed, so every trade in this response is standalone-only even though
    # portfolio-aware netting was requested -- frontend renders one fallback
    # banner rather than a per-card explanation.
    netting_unavailable_reason: Optional[str] = None


class ProposeTradesResponse(BaseModel):
    model_config = {"extra": "allow"}
    Status: int
    Error: Optional[str] = None
    Success: Optional[ProposeTradesSuccess | dict[str, Any]] = None


class ProposeTradesJobStartSuccess(BaseModel):
    job_id: str


class ProposeTradesJobStartResponse(BaseModel):
    model_config = {"extra": "allow"}
    Status: int
    Error: Optional[str] = None
    Success: Optional[ProposeTradesJobStartSuccess] = None


class ProposeTradesJobStatusSuccess(BaseModel):
    job_id: str
    status: Literal["queued", "running", "done", "error"]
    phase: str
    message: str
    progress_pct: int = 0
    progress_current: int = 0
    progress_total: int = 1
    result: Optional[ProposeTradesSuccess] = None
    error: Optional[str] = None


class ProposeTradesJobStatusResponse(BaseModel):
    model_config = {"extra": "allow"}
    Status: int
    Error: Optional[str] = None
    Success: Optional[ProposeTradesJobStatusSuccess] = None
