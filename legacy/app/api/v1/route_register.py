"""Registration, correction, and delete flows. All require Google OAuth."""
import sqlite3
import logging

from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

import app.core.config as cfg
from app.auth.credentials import (
    CredentialManager,
    decrypt_google_oauth_cookie,
)
from app.auth.user_account import (
    ensure_user_account,
    get_user_id_by_google_id,
)
from app.api.v1.route_google_auth import GOOGLE_OAUTH_COOKIE

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="templates")
router = APIRouter(tags=["register"])
cred_manager = CredentialManager(encryption_key=(cfg.JWT_SECRET or "").strip())
DB_PATH = cfg.DATA_PATH + "db.sqlite3"


def _get_google_data_from_cookie(request: Request) -> tuple[str | None, str | None]:
    """Returns (google_id, email) or (None, None) if invalid/missing."""
    cookie = request.cookies.get(GOOGLE_OAUTH_COOKIE)
    if not cookie:
        return None, None
    enc_key = (cfg.JWT_SECRET or "").strip()
    if not enc_key:
        return None, None
    result = decrypt_google_oauth_cookie(cookie, enc_key)
    return result if result else (None, None)


def _clear_oauth_cookie(response: RedirectResponse) -> None:
    response.delete_cookie(key=GOOGLE_OAUTH_COOKIE, path="/")


# --- Registration ---

@router.get("/register")
async def register_get(request: Request):
    """Show 'Sign in with Google' or, if cookie present, the ICICI registration form."""
    google_id, email = _get_google_data_from_cookie(request)
    if not google_id:
        return templates.TemplateResponse(
            "register.html",
            {"request": request, "show_form": False, "display_theme": "dark"},
        )
    return templates.TemplateResponse(
        "register.html",
        {
            "request": request,
            "show_form": True,
            "display_theme": "dark",
        },
    )


@router.post("/register")
async def register_post(
    request: Request,
    user_id: str = Form(...),
    api_key: str = Form(...),
    secret_fragment: str = Form(...),
):
    """Create user_account and credentials. Requires valid Google OAuth cookie."""
    google_id, email = _get_google_data_from_cookie(request)
    if not google_id:
        return RedirectResponse(url="/auth/google?next=/register", status_code=302)
    user_id = (user_id or "").strip()
    api_key = (api_key or "").strip()
    secret_fragment = (secret_fragment or "").strip()
    if not user_id or not api_key or not secret_fragment:
        return RedirectResponse(url="/register?error=missing", status_code=302)
    try:
        with sqlite3.connect(DB_PATH) as conn:
            existing = get_user_id_by_google_id(conn, google_id)
            if existing and existing != user_id:
                return RedirectResponse(url="/register/correct", status_code=302)
            ensure_user_account(conn, google_id, user_id, email or f"{user_id}@user.local")
            if not cred_manager.update_credentials(user_id, api_key, secret_fragment):
                return RedirectResponse(url="/register?error=cred_failed", status_code=302)
    except sqlite3.IntegrityError as e:
        logger.warning("register_post integrity error: %s", e)
        return RedirectResponse(url="/register?error=exists", status_code=302)
    except Exception as e:
        logger.exception("register_post failed: %s", e)
        return RedirectResponse(url="/register?error=server", status_code=302)
    response = RedirectResponse(url="/?registered=1", status_code=302)
    _clear_oauth_cookie(response)
    return response


# --- Correction ---

@router.get("/register/correct")
async def register_correct_get(request: Request):
    """After Google OAuth, show correction form or 'No account linked'."""
    google_id, _ = _get_google_data_from_cookie(request)
    if not google_id:
        return templates.TemplateResponse(
            "register_correct.html",
            {"request": request, "has_account": None, "display_theme": "dark"},
        )
    with sqlite3.connect(DB_PATH) as conn:
        user_id = get_user_id_by_google_id(conn, google_id)
    if not user_id:
        return templates.TemplateResponse(
            "register_correct.html",
            {"request": request, "has_account": False, "display_theme": "dark"},
        )
    return templates.TemplateResponse(
        "register_correct.html",
        {
            "request": request,
            "has_account": True,
            "user_id": user_id,
            "display_theme": "dark",
        },
    )


@router.post("/register/correct")
async def register_correct_post(
    request: Request,
    user_id: str = Form(...),
    api_key: str = Form(...),
    secret_fragment: str = Form(...),
):
    """Update credentials. Requires valid Google OAuth cookie and matching account."""
    google_id, email = _get_google_data_from_cookie(request)
    if not google_id:
        return RedirectResponse(url="/auth/google?next=/register/correct", status_code=302)
    with sqlite3.connect(DB_PATH) as conn:
        existing_user_id = get_user_id_by_google_id(conn, google_id)
    if not existing_user_id:
        return RedirectResponse(url="/register/correct", status_code=302)
    user_id = (user_id or "").strip()
    api_key = (api_key or "").strip()
    secret_fragment = (secret_fragment or "").strip()
    if not user_id or not api_key or not secret_fragment:
        return RedirectResponse(url="/register/correct?error=missing", status_code=302)
    if user_id != existing_user_id:
        return RedirectResponse(url="/register/correct?error=mismatch", status_code=302)
    if not cred_manager.update_credentials(user_id, api_key, secret_fragment):
        return RedirectResponse(url="/register/correct?error=cred_failed", status_code=302)
    with sqlite3.connect(DB_PATH) as conn:
        ensure_user_account(conn, google_id, user_id, email or "")
    response = RedirectResponse(url="/?corrected=1", status_code=302)
    _clear_oauth_cookie(response)
    return response


# --- Delete ---

@router.get("/register/delete")
async def register_delete_get(request: Request):
    """After Google OAuth, show delete confirmation or 'No account linked'."""
    google_id, _ = _get_google_data_from_cookie(request)
    if not google_id:
        return templates.TemplateResponse(
            "register_delete.html",
            {"request": request, "has_account": None, "display_theme": "dark"},
        )
    with sqlite3.connect(DB_PATH) as conn:
        user_id = get_user_id_by_google_id(conn, google_id)
    if not user_id:
        return templates.TemplateResponse(
            "register_delete.html",
            {"request": request, "has_account": False, "display_theme": "dark"},
        )
    return templates.TemplateResponse(
        "register_delete.html",
        {
            "request": request,
            "has_account": True,
            "user_id": user_id,
            "display_theme": "dark",
        },
    )


@router.post("/register/delete")
async def register_delete_post(request: Request):
    """Revoke credentials. Requires valid Google OAuth cookie and matching account."""
    google_id, _ = _get_google_data_from_cookie(request)
    if not google_id:
        return RedirectResponse(url="/auth/google?next=/register/delete", status_code=302)
    with sqlite3.connect(DB_PATH) as conn:
        user_id = get_user_id_by_google_id(conn, google_id)
    if not user_id:
        return RedirectResponse(url="/register/delete", status_code=302)
    cred_manager.revoke_credentials(user_id)
    response = RedirectResponse(url="/?deleted=1", status_code=302)
    _clear_oauth_cookie(response)
    return response
