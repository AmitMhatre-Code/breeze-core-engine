"""FastAPI dependencies (get_current_user, get_db, etc.)."""
from typing import Optional

from fastapi import Request

from icici_breeze_backend.app.auth.context import (
    RequestContext,
    extract_user_context,
    get_request_context,
    get_request_context_or_redirect,
    get_optional_request_context,
    RedirectToLogin,
    ICICI_BROKER_TOKEN_COOKIE,
    ACCESS_TOKEN_COOKIE,
    CREDENTIAL_FULL_SECRET_COOKIE,
)


async def get_current_user(request: Request) -> RequestContext:
    """Dependency: require authenticated user. Raises 401 if not authenticated."""
    return await get_request_context(request)


async def get_current_user_or_redirect(request: Request) -> RequestContext:
    """Dependency: require authenticated user. Raises RedirectToLogin if not (→ 302 to /)."""
    return await get_request_context_or_redirect(request)


def get_optional_user(request: Request) -> Optional[RequestContext]:
    """Extract context if present; returns None if not authenticated."""
    return get_optional_request_context(request)


def require_admin(ctx: RequestContext = None):
    """Dependency: require admin role. Use with Depends(get_current_user)."""
    from fastapi import Depends, HTTPException
    if ctx is None:
        return None  # Will be injected
    if "admin" not in (getattr(ctx, "roles", None) or []):
        raise HTTPException(status_code=403, detail="Admin role required")
    return ctx
