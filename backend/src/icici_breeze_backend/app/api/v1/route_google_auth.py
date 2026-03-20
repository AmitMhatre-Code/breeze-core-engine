"""Google OAuth routes: redirect and callback."""
import logging

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from urllib.parse import urlparse

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.api.frontend_redirect import frontend_url
from icici_breeze_backend.app.auth.google_oauth import get_oauth, is_google_oauth_configured
from icici_breeze_backend.app.auth.credentials import encrypt_google_oauth_cookie

logger = logging.getLogger(__name__)
router = APIRouter(tags=["google-auth"])

GOOGLE_OAUTH_COOKIE = "google_oauth_data"
COOKIE_MAX_AGE = 300  # 5 minutes


def _build_callback_url(request: Request) -> str:
    """
    Build absolute redirect_uri for Google. Must match a URI registered in Google Cloud and the same
    browser origin that holds the Starlette session (OAuth state). If the session is set on :3000
    (Next) but redirect_uri is :8000, Google sends the user back without the session cookie →
    mismatching_state.
    """
    explicit = (cfg.GOOGLE_OAUTH_REDIRECT_BASE_URL or "").strip().rstrip("/")
    if explicit:
        if "://" not in explicit:
            explicit = "http://" + explicit
        p = urlparse(explicit)
        if p.scheme and p.netloc:
            return f"{p.scheme}://{p.netloc}/auth/google/callback"
    base = str(request.base_url).rstrip("/")
    return f"{base}/auth/google/callback"


@router.get("/auth/google")
async def auth_google_redirect(request: Request, next: str = "/register"):
    """Redirect user to Google OAuth consent. `next` = where to go after callback (e.g. /register, /login)."""
    if not is_google_oauth_configured():
        logger.warning("Google OAuth not configured; redirecting to next without auth")
        return RedirectResponse(url=next, status_code=302)
    request.session["oauth_next"] = next
    oauth = get_oauth()
    google = oauth.create_client("google")
    redirect_uri = _build_callback_url(request)
    return await google.authorize_redirect(request, redirect_uri)


@router.get("/auth/google/callback")
async def auth_google_callback(request: Request):
    """Exchange code for token, fetch userinfo, set cookie, redirect to `next` from session."""
    if not is_google_oauth_configured():
        return RedirectResponse(url=frontend_url("/login?error=oauth_failed"), status_code=302)
    oauth = get_oauth()
    google = oauth.create_client("google")
    try:
        token = await google.authorize_access_token(request)
    except Exception as e:
        logger.warning("Google OAuth token exchange failed: %s", e)
        return RedirectResponse(url="/?error=oauth_failed", status_code=302)
    userinfo = token.get("userinfo") or {}
    google_id = userinfo.get("sub") or ""
    email = (userinfo.get("email") or "").strip()
    if not google_id:
        logger.warning("Google OAuth: no sub in userinfo")
        return RedirectResponse(url=frontend_url("/login?error=oauth_no_sub"), status_code=302)
    next_url = request.session.get("oauth_next", "/register")
    if "next" in request.query_params:
        next_url = request.query_params["next"]
    request.session.pop("oauth_next", None)
    enc_key = (cfg.JWT_SECRET or "").strip()
    cookie_val = encrypt_google_oauth_cookie(google_id, email, enc_key) if enc_key else ""
    if not cookie_val:
        logger.warning("Could not encrypt Google OAuth cookie; JWT_SECRET missing?")
        return RedirectResponse(url=frontend_url("/login?error=oauth_config"), status_code=302)
    response = RedirectResponse(url=next_url, status_code=302)
    response.set_cookie(
        key=GOOGLE_OAUTH_COOKIE,
        value=cookie_val,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        path="/",
    )
    return response


@router.get("/auth/icici-redirect")
async def auth_icici_redirect(request: Request):
    """After Google callback: resolve user_id from DB and redirect to ICICI Breeze (registered here so path lives with /auth/google)."""
    from icici_breeze_backend.app.api.v1.google_icici_redirect import redirect_to_icici_login

    return await redirect_to_icici_login(request)
