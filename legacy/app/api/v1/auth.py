"""Authentication API routes."""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from app.api.deps import get_current_user, RequestContext, ICICI_BROKER_TOKEN_COOKIE, ACCESS_TOKEN_COOKIE, CREDENTIAL_FULL_SECRET_COOKIE
from app.domain.auth import (
    LoginRequest,
    LoginResponse,
    AdminRotateRequest,
    AdminRevokeRequest,
)
from app.domain.responses import AdminRevokeResponse, AdminRotateResponse, LogoutResponse
from app.services.auth_service import login as auth_login, rotate_credentials, revoke_credentials
from app.auth.credentials import encrypt_for_session_cookie
from audit.logger import AuditLogger, OperationType
import app.core.config as cfg

router = APIRouter(tags=["Authentication"])


def _seconds_until_midnight_ist() -> int:
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    ist = ZoneInfo("Asia/Kolkata")
    now = datetime.now(ist)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    return int((midnight - now).total_seconds())


async def _require_admin(ctx: RequestContext = Depends(get_current_user)) -> RequestContext:
    """Dependency: require admin role."""
    if "admin" not in (getattr(ctx, "roles", None) or []):
        raise HTTPException(status_code=403, detail="Admin role required")
    return ctx


@router.post("/auth/login", response_model=LoginResponse)
async def login_endpoint(request: LoginRequest, request_obj: Request):
    """Authenticate with ICICI broker token and credential challenge."""
    ip_address = request_obj.client.host if request_obj.client else None
    request_id = getattr(request_obj.state, "correlation_id", None)

    try:
        response, full_secret = await auth_login(
            request, ip_address=ip_address, request_id=request_id
        )
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))

    result = JSONResponse(content=response.model_dump())
    result.set_cookie(
        key=ICICI_BROKER_TOKEN_COOKIE,
        value=request.icici_token,
        httponly=True,
        secure=cfg.COOKIE_SECURE,
        samesite="lax",
        max_age=_seconds_until_midnight_ist(),
        path="/",
    )
    result.set_cookie(
        key=ACCESS_TOKEN_COOKIE,
        value=response.access_token,
        httponly=True,
        secure=cfg.COOKIE_SECURE,
        samesite="lax",
        max_age=cfg.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
    )
    enc_key = (cfg.JWT_SECRET or "").strip()
    if enc_key and full_secret:
        enc_secret = encrypt_for_session_cookie(full_secret, enc_key)
        if enc_secret:
            result.set_cookie(
                key=CREDENTIAL_FULL_SECRET_COOKIE,
                value=enc_secret,
                httponly=True,
                secure=cfg.COOKIE_SECURE,
                samesite="lax",
                max_age=_seconds_until_midnight_ist(),
                path="/",
            )
    return result


@router.post("/auth/logout", response_model=LogoutResponse)
async def logout_endpoint(ctx: RequestContext = Depends(get_current_user), request_obj: Request = None):
    """Logout and clear auth cookies."""
    if not ctx:
        raise HTTPException(status_code=401, detail="Not authenticated")

    user_id = ctx.user_id
    ip_address = request_obj.client.host if request_obj.client else None
    request_id = getattr(request_obj.state, "correlation_id", None)

    from app.services.breeze_session_cache import evict
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
