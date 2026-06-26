"""Correlation ID middleware for request tracing."""
import uuid
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from icici_breeze_backend.app.auth.context import (
    reset_correlation_id_context,
    set_current_correlation_id,
)


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Set correlation_id on request for tracing."""

    async def dispatch(self, request: Request, call_next):
        request.state.correlation_id = (
            getattr(request.state, "correlation_id", None) or str(uuid.uuid4())
        )
        cid_token = set_current_correlation_id(request.state.correlation_id)
        try:
            response = await call_next(request)
            response.headers["X-Correlation-ID"] = request.state.correlation_id
            return response
        finally:
            reset_correlation_id_context(cid_token)
