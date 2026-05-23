"""Request logging middleware."""
import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from icici_breeze_backend.app.auth.context import set_current_route_id
from icici_breeze_backend.audit.logger import AuditLogger, OperationType

logger = logging.getLogger("app.middleware")

# High-frequency probes should not write to audit_log (no authenticated user).
_AUDIT_SKIP_PATHS = frozenset({"/health", "/metrics"})


def _resolve_request_actor(request: Request, state_user_id: str | None) -> str:
    client_ip = (request.client.host if request.client else None) or "unknown"
    return state_user_id or client_ip or "anonymous"


class RequestLoggerMiddleware(BaseHTTPMiddleware):
    """Log incoming requests and append to audit trail."""

    async def dispatch(self, request: Request, call_next):
        set_current_route_id(f"{request.method} {request.url.path}")
        # request.state.user_id is set by auth dependencies; middleware runs before them.
        state_user_id_before = getattr(request.state, "user_id", None)

        response = await call_next(request)

        state_user_id_after = getattr(request.state, "user_id", None)
        user_id = _resolve_request_actor(
            request, state_user_id_after or state_user_id_before
        )

        logger.info(
            "Incoming request %s %s user_id=%s",
            request.method,
            request.url.path,
            user_id,
        )

        if request.url.path in _AUDIT_SKIP_PATHS:
            return response

        try:
            AuditLogger(None).log_operation(
                user_id=user_id,
                operation_type=OperationType.HTTP_REQUEST,
                resource_type="Request",
                resource_id=f"{request.method} {request.url.path}",
                action_status="success",
                request_id=getattr(request.state, "correlation_id", None),
                ip_address=request.client.host if request.client else None,
            )
        except Exception as e:
            logger.warning("Audit log write failed: %s", e)
        return response
