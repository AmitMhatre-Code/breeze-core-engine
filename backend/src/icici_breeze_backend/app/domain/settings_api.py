"""JSON settings API models."""
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from icici_breeze_backend.app.domain.options_strategy import (
    ExecutiveSummaryOut,
    WhatIfInsightOut,
    WhyNotStrategyOut,
    WhyThisStrategyOut,
)


class CredentialsStateResponse(BaseModel):
    customer: dict[str, Any] = Field(default_factory=dict)
    margin: dict[str, Any] = Field(default_factory=dict)
    user_id: str = ""
    message: Optional[str] = None


class CredentialsUpdateBody(BaseModel):
    user_id: str
    api_key: str
    secret_fragment: str


class QuantityLimitsStateResponse(BaseModel):
    customer: dict[str, Any] = Field(default_factory=dict)
    margin: dict[str, Any] = Field(default_factory=dict)
    limits: list[dict[str, Any]] = Field(default_factory=list)
    message: Optional[str] = None
    user_id: str = ""


class QuantityLimitUpdateItem(BaseModel):
    short_name: str
    exchange_code: str
    segment_code: str = ""
    qty_limit: int


class QuantityLimitsUpdateBody(BaseModel):
    rows: list[QuantityLimitUpdateItem]


class ApiUsageByApiItem(BaseModel):
    usage_date: str
    api_name: str
    call_count: int


class ApiUsageByRouteItem(BaseModel):
    usage_date: str
    route_id: str
    call_count: int


class ApiUsageStateResponse(BaseModel):
    user_id: str = ""
    days: int = 30
    by_api: list[ApiUsageByApiItem] = Field(default_factory=list)
    by_route: list[ApiUsageByRouteItem] = Field(default_factory=list)
    rate_limit_pause_seconds: float = 0


class ApiUsagePreferencesResponse(BaseModel):
    user_id: str = ""
    rate_limit_pause_seconds: float = 0


class ApiUsagePreferencesUpdateBody(BaseModel):
    rate_limit_pause_seconds: float = Field(ge=0, le=3)


class MarginSourceStateResponse(BaseModel):
    user_id: str = ""
    margin_source: str = "breeze_api"
    latest_baseline: dict[str, Any] = Field(default_factory=dict)
    message: Optional[str] = None


class MarginSourceUpdateBody(BaseModel):
    margin_source: str


class ScripMasterStateResponse(BaseModel):
    user_id: str = ""
    master_date: Optional[str] = None
    master_age_days: Optional[int] = None
    has_past_expiries: bool = False
    past_expiries_count: int = 0
    message: Optional[str] = None


class ReferenceDataIngestHistoryItem(BaseModel):
    id: str
    kind: str
    display_name: str
    source_file_date: Optional[str] = None
    row_count: int = 0
    ingested_at: str
    ok: bool = False
    notes: Optional[str] = None
    source_url: Optional[str] = None
    upload_filename: Optional[str] = None


class ReferenceDataLoadsStateResponse(BaseModel):
    enabled: bool = True
    hour_ist: int = 18
    minute_ist: int = 0
    running: bool = False
    refresh_in_progress: bool = False
    last_refresh_message: Optional[str] = None
    nse_fo_refresh_in_progress: bool = False
    nse_fo_progress_pct: int = 0
    nse_fo_message: Optional[str] = None
    nse_fo_source_date: Optional[str] = None
    bse_fo_refresh_in_progress: bool = False
    bse_fo_progress_pct: int = 0
    bse_fo_message: Optional[str] = None
    bse_fo_source_date: Optional[str] = None
    scrip_refresh_in_progress: bool = False
    scrip_progress_pct: int = 0
    scrip_message: Optional[str] = None
    span_refresh_in_progress: bool = False
    span_progress_pct: int = 0
    span_message: Optional[str] = None
    bse_span_source_file: Optional[str] = None
    bse_span_source_date: Optional[str] = None
    bse_span_refreshed_at: Optional[str] = None
    bse_span_row_count: Optional[int] = None
    ingest_history: list[ReferenceDataIngestHistoryItem] = Field(default_factory=list)


class ReferenceDataScheduleUpdateBody(BaseModel):
    enabled: bool = True
    hour_ist: int = 18
    minute_ist: int = 0


