"""Response models for API endpoints. Ensures explicit schemas and no raw DB/API objects."""
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# ---- Health & Metrics ----
class HealthResponse(BaseModel):
    """Health check response."""
    status: Literal["ok", "degraded"] = "ok"
    timestamp: str = Field(..., description="ISO 8601 timestamp")
    redis_connected: bool = True
    redis_memory_fallback: bool = False
    redis_used_memory_human: Optional[str] = Field(
        None,
        description="Redis used_memory_human when connected",
    )


class RuntimeMetricsResponse(BaseModel):
    """Process and cache runtime metrics for small-instance monitoring."""

    model_config = ConfigDict(extra="allow")
    redis: Dict[str, Any] = Field(default_factory=dict)
    ws_tick_pipeline: Dict[str, Any] = Field(default_factory=dict)
    active_chains: Dict[str, Any] = Field(default_factory=dict)
    pnl_engine: Dict[str, Any] = Field(default_factory=dict)


class ICICIMetricsResponse(BaseModel):
    """ICICI API call metrics for monitoring."""
    call_count: int = 0
    error_count: int = 0
    avg_latency_seconds: float = 0.0
    success_rate_percent: float = 0.0
    last_call_time: Optional[float] = None


# ---- ICICI API passthrough (Status/Success/Error pattern) ----
class IciciApiResponse(BaseModel):
    """Generic ICICI API response shape. Success payload is unvalidated passthrough."""
    model_config = ConfigDict(extra="allow")
    Status: int = Field(..., description="HTTP-like status")
    Success: Optional[Any] = Field(None, description="Success payload from broker")
    Error: Optional[str] = Field(None, description="Error message")


# ---- Audit ----
class AuditLogEntryResponse(BaseModel):
    """Single audit log entry (explicit fields, no raw row leakage)."""
    model_config = ConfigDict(extra="forbid")
    id: Optional[int] = None
    user_id: Optional[str] = None
    operation_type: Optional[str] = None
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None
    action_status: Optional[str] = None
    timestamp: Optional[Any] = None
    request_id: Optional[str] = None
    ip_address: Optional[str] = None
    error_details: Optional[str] = None


class AuditLogListResponse(BaseModel):
    """Paginated audit log response."""
    entries: List[AuditLogEntryResponse] = Field(default_factory=list)
    count: int = 0


# ---- Auth ----
class LogoutResponse(BaseModel):
    """Logout success response."""
    message: str = "Logout successful; tokens revoked"


class AdminRotateResponse(BaseModel):
    """Admin credential rotation response."""
    message: str = "Credentials rotated for user"
    target_user_id: str = Field(..., description="User whose credentials were rotated")


class AdminRevokeResponse(BaseModel):
    """Admin credential revoke response."""
    message: str = "Credentials revoked for user"
    target_user_id: str = Field(..., description="User whose credentials were revoked")


# ---- Misc ----
class StockCodesResponse(BaseModel):
    """Stock codes list response."""
    stock_codes: List[Dict[str, Any]] = Field(default_factory=list)


class DeploymentLicenseContactSales(BaseModel):
    """Deployment context for Contact Sales mailto when license is expired or revoked."""

    model_config = ConfigDict(extra="forbid")
    license_key: Optional[str] = Field(None, description="Deployment license key from env")
    public_ip: Optional[str] = Field(None, description="Public IP parsed from deployment origin")
    deployment_origin: Optional[str] = Field(None, description="PUBLIC_FRONTEND_ORIGIN URL")
    app_version: Optional[str] = Field(None, description="Reported app version from heartbeat")


