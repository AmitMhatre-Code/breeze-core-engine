"""Auth-related exceptions."""
from app.exceptions.base import AppException


class UnauthorizedError(AppException):
    """Authentication required or token invalid."""

    status_code = 401
    detail = "Invalid or missing authentication token"


class InvalidCredentialsError(AppException):
    """Credential reconstruction or verification failed."""

    status_code = 401
    detail = "Invalid credential challenge response"


class ForbiddenError(AppException):
    """User lacks permission for the operation."""

    status_code = 403
    detail = "Forbidden"
