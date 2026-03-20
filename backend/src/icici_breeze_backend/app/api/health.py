"""Health and metrics endpoints."""
from datetime import datetime, timezone
from fastapi import APIRouter

from icici_breeze_backend.app.domain.responses import HealthResponse, ICICIMetricsResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Health check endpoint."""
    return HealthResponse(
        status="ok",
        timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    )


@router.get("/metrics", response_model=ICICIMetricsResponse)
async def metrics() -> ICICIMetricsResponse:
    """ICICI API call metrics for monitoring."""
    from icici_breeze_backend.core.icici_client import icici_client
    data = icici_client.get_metrics()
    return ICICIMetricsResponse(**data)