class BreezeApiTesterWsSubscribeBody(BaseModel):
    model_config = ConfigDict(extra="allow")

    stock_token: Optional[str] = None
    exchange_code: Optional[str] = None
    stock_code: Optional[str] = None
    product_type: Optional[str] = None
    expiry_date: Optional[str] = None
    strike_price: Optional[str] = None
    right: Optional[str] = None
    get_market_depth: Optional[str] = None
    get_exchange_quotes: Optional[str] = None
    interval: Optional[str] = None
    get_order_notification: Optional[str] = None
    holder_id: Optional[str] = None


class WsReleaseRequest(BaseModel):
    holder_id: str = Field(..., min_length=1, max_length=128)


class AiProviderHealthEntry(BaseModel):
    ok: bool = False
    message: Optional[str] = None
    checked_at: Optional[str] = None


class AiProviderSideState(BaseModel):
    """One LLM provider row for the settings UI."""

    provider: str
    configured: bool = False
    enabled: bool = False
    model: Optional[str] = None
    fallback_models: list[str] = Field(default_factory=list)
    """Gemini-only. When null/omitted, UI lists all models from the global catalog."""
    tracked_models: Optional[list[str]] = None
    masked_api_key: Optional[str] = None
    models_working: int = 0
    models_failing: int = 0
    last_model_health_at: Optional[str] = None
    model_health: dict[str, AiProviderHealthEntry] = Field(default_factory=dict)


def _default_gemini_side() -> AiProviderSideState:
    return AiProviderSideState(provider="gemini")


def _default_openai_side() -> AiProviderSideState:
    return AiProviderSideState(provider="openai")


class AiProviderStateResponse(BaseModel):
    user_id: str = ""
    gemini: AiProviderSideState = Field(default_factory=_default_gemini_side)
    openai: AiProviderSideState = Field(default_factory=_default_openai_side)
    outlook_ai_provider: Optional[str] = None
    english_only: bool = True
    disclaimer: str = (
        "AI-generated outlook. Informational only. Not investment advice. Verify with primary sources."
    )
    message: Optional[str] = None


class AiProviderUpdateBody(BaseModel):
    provider: str
    api_key: str
    model: Optional[str] = None
    fallback_models: list[str] = Field(default_factory=list)
    enabled: bool = True


class AiProviderPatchBody(BaseModel):
    provider: str
    model: Optional[str] = None
    fallback_models: Optional[list[str]] = None
    """Gemini only. Omit to leave unchanged; empty list clears tracking (show full catalog)."""
    tracked_models: Optional[list[str]] = None


class AiProviderOutlookPickBody(BaseModel):
    provider: str


class AiProviderTestModelBody(BaseModel):
    provider: str
    model: str


class AiProviderTestBody(BaseModel):
    provider: str
    api_key: str
    model: Optional[str] = None
    fallback_models: list[str] = Field(default_factory=list)


class AiProviderModelTestResult(BaseModel):
    model: str
    ok: bool
    status_code: Optional[int] = None
    message: Optional[str] = None


class AiProviderTestResponse(BaseModel):
    ok: bool
    message: str
    results: list[AiProviderModelTestResult] = Field(default_factory=list)


class GeminiCatalogModelItem(BaseModel):
    model: str
    status: str = "healthy"
    message: Optional[str] = None
    display_name: Optional[str] = None


class GeminiCatalogPickerEntry(BaseModel):
    model: str
    display_name: Optional[str] = None


class GeminiCatalogResponse(BaseModel):
    provider: str = "gemini"
    available_models: list[GeminiCatalogModelItem] = Field(default_factory=list)
    stale_models: list[GeminiCatalogModelItem] = Field(default_factory=list)
    """Full generateContent-capable list from the cached Google catalog (for the picker modal)."""
    full_catalog: list[GeminiCatalogPickerEntry] = Field(default_factory=list)
    last_refreshed_at: Optional[str] = None
    last_health_check_at: Optional[str] = None


class OutlookFeedInput(BaseModel):
    name: str
    url: str


class OutlookConfigStateResponse(BaseModel):
    user_id: str = ""
    feeds: list[OutlookFeedInput] = Field(default_factory=list)
    prompt_template: str = ""
    system_prompt: str = ""
    using_default_feeds: bool = True
    using_default_prompt: bool = True
    using_default_system_prompt: bool = True
    message: Optional[str] = None


