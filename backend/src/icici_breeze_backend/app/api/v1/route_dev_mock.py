"""Dev-only routes when ICICI_BROKER_MODE=mock (set HttpOnly broker cookie after Google JWT)."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.api.deps import ICICI_BROKER_TOKEN_COOKIE
from icici_breeze_backend.app.auth.context import extract_user_context

router = APIRouter(tags=["dev-mock"])


def _mock_guard():
    if getattr(cfg, "ICICI_BROKER_MODE", "live") != "mock":
        raise HTTPException(status_code=404, detail="Not found")


@router.post("/dev/mock-broker-cookie", include_in_schema=False)
async def post_mock_broker_cookie(request: Request):
    """Set `icici_broker_token` for an already-JWT-authenticated session (e.g. after Google OAuth)."""
    _mock_guard()
    ctx = extract_user_context(request)
    if not ctx or not ctx.is_authenticated:
        raise HTTPException(status_code=401, detail="JWT required; sign in with Google first")
    token = getattr(cfg, "ICICI_MOCK_BROKER_COOKIE_VALUE", "mock") or "mock"
    response = JSONResponse({"ok": True, "redirect": "/dashboard"})
    response.set_cookie(
        key=ICICI_BROKER_TOKEN_COOKIE,
        value=token,
        httponly=True,
        secure=cfg.COOKIE_SECURE,
        samesite="lax",
        max_age=86400,
        path="/",
    )
    return response
