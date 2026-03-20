"""Rate limit middleware. E2E bypass when X-E2E-Test matches E2E_RATE_LIMIT_BYPASS_SECRET."""
import os
import time
from collections import defaultdict
from threading import Lock

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

try:
    RATE_LIMIT_PER_MIN = int(os.environ.get("RATE_LIMIT_PER_MIN", "100"))
except (ValueError, TypeError):
    RATE_LIMIT_PER_MIN = 100

# When set, requests with header X-E2E-Test: <this value> are not counted (for Playwright E2E).
_E2E_BYPASS_SECRET = (os.environ.get("E2E_RATE_LIMIT_BYPASS_SECRET") or "").strip()

_rate_limit: dict[str, list[float]] = defaultdict(list)
_rate_limit_lock = Lock()


class RateLimitMiddleware(BaseHTTPMiddleware):
    """In-memory rate limit by client IP."""

    async def dispatch(self, request: Request, call_next):
        if _E2E_BYPASS_SECRET and request.headers.get("X-E2E-Test") == _E2E_BYPASS_SECRET:
            return await call_next(request)
        key = request.client.host if request.client else "unknown"
        now = time.monotonic()
        with _rate_limit_lock:
            _rate_limit[key] = [t for t in _rate_limit[key] if now - t < 60]
            if len(_rate_limit[key]) >= RATE_LIMIT_PER_MIN:
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Too many requests"},
                )
            _rate_limit[key].append(now)
        return await call_next(request)
