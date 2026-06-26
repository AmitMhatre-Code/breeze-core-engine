"""Correlation ID middleware for request tracing."""
import uuid
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Set correlation_id on request for tracing."""

    async def dispatch(self, request: Request, call_next):
        request.state.correlation_id = (
            getattr(request.state, "correlation_id", None) or str(uuid.uuid4())
        )
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = request.state.correlation_id
        return response
