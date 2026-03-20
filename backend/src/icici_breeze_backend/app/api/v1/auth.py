"""Authentication API routes."""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from icici_breeze_backend.app.api.deps import get_current_user, RequestContext, ICICI_BROKER_TOKEN_COOKIE, ACCESS_TOKEN_COOKIE, CREDENTIAL_FULL_SECRET_COOKIE
from icici_breeze_backend.app.domain.auth import (
    AdminRotateRequest,
    AdminRevokeRequest,
)
from icici_breeze_backend.app.domain.responses import AdminRevokeResponse, AdminRotateResponse, LogoutResponse
from icici_breeze_backend.app.services.auth_service import rotate_credentials, revoke_credentials
from icici_breeze_backend.audit.logger import AuditLogger, OperationType
import icici_breeze_backend.app.core.config as cfg

router = APIRouter(tags=["Authentication"])


async def _require_admin(ctx: RequestContext = Depends(get_current_user)) -> RequestContext:
    """Dependency: require admin role."""
    if "admin" not in (getattr(ctx, "roles", None) or []):
        raise HTTPException(status_code=403, detail="Admin role required")
    return ctx


@router.post("/auth/login", include_in_schema=False)
async def login_endpoint_disabled():
    """ICICI token + user id login removed; use Google OAuth then ICICI (see /auth/google, /auth/icici-redirect)."""
    raise HTTPException(
        status_code=410,
        detail="Login with ICICI token is disabled. Sign in with Google, then complete ICICI login.",
    )


@router.post("/auth/logout", response_model=LogoutResponse)
async def logout_endpoint(ctx: RequestContext = Depends(get_current_user), request_obj: Request = None):
    """Logout and clear auth cookies."""
    if not ctx:
        raise HTTPException(status_code=401, detail="Not authenticated")

    user_id = ctx.user_id
    ip_address = request_obj.client.host if request_obj.client else None
    request_id = getattr(request_obj.state, "correlation_id", None)

    from icici_breeze_backend.app.services.breeze_session_cache import evict
    evict(user_id, ctx.broker_token or "")

    response = JSONResponse(content=LogoutResponse().model_dump())
    response.delete_cookie(key=ICICI_BROKER_TOKEN_COOKIE, path="/")
    response.delete_cookie(key=ACCESS_TOKEN_COOKIE, path="/")
    response.delete_cookie(key=CREDENTIAL_FULL_SECRET_COOKIE, path="/")

    AuditLogger(None).log_logout(user_id, request_id=request_id, ip_address=ip_address)
    return response


@router.post("/auth/admin/rotate", tags=["Admin"], response_model=AdminRotateResponse)
async def admin_rotate_endpoint(req: AdminRotateRequest, ctx: RequestContext = Depends(_require_admin)):
    """Admin: rotate credentials for a user."""
    try:
        ok = rotate_credentials(req.target_user_id, req.new_secret_fragment)
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    if not ok:
        raise HTTPException(status_code=400, detail="Rotation failed")
    AuditLogger(None).log_operation(
        ctx.user_id, OperationType.CREDENTIAL_ROTATION,
        "Credential", req.target_user_id,
        request_id=getattr(ctx, "request_id", None),
    )
    return AdminRotateResponse(message="Credentials rotated for user", target_user_id=req.target_user_id)


@router.post("/auth/admin/revoke", tags=["Admin"], response_model=AdminRevokeResponse)
async def admin_revoke_endpoint(req: AdminRevokeRequest, ctx: RequestContext = Depends(_require_admin)):
    """Admin: revoke credentials for a user."""
    try:
        ok = revoke_credentials(req.target_user_id)
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    if not ok:
        raise HTTPException(status_code=404, detail="No active credentials to revoke")
    AuditLogger(None).log_operation(
        ctx.user_id, OperationType.CREDENTIAL_REVOCATION,
        "Credential", req.target_user_id,
        request_id=getattr(ctx, "request_id", None),
    )
    return AdminRevokeResponse(message="Credentials revoked for user", target_user_id=req.target_user_id)
