"""Authentication API routes."""
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from icici_breeze_backend.app.api.deps import get_current_user, get_optional_user, RequestContext, ICICI_BROKER_TOKEN_COOKIE, ACCESS_TOKEN_COOKIE, CREDENTIAL_FULL_SECRET_COOKIE
from icici_breeze_backend.app.auth.auth_cookies import COOKIE_MAX_AGE, DIRECT_ICICI_COOKIE
from icici_breeze_backend.app.auth.credentials import encrypt_direct_icici_cookie
from icici_breeze_backend.app.auth.user_account import verify_direct_account_password, get_google_id_by_user_id
from icici_breeze_backend.app.domain.auth import (
    AdminRotateRequest,
    AdminRevokeRequest,
    DirectLoginRequest,
)
from icici_breeze_backend.app.domain.responses import AdminRevokeResponse, AdminRotateResponse, LogoutResponse
from icici_breeze_backend.app.services.auth_service import rotate_credentials, revoke_credentials
from icici_breeze_backend.audit.logger import AuditLogger, OperationType
import icici_breeze_backend.app.core.config as cfg

router = APIRouter(tags=["Authentication"])


@router.get("/auth/session", include_in_schema=False)
async def auth_session_status(request: Request):
    """Lightweight check: valid JWT + ICICI broker cookie. Used by SPA landing redirect."""
    ctx = get_optional_user(request)
    if ctx and ctx.broker_token:
        user_id = (ctx.user_id or "").strip().upper() or None
        return JSONResponse(content={"authenticated": True, "user_id": user_id})
    return JSONResponse(content={"authenticated": False}, status_code=401)


async def _require_admin(ctx: RequestContext = Depends(get_current_user)) -> RequestContext:
    """Dependency: require admin role."""
    if "admin" not in (getattr(ctx, "roles", None) or []):
        raise HTTPException(status_code=403, detail="Admin role required")
    return ctx


@router.post("/auth/login", include_in_schema=False)
async def login_endpoint_disabled():
    """ICICI token + user id login removed; use app password sign-in then complete ICICI login."""
    raise HTTPException(
        status_code=410,
        detail="Login with ICICI token is disabled. Use app password sign-in, then complete ICICI login.",
    )


@router.get("/auth/icici-redirect", include_in_schema=False)
async def auth_icici_redirect(request: Request):
    """After direct-login bootstrap: resolve user_id and redirect to ICICI Breeze."""
    from icici_breeze_backend.app.api.v1.google_icici_redirect import redirect_to_icici_login

    return await redirect_to_icici_login(request)


@router.post("/auth/direct-login", include_in_schema=False)
async def auth_direct_login(request: Request, body: DirectLoginRequest):
    """Validate ICICI user id + app password; set short-lived cookie and continue ICICI broker login."""
    uid = (body.user_id or "").strip()
    pwd = body.password or ""
    if not uid or not pwd:
        raise HTTPException(status_code=400, detail="user_id and password are required")
    enc_key = (cfg.JWT_SECRET or "").strip()
    if not enc_key:
        raise HTTPException(status_code=503, detail="Server misconfiguration")
    db_path = cfg.DATA_PATH + cfg.USERS_DB
    with sqlite3.connect(db_path) as conn:
        if not verify_direct_account_password(conn, uid, pwd):
            raise HTTPException(status_code=401, detail="Invalid credentials")
    if getattr(cfg, "ICICI_BROKER_MODE", "live") == "mock":
        from icici_breeze_backend.app.auth.jwt_handler import JWTHandler
        from icici_breeze_backend.app.api.v1 import home as home_module
        from icici_breeze_backend.app.auth.credentials import CredentialManager

        with sqlite3.connect(db_path) as conn:
            google_id = get_google_id_by_user_id(conn, uid)
        handler = JWTHandler(
            secret_key=cfg.JWT_SECRET,
            access_token_expire_minutes=cfg.JWT_ACCESS_TOKEN_EXPIRE_MINUTES,
        )
        access_token = handler.create_access_token(uid, uid, google_id=google_id)
        mgr = CredentialManager(encryption_key=enc_key)
        full_secret = (mgr.reconstruct_full_api_secret(uid, "") or "").strip() or None
        response = JSONResponse({"ok": True, "redirect": "/dashboard"})
        home_module._set_auth_cookies(
            response,
            getattr(cfg, "ICICI_MOCK_BROKER_COOKIE_VALUE", "mock"),
            access_token,
            full_secret,
            uid,
        )
        from icici_breeze_backend.app.services.portal_deployment_login import notify_portal_deployment_login
        from icici_breeze_backend.app.services.system_chain_health import (
            start_prefetch_for_new_broker_session,
        )

        notify_portal_deployment_login(icici_user_id=uid.strip().upper() if uid else None)
        start_prefetch_for_new_broker_session(
            uid, getattr(cfg, "ICICI_MOCK_BROKER_COOKIE_VALUE", "mock")
        )
        return response
    cookie_val = encrypt_direct_icici_cookie(uid, enc_key)
    if not cookie_val:
        raise HTTPException(status_code=500, detail="Could not create session")
    response = JSONResponse({"ok": True, "redirect": "/auth/icici-redirect"})
    response.set_cookie(
        key=DIRECT_ICICI_COOKIE,
        value=cookie_val,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        path="/",
    )
    return response


@router.post("/auth/logout", response_model=LogoutResponse)
async def logout_endpoint(ctx: RequestContext = Depends(get_current_user), request_obj: Request = None):
    """Logout and clear auth cookies."""
    if not ctx:
        raise HTTPException(status_code=401, detail="Not authenticated")

    user_id = ctx.user_id
    ip_address = request_obj.client.host if request_obj.client else None
    request_id = getattr(request_obj.state, "correlation_id", None)

    from icici_breeze_backend.app.repositories.broker_session import clear_broker_session_token
    from icici_breeze_backend.app.services.breeze_session_cache import evict
    from icici_breeze_backend.app.services.broker_snapshot_cache import evict as evict_snapshot

    evict(user_id, ctx.broker_token or "")
    evict_snapshot(user_id, ctx.broker_token or "")
    clear_broker_session_token(user_id)

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
