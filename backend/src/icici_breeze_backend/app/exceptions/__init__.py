"""Custom exception classes."""
from icici_breeze_backend.app.exceptions.base import AppException
from icici_breeze_backend.app.exceptions.auth import UnauthorizedError, InvalidCredentialsError, ForbiddenError
from icici_breeze_backend.app.exceptions.icici import ICICIServiceError, SessionExpiredError

__all__ = [
    "AppException",
    "UnauthorizedError",
    "InvalidCredentialsError",
    "ForbiddenError",
    "ICICIServiceError",
    "SessionExpiredError",
]
