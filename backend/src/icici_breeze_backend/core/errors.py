"""Custom error/exception classes for the application (Phase 6 T099)."""
from fastapi import HTTPException


class ConflictError(HTTPException):
    def __init__(self, detail: str = "Conflict", headers: dict = None):
        super().__init__(status_code=409, detail=detail, headers=headers or {})


class NotFoundError(HTTPException):
    def __init__(self, detail: str = "Not Found"):
        super().__init__(status_code=404, detail=detail)


class UnauthorizedError(HTTPException):
    def __init__(self, detail: str = "Unauthorized"):
        super().__init__(status_code=401, detail=detail)


class ForbiddenError(HTTPException):
    def __init__(self, detail: str = "Forbidden"):
        super().__init__(status_code=403, detail=detail)


class ServiceUnavailableError(HTTPException):
    """ICICI API or circuit breaker unavailable - return 503 with retry guidance."""

    def __init__(self, detail: str = "Service temporarily unavailable; please retry later"):
        super().__init__(status_code=503, detail=detail)


# Map ICICI API error patterns to HTTP status and user-facing messages (T099)
ICICI_ERROR_MAP = {
    "session": (401, "Session expired or invalid; please log in again"),
    "token": (401, "Invalid or expired token"),
    "unauthorized": (401, "Authentication failed"),
    "rate limit": (429, "Too many requests; please slow down"),
    "timeout": (504, "Request to broker timed out; please retry"),
    "connection": (503, "Broker service unavailable; please retry later"),
    "internal": (502, "Broker returned an error; please retry"),
}


def icici_error_to_http(message: str) -> tuple[int, str]:
    """Map ICICI API error message to (status_code, user_message)."""
    msg_lower = (message or "").lower()
    for pattern, (code, user_msg) in ICICI_ERROR_MAP.items():
        if pattern in msg_lower:
            return code, user_msg
    return 502, "Broker request failed; please retry"
