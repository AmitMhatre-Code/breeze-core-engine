"""Rate limit middleware. E2E bypass when X-E2E-Test matches E2E_RATE_LIMIT_BYPASS_SECRET."""
import os
import time
from collections import defaultdict
from threading import Lock

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

try:
    # Counts mutating requests and /auth/* only -- safe reads are never counted, so a
    # dashboard that fans out a dozen GETs on load (or a background poll) can't spend the
    # budget. The threat this guards is auth brute-force; order place/modify/cancel is
    # already serialized behind a per-user lock elsewhere.
    RATE_LIMIT_PER_MIN = int(os.environ.get("RATE_LIMIT_PER_MIN", "240"))
except (ValueError, TypeError):
    RATE_LIMIT_PER_MIN = 240

# When set, requests with header X-E2E-Test: <this value> are not counted (for Playwright E2E).
_E2E_BYPASS_SECRET = (os.environ.get("E2E_RATE_LIMIT_BYPASS_SECRET") or "").strip()

# Non-mutating methods -- exempt regardless of volume (see RATE_LIMIT_PER_MIN note).
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# Liveness / scrape probes hit these through the same proxy on a timer -- never counted.
_EXEMPT_PATHS = frozenset({"/health", "/metrics"})

_rate_limit: dict[str, list[float]] = defaultdict(list)
_rate_limit_lock = Lock()


def _client_key(request: Request) -> str:
    """The real client IP, not the proxy's.

    Every topology fronts uvicorn with a proxy (nginx in compose/prod, Next's rewrites in
    dev), so ``request.client.host`` is always a loopback address and would collapse the
    whole deployment onto one bucket. nginx passes the true origin in ``X-Real-IP`` /
    ``X-Forwarded-For``. This is a coarse in-memory abuse guard on a single-tenant box, not
    a security boundary, so the headers are trusted as-is without a trusted-proxy allowlist.
    """
    real_ip = (request.headers.get("x-real-ip") or "").strip()
    if real_ip:
        return real_ip
    first_hop = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if first_hop:
        return first_hop
    return request.client.host if request.client else "unknown"


def _should_count(request: Request) -> bool:
    if request.url.path in _EXEMPT_PATHS:
        return False
    if request.method == "OPTIONS":
        return False  # CORS preflight carries no credentials and isn't an abuse vector
    if request.method in _SAFE_METHODS and not request.url.path.startswith("/auth/"):
        return False
    return True


class RateLimitMiddleware(BaseHTTPMiddleware):
    """In-memory rate limit by client IP, applied to mutating requests and /auth/*."""

    async def dispatch(self, request: Request, call_next):
        if _E2E_BYPASS_SECRET and request.headers.get("X-E2E-Test") == _E2E_BYPASS_SECRET:
            return await call_next(request)
        if not _should_count(request):
            return await call_next(request)
        key = _client_key(request)
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
