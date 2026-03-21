"""Response models for API endpoints. Ensures explicit schemas and no raw DB/API objects."""
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


# ---- Health & Metrics ----
class HealthResponse(BaseModel):
    """Health check response."""
    status: str = "ok"
    timestamp: str = Field(..., description="ISO 8601 timestamp")


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


class OrderDetailResponse(BaseModel):
    """Order detail response (from get_order_detail)."""
    model_config = ConfigDict(extra="allow")
