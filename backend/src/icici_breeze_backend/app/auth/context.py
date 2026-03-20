"""Request-scoped user context injected from JWT tokens."""
import logging
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Optional
from fastapi import Request

from icici_breeze_backend.app.auth.credentials import decrypt_from_session_cookie

_logger = logging.getLogger(__name__)

ICICI_BROKER_TOKEN_COOKIE = "icici_broker_token"
ACCESS_TOKEN_COOKIE = "access_token"
CREDENTIAL_FULL_SECRET_COOKIE = "icici_full_secret"
LOGIN_USER_ID_COOKIE = "login_user_id"  # set before ICICI redirect; must survive slow broker login
LOGIN_USER_ID_COOKIE_MAX_AGE = 3600  # 1 hour (ICICI login + 2FA often exceeds a few minutes)

_broker_token_ctx: ContextVar[Optional[str]] = ContextVar("broker_token", default=None)
_full_secret_ctx: ContextVar[Optional[str]] = ContextVar("full_secret", default=None)
_breeze_session_ctx: ContextVar[Optional[object]] = ContextVar("breeze_session", default=None)
_user_id_ctx: ContextVar[Optional[str]] = ContextVar("user_id", default=None)


def set_breeze_session_for_request(session: Optional[object]) -> None:
    _breeze_session_ctx.set(session)


def get_breeze_session_for_request() -> Optional[object]:
    return _breeze_session_ctx.get()


def set_broker_token_for_request(token: Optional[str]) -> None:
    _broker_token_ctx.set(token)


def get_broker_token_for_request() -> Optional[str]:
    return _broker_token_ctx.get()


def set_full_secret_for_request(secret: Optional[str]) -> None:
    _full_secret_ctx.set(secret)


def get_full_secret_for_request() -> Optional[str]:
    return _full_secret_ctx.get()


def get_current_user_id() -> Optional[str]:
    """Return the current request's user_id (ICICI) for API call counting. None if not in an authenticated request."""
    return _user_id_ctx.get()


@dataclass
class RequestContext:
    """User context extracted from JWT token. google_id = identity; user_id = ICICI for API calls."""
    user_id: str
    username: str
    roles: list[str]
    is_authenticated: bool
    google_id: Optional[str] = None
    request_id: Optional[str] = None
    ip_address: Optional[str] = None
    broker_token: Optional[str] = None


def extract_user_context(request: Request) -> Optional[RequestContext]:
    auth: str = request.headers.get("authorization") or request.headers.get("Authorization")
    token = None
    if auth and auth.lower().startswith("bearer "):
        token = auth.split(" ", 1)[1].strip()
    if not token:
        token = request.cookies.get(ACCESS_TOKEN_COOKIE)
    if not token:
        return None
    # Read from icici_breeze_backend.app.core.config (tests set both core.config and app.core.config)
    import icici_breeze_backend.app.core.config as app_cfg
    import icici_breeze_backend.core.config as core_cfg
    secret = (app_cfg.JWT_SECRET or core_cfg.JWT_SECRET or "").strip()
    if not secret:
        return None
    from icici_breeze_backend.app.auth.jwt_handler import JWTHandler
    handler = JWTHandler(secret_key=secret)
    try:
        payload = handler.validate_token(token)
    except Exception as e:
        _logger.debug("JWT validation failed: %s", e)
        return None
    if not payload:
        return None
    if not getattr(payload, "google_id", None):
        _logger.debug("Token missing google_id; legacy token rejected")
        return None
    broker_token = request.cookies.get(ICICI_BROKER_TOKEN_COOKIE)
    set_broker_token_for_request(broker_token)
    enc_key = (secret or "").strip()
    enc_cookie = request.cookies.get(CREDENTIAL_FULL_SECRET_COOKIE)
    full_secret = decrypt_from_session_cookie(enc_cookie or "", enc_key) if enc_key and enc_cookie else None
    set_full_secret_for_request(full_secret)
    _user_id_ctx.set(payload.user_id)
    ctx = RequestContext(
        user_id=payload.user_id,
        username=payload.username,
        roles=payload.roles,
        is_authenticated=True,
        google_id=getattr(payload, "google_id", None),
        request_id=None,
        ip_address=request.client.host if request.client else None,
        broker_token=broker_token,
    )
    request.state.user_id = ctx.user_id
    return ctx


async def get_request_context(request: Request) -> RequestContext:
    from fastapi import HTTPException
    ctx = extract_user_context(request)
    if not ctx or not ctx.is_authenticated:
        raise HTTPException(status_code=401, detail="Invalid or missing authentication token")
    return ctx


def get_optional_request_context(request: Request) -> Optional[RequestContext]:
    return extract_user_context(request)


class RedirectToLogin(Exception):
    pass


async def get_request_context_or_redirect(request: Request) -> RequestContext:
    ctx = extract_user_context(request)
    if not ctx or not ctx.is_authenticated:
        raise RedirectToLogin()
    if not ctx.broker_token:
        raise RedirectToLogin()
    return ctx