class OutlookConfigUpdateBody(BaseModel):
    feeds: list[OutlookFeedInput] = Field(default_factory=list)
    prompt_template: str = ""
    system_prompt: str = ""


class OutlookConfigResetBody(BaseModel):
    reset_feeds: bool = True
    reset_prompt: bool = True
    reset_system_prompt: bool = False


class BreezeApiTesterParamDef(BaseModel):
    name: str
    label: str
    type: str = "string"
    required: bool = False
    placeholder: str = ""
    help: str = ""


class BreezeApiTesterCatalogEntry(BaseModel):
    method: str
    title: str
    risk_level: str
    description: str = ""
    notes: str = ""
    params: list[BreezeApiTesterParamDef] = Field(default_factory=list)


class BreezeApiTesterCatalogResponse(BaseModel):
    entries: list[BreezeApiTesterCatalogEntry] = Field(default_factory=list)


class BreezeApiTesterRiskStatusResponse(BaseModel):
    accepted: bool = False
    accepted_at: Optional[str] = None


class BreezeApiTesterInvokeBody(BaseModel):
    method: str
    params: dict[str, Any] = Field(default_factory=dict)


class BreezeApiTesterInvokeResponse(BaseModel):
    ok: bool
    method: str
    duration_ms: int
    response: Any = None
    error: Optional[str] = None


class StrategyBuilderAuditLogItem(BaseModel):
    session_id: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    stock_code: Optional[str] = None
    expiry_date: Optional[str] = None
    min_pop_pct: Optional[float] = None
    margin_lacs: Optional[float] = None
    max_loss_lacs: Optional[float] = None
    provision_elm: Optional[bool] = None
    risk_reward_profile: Optional[str] = None
    strategy_category: Optional[str] = None
    event_count: Optional[int] = None
    filename: str = ""
    explainability_available: bool = False
    level_4_available: bool = True


class StrategyBuilderAuditLogsResponse(BaseModel):
    user_id: str = ""
    max_logs: int = 10
    logs: list[StrategyBuilderAuditLogItem] = Field(default_factory=list)


class StrategyBuilderAuditExplainabilityLevel2Out(BaseModel):
    why_this: list[WhyThisStrategyOut] = Field(default_factory=list)
    why_not: list[WhyNotStrategyOut] = Field(default_factory=list)


class StrategyBuilderAuditExplainabilityResponse(BaseModel):
    session_id: str
    level_1: ExecutiveSummaryOut
    level_2: StrategyBuilderAuditExplainabilityLevel2Out
    level_3: list[WhatIfInsightOut] = Field(default_factory=list)


class ExchangeCalendarWorkingHours(BaseModel):
    open_hour: int = Field(ge=0, le=23)
    open_minute: int = Field(ge=0, le=59)
    close_hour: int = Field(ge=0, le=23)
    close_minute: int = Field(ge=0, le=59)


class ExchangeCalendarHolidayItem(BaseModel):
    date: str
    name: str


class ExchangeCalendarUpdateBody(BaseModel):
    working_hours: ExchangeCalendarWorkingHours
    holidays: list[ExchangeCalendarHolidayItem] = Field(default_factory=list)


class ExchangeCalendarAddHolidayBody(BaseModel):
    date: str = Field(min_length=10, max_length=10)
    name: str = Field(min_length=1, max_length=256)


class ExchangeCalendarStateResponse(BaseModel):
    user_id: str = ""
    source: str = "local"
    working_hours: ExchangeCalendarWorkingHours
    holidays: dict[str, str] = Field(default_factory=dict)
    holidays_list: list[ExchangeCalendarHolidayItem] = Field(default_factory=list)
    portal_configured: bool = False
    has_local_edits: bool = False
    console_updated_at: str | None = None
    local_updated_at: str | None = None
    updated_at: str | None = None


class ExchangeCalendarSyncPreviewResponse(BaseModel):
    portal_configured: bool = False
    would_overwrite_local: bool = False
    console: ExchangeCalendarStateResponse | None = None
    local_holiday_count: int = 0
    console_holiday_count: int = 0
    message: str | None = None


class ExchangeCalendarSyncBody(BaseModel):
    confirm_override: bool = False


class MarketStatusResponse(BaseModel):
    is_open: bool
    closed_reason: str
