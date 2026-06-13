"""Proxy login risk disclosure to breeze-saas-portal."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from icici_breeze_backend.app.api.deps import get_current_user
from icici_breeze_backend.app.auth.context import RequestContext
from icici_breeze_backend.app.services.portal_login_disclosure import (
    fetch_portal_login_disclosure_current,
    post_portal_login_disclosure_accept,
    portal_login_disclosure_configured,
)

router = APIRouter(prefix="/api/login-disclosure", tags=["login-disclosure"])


class LoginDisclosureAcceptBody(BaseModel):
    disclosure_version: int = Field(..., ge=1)


@router.get("/public/current")
async def login_disclosure_public_current():
    """Unauthenticated read for preloading during ICICI login (content is not user-specific)."""
    if not portal_login_disclosure_configured():
        raise HTTPException(status_code=503, detail="Login disclosure unavailable (portal not configured)")
    doc = await fetch_portal_login_disclosure_current()
    if not doc:
        raise HTTPException(status_code=503, detail="Login disclosure unavailable from portal")
    return {**doc, "portal_configured": True}


@router.get("/current")
async def login_disclosure_current(_: RequestContext = Depends(get_current_user)):
    if not portal_login_disclosure_configured():
        raise HTTPException(status_code=503, detail="Login disclosure unavailable (portal not configured)")
    doc = await fetch_portal_login_disclosure_current()
    if not doc:
        raise HTTPException(status_code=503, detail="Login disclosure unavailable from portal")
    return {**doc, "portal_configured": True}


@router.post("/accept")
async def login_disclosure_accept(
    body: LoginDisclosureAcceptBody,
    ctx: RequestContext = Depends(get_current_user),
):
    user_id = (ctx.user_id or "").strip().upper()
    if not user_id:
        raise HTTPException(status_code=400, detail="User id unavailable")
    result = await post_portal_login_disclosure_accept(
        icici_user_id=user_id,
        disclosure_version=body.disclosure_version,
    )
    if not result.get("ok"):
        detail = str(result.get("detail") or "Login disclosure acceptance failed")
        code = int(result.get("status_code") or 502)
        raise HTTPException(status_code=code if 400 <= code < 600 else 502, detail=detail)
    return result
