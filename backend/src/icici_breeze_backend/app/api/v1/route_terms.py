"""Proxy Terms & Conditions to breeze-saas-portal."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from icici_breeze_backend.app.api.deps import get_current_user
from icici_breeze_backend.app.auth.context import RequestContext
from icici_breeze_backend.app.services.portal_terms import (
    fetch_portal_terms_current,
    fetch_portal_terms_status,
    post_portal_terms_accept,
)

router = APIRouter(prefix="/api/terms", tags=["terms"])


class TermsAcceptBody(BaseModel):
    terms_version: int = Field(..., ge=1)


@router.get("/current")
async def terms_current(_: RequestContext = Depends(get_current_user)):
    doc = await fetch_portal_terms_current()
    if not doc:
        raise HTTPException(status_code=503, detail="Terms unavailable from portal")
    return doc


@router.get("/status")
async def terms_status(ctx: RequestContext = Depends(get_current_user)):
    user_id = (ctx.user_id or "").strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="User id unavailable")
    status = await fetch_portal_terms_status(icici_user_id=user_id)
    if status.get("needs_acceptance") and status.get("content_markdown"):
        doc = await fetch_portal_terms_current()
        if doc and isinstance(doc, dict):
            status["effective_date"] = doc.get("effective_date")
    return status


@router.post("/accept")
async def terms_accept(
    body: TermsAcceptBody,
    ctx: RequestContext = Depends(get_current_user),
):
    user_id = (ctx.user_id or "").strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="User id unavailable")
    result = await post_portal_terms_accept(
        icici_user_id=user_id,
        terms_version=body.terms_version,
    )
    if not result.get("ok"):
        status_code = int(result.get("status_code") or 502)
        if status_code < 400 or status_code >= 600:
            status_code = 502
        raise HTTPException(status_code=status_code, detail=result.get("detail") or "Terms acceptance failed")
    return {"ok": True, **{k: v for k, v in result.items() if k != "ok"}}
