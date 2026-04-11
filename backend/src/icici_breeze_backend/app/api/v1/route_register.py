"""Registration API under /api/register (Next serves HTML at /register)."""
import logging
import sqlite3

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.auth.auth_cookies import (
    BROKER_RECOVERY_BOOTSTRAP_COOKIE,
    BROKER_RECOVERY_TOKEN_COOKIE,
    COOKIE_MAX_AGE,
    RECOVERY_TOKEN_MAX_AGE,
)
from icici_breeze_backend.app.auth.credentials import (
    CredentialManager,
    decrypt_broker_recovery_token,
    encrypt_broker_recovery_bootstrap_cookie,
    encrypt_broker_recovery_token,
)
from icici_breeze_backend.app.auth.user_account import (
    create_direct_user_account,
    delete_user_account_by_user_id,
    get_account_auth_row,
    update_direct_app_password,
    user_account_exists_by_user_id,
    user_has_active_broker_credentials,
    verify_direct_account_password,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/register", tags=["register"])
cred_manager = CredentialManager(encryption_key=(cfg.JWT_SECRET or "").strip())
DB_PATH = cfg.DATA_PATH + cfg.USERS_DB

_MIN_PASSWORD_LEN = 8


class RegisterBootstrapResponse(BaseModel):
    direct_registration_available: bool = True


class RegisterDirectBody(BaseModel):
    user_id: str = Field(..., min_length=1)
    password: str = Field(..., min_length=_MIN_PASSWORD_LEN)
    api_key: str = Field(..., min_length=1)
    secret_fragment: str = Field(..., min_length=1)


class CorrectDirectBody(BaseModel):
    user_id: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)
    api_key: str = Field(..., min_length=1)
    secret_fragment: str = Field(..., min_length=1)


class RecoverStartBody(BaseModel):
    user_id: str = Field(..., min_length=1)


class RecoverCompleteBody(BaseModel):
    """ICICI verification is complete; only the new app password is required."""

    password: str = Field(..., min_length=_MIN_PASSWORD_LEN)


class DeleteAccountBody(BaseModel):
    user_id: str | None = None
    password: str | None = None


def _clear_recovery_token(response: JSONResponse) -> None:
    response.delete_cookie(key=BROKER_RECOVERY_TOKEN_COOKIE, path="/")


@router.get("/session", response_model=RegisterBootstrapResponse)
async def register_session():
    return RegisterBootstrapResponse(direct_registration_available=True)


@router.post("/direct", response_model=None)
async def register_direct_post(body: RegisterDirectBody):
    user_id = (body.user_id or "").strip()
    password = body.password or ""
    api_key = (body.api_key or "").strip()
    secret_fragment = (body.secret_fragment or "").strip()
    if len(password) < _MIN_PASSWORD_LEN:
        raise HTTPException(
            status_code=400,
            detail=f"Password must be at least {_MIN_PASSWORD_LEN} characters",
        )
    if not user_id or not api_key or not secret_fragment:
        raise HTTPException(status_code=400, detail="Missing fields")
    email = f"{user_id}@user.local"
    created_user = False
    try:
        with sqlite3.connect(DB_PATH) as conn:
            create_direct_user_account(conn, user_id, email, password, do_commit=True)
            created_user = True
        if not cred_manager.update_credentials(user_id, api_key, secret_fragment):
            raise HTTPException(status_code=400, detail="Could not save credentials")
    except HTTPException:
        if created_user:
            try:
                with sqlite3.connect(DB_PATH) as conn:
                    delete_user_account_by_user_id(conn, user_id)
            except Exception:
                logger.exception("rollback orphan direct user failed user_id=%s", user_id)
        raise
    except sqlite3.IntegrityError as e:
        logger.warning("register_direct_post integrity error: %s", e)
        raise HTTPException(status_code=409, detail="Account already exists")
    except Exception as e:
        if created_user:
            try:
                with sqlite3.connect(DB_PATH) as conn:
                    delete_user_account_by_user_id(conn, user_id)
            except Exception:
                logger.exception("rollback orphan direct user failed user_id=%s", user_id)
        logger.exception("register_direct_post failed: %s", e)
        raise HTTPException(status_code=500, detail="Registration failed")
    return JSONResponse({"ok": True, "redirect": "/login?registered=1"})


