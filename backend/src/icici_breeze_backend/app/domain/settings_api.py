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


class AiProviderStateResponse(BaseModel):
    user_id: str = ""
    configured: bool = False
    enabled: bool = False
    provider: Optional[str] = None
    model: Optional[str] = None
    masked_api_key: Optional[str] = None
    english_only: bool = True
    disclaimer: str = (
        "AI-generated outlook. Informational only. Not investment advice. Verify with primary sources."
    )
    message: Optional[str] = None


class AiProviderUpdateBody(BaseModel):
    provider: str
    api_key: str
    model: Optional[str] = None
    enabled: bool = True


class AiProviderTestBody(BaseModel):
    provider: str
    api_key: str
    model: Optional[str] = None


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
