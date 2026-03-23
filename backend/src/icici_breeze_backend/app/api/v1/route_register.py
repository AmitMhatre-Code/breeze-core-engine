"""Registration API under /api/register (Next serves HTML at /register)."""
import logging
import sqlite3

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.api.v1.route_google_auth import GOOGLE_OAUTH_COOKIE
from icici_breeze_backend.app.auth.credentials import CredentialManager, decrypt_google_oauth_cookie
from icici_breeze_backend.app.auth.user_account import (
    create_direct_user_account,
    delete_user_account_by_user_id,
    ensure_user_account,
    get_user_id_by_google_id,
    verify_direct_account_password,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/register", tags=["register"])
cred_manager = CredentialManager(encryption_key=(cfg.JWT_SECRET or "").strip())
DB_PATH = cfg.DATA_PATH + cfg.USERS_DB

_MIN_PASSWORD_LEN = 8


def _get_google_data_from_cookie(request: Request) -> tuple[str | None, str | None]:
    cookie = request.cookies.get(GOOGLE_OAUTH_COOKIE)
    if not cookie:
        return None, None
    enc_key = (cfg.JWT_SECRET or "").strip()
    if not enc_key:
        return None, None
    result = decrypt_google_oauth_cookie(cookie, enc_key)
    return result if result else (None, None)


def _clear_oauth_cookie(response: JSONResponse) -> None:
    response.delete_cookie(key=GOOGLE_OAUTH_COOKIE, path="/")


class RegisterBootstrapResponse(BaseModel):
    google_authenticated: bool
    direct_registration_available: bool = True


class RegisterSubmitBody(BaseModel):
    user_id: str
    api_key: str
    secret_fragment: str


class RegisterDirectBody(BaseModel):
    user_id: str = Field(..., min_length=1)
    password: str = Field(..., min_length=_MIN_PASSWORD_LEN)
    api_key: str = Field(..., min_length=1)
    secret_fragment: str = Field(..., min_length=1)


class CorrectBootstrapResponse(BaseModel):
    google_authenticated: bool
    has_account: bool | None = None
    user_id: str | None = None


class DeleteBootstrapResponse(BaseModel):
    google_authenticated: bool
    has_account: bool | None = None
    user_id: str | None = None
    direct_delete_available: bool = True


class DeleteAccountBody(BaseModel):
    user_id: str | None = None
    password: str | None = None


@router.get("/session", response_model=RegisterBootstrapResponse)
async def register_session(request: Request):
    google_id, _ = _get_google_data_from_cookie(request)
    return RegisterBootstrapResponse(google_authenticated=bool(google_id))


@router.post("")
async def register_post(request: Request, body: RegisterSubmitBody):
    google_id, email = _get_google_data_from_cookie(request)
    if not google_id:
        raise HTTPException(status_code=401, detail="Sign in with Google first")
    user_id = (body.user_id or "").strip()
    api_key = (body.api_key or "").strip()
    secret_fragment = (body.secret_fragment or "").strip()
    if not user_id or not api_key or not secret_fragment:
        raise HTTPException(status_code=400, detail="Missing fields")
    try:
        with sqlite3.connect(DB_PATH) as conn:
            existing = get_user_id_by_google_id(conn, google_id)
            if existing and existing != user_id:
                raise HTTPException(
                    status_code=409,
                    detail="Different ICICI id linked; use correct-credentials flow",
                )
            ensure_user_account(
                conn,
                google_id,
                user_id,
                email or f"{user_id}@user.local",
            )
            if not cred_manager.update_credentials(user_id, api_key, secret_fragment):
                raise HTTPException(status_code=400, detail="Could not save credentials")
    except HTTPException:
        raise
    except sqlite3.IntegrityError as e:
        logger.warning("register_post integrity error: %s", e)
        raise HTTPException(status_code=409, detail="Account already exists")
    except Exception as e:
        logger.exception("register_post failed: %s", e)
        raise HTTPException(status_code=500, detail="Registration failed")
    response = JSONResponse({"ok": True, "redirect": "/login?registered=1"})
    _clear_oauth_cookie(response)
    return response


@router.post("/direct", response_model=None)
async def register_direct_post(body: RegisterDirectBody):
    user_id = (body.user_id or "").strip()
    password = body.password or ""
    api_key = (body.api_key or "").strip()
    secret_fragment = (body.secret_fragment or "").strip()
    if len(password) < _MIN_PASSWORD_LEN:
        raise HTTPException(status_code=400, detail=f"Password must be at least {_MIN_PASSWORD_LEN} characters")
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


@router.get("/correct/session", response_model=CorrectBootstrapResponse)
async def register_correct_session(request: Request):
    google_id, _ = _get_google_data_from_cookie(request)
    if not google_id:
        return CorrectBootstrapResponse(google_authenticated=False, has_account=None)
    with sqlite3.connect(DB_PATH) as conn:
        user_id = get_user_id_by_google_id(conn, google_id)
    if not user_id:
        return CorrectBootstrapResponse(google_authenticated=True, has_account=False)
    return CorrectBootstrapResponse(google_authenticated=True, has_account=True, user_id=user_id)


@router.post("/correct", response_model=None)
async def register_correct_post(request: Request, body: RegisterSubmitBody):
    google_id, email = _get_google_data_from_cookie(request)
    if not google_id:
        raise HTTPException(status_code=401, detail="Sign in with Google first")
    with sqlite3.connect(DB_PATH) as conn:
        existing_user_id = get_user_id_by_google_id(conn, google_id)
    if not existing_user_id:
        raise HTTPException(status_code=400, detail="No account for this Google user")
    user_id = (body.user_id or "").strip()
    api_key = (body.api_key or "").strip()
    secret_fragment = (body.secret_fragment or "").strip()
    if not user_id or not api_key or not secret_fragment:
        raise HTTPException(status_code=400, detail="Missing fields")
    if user_id != existing_user_id:
        raise HTTPException(status_code=400, detail="User id mismatch")
    if not cred_manager.update_credentials(user_id, api_key, secret_fragment):
        raise HTTPException(status_code=400, detail="Could not save credentials")
    em = (email or "").strip() or f"{user_id}@user.local"
    with sqlite3.connect(DB_PATH) as conn:
        ensure_user_account(conn, google_id, user_id, em)
    response = JSONResponse({"ok": True, "redirect": "/login?corrected=1"})
    _clear_oauth_cookie(response)
    return response


@router.get("/delete/session", response_model=DeleteBootstrapResponse)
async def register_delete_session(request: Request):
    google_id, _ = _get_google_data_from_cookie(request)
    if not google_id:
        return DeleteBootstrapResponse(google_authenticated=False, has_account=None)
    with sqlite3.connect(DB_PATH) as conn:
        user_id = get_user_id_by_google_id(conn, google_id)
    if not user_id:
        return DeleteBootstrapResponse(google_authenticated=True, has_account=False)
    return DeleteBootstrapResponse(google_authenticated=True, has_account=True, user_id=user_id)


@router.post("/delete", response_model=None)
async def register_delete_post(request: Request, body: DeleteAccountBody | None = None):
    google_id, _ = _get_google_data_from_cookie(request)
    if google_id:
        with sqlite3.connect(DB_PATH) as conn:
            user_id = get_user_id_by_google_id(conn, google_id)
        if not user_id:
            raise HTTPException(status_code=400, detail="No account")
        with sqlite3.connect(DB_PATH) as conn:
            delete_user_account_by_user_id(conn, user_id)
        response = JSONResponse({"ok": True, "redirect": "/login?deleted=1"})
        _clear_oauth_cookie(response)
        return response

    # Direct account: require user_id + password (no Google cookie)
    body = body or DeleteAccountBody()
    uid = (body.user_id or "").strip()
    pwd = body.password or ""
    if not uid or not pwd:
        raise HTTPException(status_code=400, detail="user_id and password are required for direct accounts")
    with sqlite3.connect(DB_PATH) as conn:
        if not verify_direct_account_password(conn, uid, pwd):
            raise HTTPException(status_code=401, detail="Invalid credentials")
        delete_user_account_by_user_id(conn, uid)
    return JSONResponse({"ok": True, "redirect": "/login?deleted=1"})
