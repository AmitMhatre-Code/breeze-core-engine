"""Proxy Terms & Conditions (read-only) to breeze-saas-portal."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from icici_breeze_backend.app.api.deps import get_current_user
from icici_breeze_backend.app.auth.context import RequestContext
from icici_breeze_backend.app.services.portal_terms import fetch_portal_terms_current

router = APIRouter(prefix="/api/terms", tags=["terms"])


@router.get("/current")
async def terms_current(_: RequestContext = Depends(get_current_user)):
    doc = await fetch_portal_terms_current()
    if not doc:
        raise HTTPException(status_code=503, detail="Terms unavailable from portal")
    return doc
