"""After Google OAuth: DB lookup → ICICI Breeze login URL (shared by /login and /auth/icici-redirect)."""
import sqlite3
import urllib.parse

from fastapi import Request
from fastapi.responses import RedirectResponse

from icici_breeze_backend.app.api.frontend_redirect import frontend_url
from icici_breeze_backend.app.api.v1.route_google_auth import GOOGLE_OAUTH_COOKIE
from icici_breeze_backend.app.auth.context import LOGIN_USER_ID_COOKIE, LOGIN_USER_ID_COOKIE_MAX_AGE
from icici_breeze_backend.app.auth.credentials import decrypt_google_oauth_cookie
from icici_breeze_backend.app.auth.user_account import get_user_id_by_google_id
import icici_breeze_backend.app.core.config as cfg


async def redirect_to_icici_login(request: Request) -> RedirectResponse:
    """Read Google OAuth cookie, resolve user_id in SQLite, redirect to ICICI login."""
    from icici_breeze_backend.app.api.v1 import home as home_module

    breeze = home_module.breeze
    cookie = request.cookies.get(GOOGLE_OAUTH_COOKIE)
    if not cookie:
        return RedirectResponse(url=frontend_url("/login?error=oauth_invalid"), status_code=302)
    enc_key = (cfg.JWT_SECRET or "").strip()
    data = decrypt_google_oauth_cookie(cookie, enc_key) if enc_key else None
    if not data:
        return RedirectResponse(url=frontend_url("/login?error=oauth_invalid"), status_code=302)
    google_id, _ = data
    with sqlite3.connect(cfg.DATA_PATH + cfg.USERS_DB) as conn:
        user_id = get_user_id_by_google_id(conn, google_id)
    if not user_id:
        return RedirectResponse(url=frontend_url("/login?error=no_account"), status_code=302)
    login_url = breeze.get_login_url(user_id)
    if not login_url:
        return RedirectResponse(url=frontend_url("/login?error=no_credentials"), status_code=302)
    response = RedirectResponse(urllib.parse.quote(login_url, safe=":/?=&"), status_code=307)
    response.set_cookie(
        key=LOGIN_USER_ID_COOKIE,
        value=user_id,
        max_age=LOGIN_USER_ID_COOKIE_MAX_AGE,
        httponly=True,
        secure=cfg.COOKIE_SECURE,
        samesite="lax",
        path="/",
    )
    return response
