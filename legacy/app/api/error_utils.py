"""Error handling utilities for route handlers."""
import logging
from fastapi import Request
from fastapi.templating import Jinja2Templates

from core.user_messages import sanitize_errors_for_ui

_logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="templates")


def render_error_page(
    request: Request,
    errors: list,
    active: str = "",
    log_context: str = "",
):
    """Render error.html with user-friendly messages."""
    if errors and log_context:
        _logger.warning("Route error: %s errors=%s", log_context, [e.get("location") or e.get("contents") for e in errors[:3]])
    sanitized = sanitize_errors_for_ui(errors)
    return templates.TemplateResponse(
        "error.html",
        {
            "request": request,
            "is_logged_in": False,
            "login_url": None,
            "active": active,
            "errors": sanitized,
            "display_theme": "dark",
        },
    )