@router.post("/correct-direct", response_model=None)
async def register_correct_direct_post(body: CorrectDirectBody):
    user_id = (body.user_id or "").strip()
    password = body.password or ""
    api_key = (body.api_key or "").strip()
    secret_fragment = (body.secret_fragment or "").strip()
    if not user_id or not password or not api_key or not secret_fragment:
        raise HTTPException(status_code=400, detail="Missing fields")
    with sqlite3.connect(DB_PATH) as conn:
        if not verify_direct_account_password(conn, user_id, password):
            raise HTTPException(status_code=401, detail="Invalid credentials")
    if not cred_manager.update_credentials(user_id, api_key, secret_fragment):
        raise HTTPException(status_code=400, detail="Could not save credentials")
    return JSONResponse({"ok": True, "redirect": "/login?corrected=1"})


@router.post("/recover/start", response_model=None)
async def register_recover_start(request: Request, body: RecoverStartBody):
    user_id = (body.user_id or "").strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id is required")
    enc_key = (cfg.JWT_SECRET or "").strip()
    if not enc_key:
        raise HTTPException(status_code=503, detail="Server misconfiguration")

    with sqlite3.connect(DB_PATH) as conn:
        row = get_account_auth_row(conn, user_id)
        if not row:
            raise HTTPException(status_code=404, detail="No account found")
        _gid, provider, ph = row
        if (provider or "") != "direct" or not ph:
            raise HTTPException(
                status_code=400,
                detail="Recovery is only available for app-password accounts",
            )
        if not user_has_active_broker_credentials(conn, user_id):
            raise HTTPException(status_code=400, detail="No broker credentials on file")

    if getattr(cfg, "ICICI_BROKER_MODE", "live") == "mock":
        token_val = encrypt_broker_recovery_token(user_id, enc_key, RECOVERY_TOKEN_MAX_AGE)
        if not token_val:
            raise HTTPException(status_code=500, detail="Could not issue recovery token")
        response = JSONResponse({"ok": True, "redirect": "/register/recover-complete"})
        response.set_cookie(
            key=BROKER_RECOVERY_TOKEN_COOKIE,
            value=token_val,
            max_age=RECOVERY_TOKEN_MAX_AGE,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="lax",
            path="/",
        )
        return response

    cookie_val = encrypt_broker_recovery_bootstrap_cookie(user_id, enc_key)
    if not cookie_val:
        raise HTTPException(status_code=500, detail="Could not create recovery session")
    response = JSONResponse({"ok": True, "redirect": "/auth/icici-redirect"})
    response.set_cookie(
        key=BROKER_RECOVERY_BOOTSTRAP_COOKIE,
        value=cookie_val,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        path="/",
    )
    return response


@router.post("/recover/complete", response_model=None)
async def register_recover_complete(request: Request, body: RecoverCompleteBody):
    password = body.password or ""
    if len(password) < _MIN_PASSWORD_LEN:
        raise HTTPException(
            status_code=400,
            detail=f"Password must be at least {_MIN_PASSWORD_LEN} characters",
        )

    enc_key = (cfg.JWT_SECRET or "").strip()
    if not enc_key:
        raise HTTPException(status_code=503, detail="Server misconfiguration")
    raw = request.cookies.get(BROKER_RECOVERY_TOKEN_COOKIE)
    if not raw:
        raise HTTPException(status_code=401, detail="Recovery session expired or missing")
    decoded = decrypt_broker_recovery_token(raw, enc_key)
    if not decoded:
        raise HTTPException(status_code=401, detail="Recovery session invalid")
    user_id = (decoded[0] or "").strip()
    if not user_id:
        raise HTTPException(status_code=401, detail="Recovery session invalid")

    with sqlite3.connect(DB_PATH) as conn:
        if not user_account_exists_by_user_id(conn, user_id):
            raise HTTPException(status_code=404, detail="No account found")
        row = get_account_auth_row(conn, user_id)
        if not row or (row[1] or "") != "direct":
            raise HTTPException(status_code=400, detail="Invalid account type")

    with sqlite3.connect(DB_PATH) as conn:
        if not update_direct_app_password(conn, user_id, password):
            raise HTTPException(status_code=400, detail="Could not update app password")

    response = JSONResponse({"ok": True, "redirect": "/login?recovered=1"})
    _clear_recovery_token(response)
    return response


@router.post("/delete", response_model=None)
async def register_delete_post(body: DeleteAccountBody | None = None):
    body = body or DeleteAccountBody()
    uid = (body.user_id or "").strip()
    pwd = body.password or ""
    if not uid or not pwd:
        raise HTTPException(status_code=400, detail="user_id and password are required")
    with sqlite3.connect(DB_PATH) as conn:
        if not verify_direct_account_password(conn, uid, pwd):
            raise HTTPException(status_code=401, detail="Invalid credentials")
        delete_user_account_by_user_id(conn, uid)
    return JSONResponse({"ok": True, "redirect": "/login?deleted=1"})
