"""ICICI broker API exceptions."""
from app.exceptions.base import AppException


class ICICIServiceError(AppException):
    """ICICI broker API error."""

    status_code = 502
    detail = "Broker request failed; please retry"


class SessionExpiredError(AppException):
    """ICICI session expired or invalid."""

    status_code = 401
    detail = "Session expired or invalid; please log in again"
