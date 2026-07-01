"""Health and metrics endpoints."""
from datetime import datetime, timezone

from fastapi import APIRouter

from icici_breeze_backend.app.domain.responses import (
    HealthResponse,
    ICICIMetricsResponse,
    RuntimeMetricsResponse,
)

router = APIRouter()


def _health_payload() -> HealthResponse:
    from icici_breeze_backend.app.db.redis_client import redis_runtime_stats

    stats = redis_runtime_stats()
    connected = bool(stats.get("redis_connected"))
    fallback = bool(stats.get("redis_memory_fallback"))
    status = "degraded" if fallback else "ok"
    return HealthResponse(
        status=status,
        timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        redis_connected=connected,
        redis_memory_fallback=fallback,
        redis_used_memory_human=stats.get("used_memory_human") or None,
    )


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Health check endpoint."""
    return _health_payload()


@router.get("/metrics", response_model=ICICIMetricsResponse)
async def metrics() -> ICICIMetricsResponse:
    """ICICI API call metrics for monitoring."""
    from icici_breeze_backend.core.icici_client import icici_client

    data = icici_client.get_metrics()
    return ICICIMetricsResponse(**data)


@router.get("/metrics/runtime", response_model=RuntimeMetricsResponse)
async def runtime_metrics() -> RuntimeMetricsResponse:
    """WS tick pipeline, active chains, and Redis memory stats."""
    from icici_breeze_backend.app.db.redis_client import redis_runtime_stats
    from icici_breeze_backend.app.services.reference_data.active_chains import active_chain_stats
    from icici_breeze_backend.app.services.ws_tick_pipeline import pipeline_stats

    return RuntimeMetricsResponse(
        redis=redis_runtime_stats(),
        ws_tick_pipeline=pipeline_stats(),
        active_chains=active_chain_stats(),
    )