class HomeDataResponse(BaseModel):
    """Home /data: customer and margin info."""
    customer: Dict[str, Any] = Field(default_factory=dict)
    margin: Dict[str, Any] = Field(default_factory=dict)
    api_calls_today: int = Field(
        0,
        description="ICICI Breeze REST calls counted for this user today (IST)",
    )
    api_calls_limit: int = Field(
        5000,
        description="ICICI-documented daily cap for Breeze API calls",
    )
    api_usage_band: str = Field(
        "green",
        description="green | amber | red relative to daily limit thresholds",
    )
    api_usage_warning: Optional[str] = Field(
        None,
        description="Proactive warning when user is in the final 1000-call band",
    )
    api_usage_blocked: bool = Field(
        False,
        description="True when daily Breeze API cap is reached (broker calls blocked)",
    )
    deployment_license_status: Optional[Literal["active", "expired", "revoked", "unlicensed"]] = Field(
        None,
        description="Portal-reported deployment license status when portal env is configured",
    )
    deployment_license_read_only: bool = Field(
        False,
        description="True when deployment is unlicensed or revoked (trading mutations blocked)",
    )
    contact_sales: Optional[DeploymentLicenseContactSales] = Field(
        None,
        description="License/deployment snapshot for Contact Sales when status is expired, revoked, or unlicensed",
    )
    aggressive_limit_order_enabled: bool = Field(
        False,
        description="False while ICICI has no native aggressive-limit order support (AGGRESSIVE_LIMIT_ORDER_ENABLED)",
    )


class DeploymentLicenseStatusResponse(BaseModel):
    """Portal deployment license status (heartbeat cache); JWT only, no broker calls."""

    deployment_license_status: Optional[Literal["active", "expired", "revoked", "unlicensed"]] = Field(
        None,
        description="Portal-reported deployment license status when portal env is configured",
    )
    deployment_license_read_only: bool = Field(
        False,
        description="True when deployment is unlicensed or revoked (trading mutations blocked)",
    )
    contact_sales: Optional[DeploymentLicenseContactSales] = Field(
        None,
        description="License/deployment snapshot for Contact Sales when status is expired, revoked, or unlicensed",
    )


class PerformanceDataResponse(BaseModel):
    """Performance /data: margin, funds, FY-scoped performance, and FY picker metadata."""
    performance: Dict[str, Any] = Field(default_factory=dict)
    funds: Dict[str, Any] = Field(default_factory=dict)
    margin: Dict[str, Any] = Field(default_factory=dict)
    years: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Recent financial years with year label and start/end dates",
    )
    fy: str = Field(
        "",
        description="Financial year label applied for performance (e.g. 2024-25)",
    )
    start: str = Field("", description="Applied performance window start YYYY-MM-DD")
    end: str = Field("", description="Applied performance window end YYYY-MM-DD")


class UncoveredShortsDataResponse(BaseModel):
    """Uncovered Shorts /data: options list."""
    options: Any = Field(default=None)


class UncoveredShortsScanResponse(BaseModel):
    """Uncovered Shorts /scan: CE/PE option lists from get_options (legacy Optimize)."""

    model_config = ConfigDict(extra="allow")
    ce_options: Dict[str, Any] = Field(default_factory=dict)
    pe_options: Dict[str, Any] = Field(default_factory=dict)


class OrderDetailResponse(BaseModel):
    """Order detail response (from get_order_detail)."""
    model_config = ConfigDict(extra="allow")


class BookDataResponse(BaseModel):
    """Order book JSON for SPA (legacy book.html parity: messages + grouped orders)."""

    model_config = ConfigDict(extra="forbid")
    messages: List[Dict[str, Any]] = Field(default_factory=list)
    grouped_orders: Optional[List[Dict[str, Any]]] = Field(
        None,
        description="Grouped rows from Processor.group_orders; None when no data",
    )
    start: str = Field("", description="Applied from_date YYYY-MM-DD")
    end: str = Field("", description="Applied to_date YYYY-MM-DD")
    orders_failed: bool = Field(
        False,
        description="True when get_orders returned non-200",
    )
    rule_spawned_orders: Optional[List[Dict[str, Any]]] = Field(
        None,
        description=(
            "Raw order rows excluded from grouped_orders because they were spawned by a "
            "Profit Booking / Stop Loss exit rule; each is tagged with exit_rule_source "
            "('squareoff_rule' | 'gtt_exit_order') and exit_rule_id"
        ),
    )
