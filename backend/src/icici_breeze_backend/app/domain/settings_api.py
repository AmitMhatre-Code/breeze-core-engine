"""JSON settings API models."""
from typing import Any, Optional

from pydantic import BaseModel, Field


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
    rate_limit_pause_seconds: float = 1


class ApiUsagePreferencesResponse(BaseModel):
    user_id: str = ""
    rate_limit_pause_seconds: float = 1


class ApiUsagePreferencesUpdateBody(BaseModel):
    rate_limit_pause_seconds: float = Field(ge=0.25, le=300)


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
