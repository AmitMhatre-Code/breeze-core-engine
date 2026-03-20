"""Registration API under /api/register (Next serves HTML at /register)."""
import sqlite3
import logging

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.auth.credentials import CredentialManager, decrypt_google_oauth_cookie
from icici_breeze_backend.app.auth.user_account import ensure_user_account, get_user_id_by_google_id
from icici_breeze_backend.app.api.v1.route_google_auth import GOOGLE_OAUTH_COOKIE
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/register", tags=["register"])
cred_manager = CredentialManager(encryption_key=(cfg.JWT_SECRET or "").strip())
DB_PATH = cfg.DATA_PATH + "db.sqlite3"


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


class RegisterSubmitBody(BaseModel):
    user_id: str
    api_key: str
    secret_fragment: str


class CorrectBootstrapResponse(BaseModel):
    google_authenticated: bool
    has_account: bool | None = None
    user_id: str | None = None


class DeleteBootstrapResponse(BaseModel):
    google_authenticated: bool
    has_account: bool | None = None
    user_id: str | None = None


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
                raise HTTPException(status_code=409, detail="Different ICICI id linked; use correct-credentials flow")
            ensure_user_account(conn, google_id, user_id, email or f"{user_id}@user.local")
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
    with sqlite3.connect(DB_PATH) as conn:
        ensure_user_account(conn, google_id, user_id, email or "")
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
async def register_delete_post(request: Request):
    google_id, _ = _get_google_data_from_cookie(request)
    if not google_id:
        raise HTTPException(status_code=401, detail="Sign in with Google first")
    with sqlite3.connect(DB_PATH) as conn:
        user_id = get_user_id_by_google_id(conn, google_id)
    if not user_id:
        raise HTTPException(status_code=400, detail="No account")
    cred_manager.revoke_credentials(user_id)
    response = JSONResponse({"ok": True, "redirect": "/login?deleted=1"})
    _clear_oauth_cookie(response)
    return response
