"""Resolve user_id from broker-recovery or direct-login cookie → ICICI Breeze login URL."""
import logging
import sqlite3
import urllib.parse

from fastapi import Request
from fastapi.responses import RedirectResponse

from icici_breeze_backend.app.api.frontend_redirect import frontend_url
from icici_breeze_backend.app.auth.auth_cookies import (
    BROKER_RECOVERY_BOOTSTRAP_COOKIE,
    BROKER_RECOVERY_PENDING_COOKIE,
    BROKER_RECOVERY_TOKEN_COOKIE,
    DIRECT_ICICI_COOKIE,
    RECOVERY_TOKEN_MAX_AGE,
)
from icici_breeze_backend.app.auth.context import LOGIN_USER_ID_COOKIE, LOGIN_USER_ID_COOKIE_MAX_AGE
from icici_breeze_backend.app.auth.credentials import (
    decrypt_broker_recovery_bootstrap_cookie,
    decrypt_direct_icici_cookie,
    encrypt_broker_recovery_pending_cookie,
    encrypt_broker_recovery_token,
)
from icici_breeze_backend.app.auth.user_account import get_account_auth_row
import icici_breeze_backend.app.core.config as cfg

logger = logging.getLogger(__name__)


def _delete_cookie(response: RedirectResponse, key: str) -> None:
    response.delete_cookie(key=key, path="/")


def _set_pending_recovery_cookies(response: RedirectResponse, user_id: str) -> None:
    enc_key = (cfg.JWT_SECRET or "").strip()
    if not enc_key:
        return
    pending_val = encrypt_broker_recovery_pending_cookie(user_id, enc_key)
    if pending_val:
        response.set_cookie(
            key=BROKER_RECOVERY_PENDING_COOKIE,
            value=pending_val,
            max_age=LOGIN_USER_ID_COOKIE_MAX_AGE,
            httponly=True,
            secure=cfg.COOKIE_SECURE,
            samesite="lax",
            path="/",
        )
    response.set_cookie(
        key=LOGIN_USER_ID_COOKIE,
        value=user_id,
        max_age=LOGIN_USER_ID_COOKIE_MAX_AGE,
        httponly=True,
        secure=cfg.COOKIE_SECURE,
        samesite="lax",
        path="/",
    )


async def redirect_to_icici_login(request: Request) -> RedirectResponse:
    """Resolve user_id from broker recovery bootstrap or direct-login cookie → ICICI Breeze."""
    from icici_breeze_backend.app.api.v1 import home as home_module

    breeze = home_module.breeze
    enc_key = (cfg.JWT_SECRET or "").strip()
    user_id: str | None = None
    from_recovery_bootstrap = False

    r_cookie = request.cookies.get(BROKER_RECOVERY_BOOTSTRAP_COOKIE)
    if r_cookie and enc_key:
        candidate = decrypt_broker_recovery_bootstrap_cookie(r_cookie, enc_key)
        if candidate:
            with sqlite3.connect(cfg.DATA_PATH + cfg.USERS_DB) as conn:
                row = get_account_auth_row(conn, candidate)
                if row and (row[1] or "") == "direct" and row[2]:
                    user_id = candidate
                    from_recovery_bootstrap = True

    if not user_id:
        d_cookie = request.cookies.get(DIRECT_ICICI_COOKIE)
        if d_cookie and enc_key:
            candidate = decrypt_direct_icici_cookie(d_cookie, enc_key)
            if candidate:
                with sqlite3.connect(cfg.DATA_PATH + cfg.USERS_DB) as conn:
                    row = get_account_auth_row(conn, candidate)
                    if row and (row[1] or "") == "direct" and row[2]:
                        user_id = candidate

    if not user_id:
        return RedirectResponse(url=frontend_url("/login?error=oauth_invalid"), status_code=302)
    login_url = breeze.get_login_url(user_id)
    if not login_url:
        return RedirectResponse(url=frontend_url("/login?error=no_credentials"), status_code=302)

    if getattr(cfg, "ICICI_BROKER_MODE", "live") == "mock":
        if from_recovery_bootstrap:
            token_val = encrypt_broker_recovery_token(user_id, enc_key, RECOVERY_TOKEN_MAX_AGE)
            if not token_val:
                return RedirectResponse(url=frontend_url("/login?error=oauth_config"), status_code=302)
            response = RedirectResponse(url=frontend_url("/register/recover-complete"), status_code=302)
            response.set_cookie(
                key=BROKER_RECOVERY_TOKEN_COOKIE,
                value=token_val,
                max_age=RECOVERY_TOKEN_MAX_AGE,
                httponly=True,
                secure=cfg.COOKIE_SECURE,
                samesite="lax",
                path="/",
            )
            _delete_cookie(response, DIRECT_ICICI_COOKIE)
            _delete_cookie(response, BROKER_RECOVERY_BOOTSTRAP_COOKIE)
            _delete_cookie(response, BROKER_RECOVERY_PENDING_COOKIE)
            logger.info(
                "ICICI_BROKER_MODE=mock: broker recovery bootstrap → recover-complete user_id=%s",
                user_id,
            )
            return response

        from icici_breeze_backend.app.auth.jwt_handler import JWTHandler
        from icici_breeze_backend.app.auth.user_account import get_google_id_by_user_id
        from icici_breeze_backend.app.api.v1.home import _set_auth_cookies

        with sqlite3.connect(cfg.DATA_PATH + cfg.USERS_DB) as conn:
            google_id = get_google_id_by_user_id(conn, user_id)
        handler = JWTHandler(
            secret_key=cfg.JWT_SECRET,
            access_token_expire_minutes=cfg.JWT_ACCESS_TOKEN_EXPIRE_MINUTES,
        )
        access_token = handler.create_access_token(user_id, user_id, google_id=google_id)
        mock_broker = getattr(cfg, "ICICI_MOCK_BROKER_COOKIE_VALUE", "mock") or "mock"
        response = RedirectResponse(url=frontend_url("/dashboard"), status_code=302)
        _set_auth_cookies(response, mock_broker, access_token, None, user_id)
        _delete_cookie(response, DIRECT_ICICI_COOKIE)
        _delete_cookie(response, BROKER_RECOVERY_BOOTSTRAP_COOKIE)
        _delete_cookie(response, BROKER_RECOVERY_PENDING_COOKIE)
        logger.info("ICICI_BROKER_MODE=mock: completed login without ICICI redirect user_id=%s", user_id)
        return response

    response = RedirectResponse(urllib.parse.quote(login_url, safe=":/?=&"), status_code=307)
    _delete_cookie(response, DIRECT_ICICI_COOKIE)
    _delete_cookie(response, BROKER_RECOVERY_BOOTSTRAP_COOKIE)
    if from_recovery_bootstrap:
        _set_pending_recovery_cookies(response, user_id)
    else:
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
