"""Request logging middleware."""
import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from icici_breeze_backend.audit.logger import AuditLogger, OperationType

logger = logging.getLogger("app.middleware")


class RequestLoggerMiddleware(BaseHTTPMiddleware):
    """Log incoming requests and append to audit trail."""

    async def dispatch(self, request: Request, call_next):
        # request.state.user_id is set by auth dependencies; middleware runs before them.
        state_user_id_before = getattr(request.state, "user_id", None)
        client_ip = request.client.host if request.client else "unknown"

        response = await call_next(request)

        state_user_id_after = getattr(request.state, "user_id", None)
        user_id = state_user_id_after or state_user_id_before or client_ip

        logger.info(
            "Incoming request %s %s user_id=%s",
            request.method,
            request.url.path,
            user_id,
        )

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
