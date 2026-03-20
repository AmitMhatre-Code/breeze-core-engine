"""JSON error responses for SPA clients (replaces Jinja error pages)."""
import logging

from fastapi import HTTPException

from icici_breeze_backend.core.user_messages import sanitize_errors_for_ui

_logger = logging.getLogger(__name__)


def raise_route_errors(errors: list, log_context: str = "") -> None:
    """Raise HTTPException with sanitized errors for UI."""
    if errors and log_context:
        _logger.warning(
            "Route error: %s errors=%s",
            log_context,
            [e.get("location") or e.get("contents") for e in errors[:3]],
        )
    sanitized = sanitize_errors_for_ui(errors)
    raise HTTPException(status_code=422, detail={"errors": sanitized})
