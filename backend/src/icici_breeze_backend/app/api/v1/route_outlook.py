"""GenAI market outlook routes."""
from __future__ import annotations

import logging

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.auth.ai_provider_keys import AiProviderKeyManager
from icici_breeze_backend.app.auth.outlook_preferences import OutlookPreferencesManager
from icici_breeze_backend.app.auth.context import RequestContext, get_request_context
from icici_breeze_backend.app.services.outlook_service import OutlookError, OutlookService

router = APIRouter(prefix="/api/outlook", tags=["outlook"])
key_manager = AiProviderKeyManager(encryption_key=(cfg.JWT_SECRET or "").strip())
outlook_preferences_manager = OutlookPreferencesManager()
service = OutlookService()
logger = logging.getLogger(__name__)


def _status_for_outlook_error(exc: OutlookError) -> int:
    if exc.code == "invalid_api_key":
        return 401
    if exc.code in ("quota_exceeded", "rate_limit_exceeded"):
        return 429
    if exc.code == "provider_error":
        if exc.status_code and 500 <= exc.status_code <= 599:
            return exc.status_code
        return 502
    return 400


def _get_enabled_ai_provider(user_id: str):
    cfg_row = key_manager.get(user_id)
    if not cfg_row or not cfg_row.enabled:
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "no_llm_api_key_configured",
                "message": "No API key set for any LLMs.",
            },
        )
    return cfg_row


@router.get("/market")
async def market_outlook(
    force_refresh: bool = Query(default=False),
    max_age_minutes: int = Query(default=120, ge=5, le=720),
    ctx: RequestContext = Depends(get_request_context),
):
    ai_cfg = _get_enabled_ai_provider(ctx.user_id)
    prefs = outlook_preferences_manager.get(ctx.user_id)
    try:
        return service.get_market_outlook(
            ai_cfg=ai_cfg,
            force_refresh=force_refresh,
            max_age_minutes=max_age_minutes,
            feeds=[(f.name, f.url) for f in prefs.feeds],
            prompt_template=prefs.prompt_template,
            system_prompt=prefs.system_prompt,
        )
    except OutlookError as exc:
        cached = service.get_cached()
        if cached:
            return {
                **cached,
                "warning": {
                    "error_code": exc.code,
                    "message": exc.message,
                    "stale_response_served": True,
                    "upstream_status": exc.status_code,
                },
            }
        raise HTTPException(
            status_code=_status_for_outlook_error(exc),
            detail={"message": exc.message, "error_code": exc.code},
        ) from exc


@router.get("/market/stream")
async def market_outlook_stream(
    max_age_minutes: int = Query(default=120, ge=5, le=720),
    interval_seconds: int = Query(default=30, ge=10, le=300),
    ctx: RequestContext = Depends(get_request_context),
):
    ai_cfg = _get_enabled_ai_provider(ctx.user_id)
    prefs = outlook_preferences_manager.get(ctx.user_id)

    async def event_generator():
        # Send periodic updates so the dashboard can stay fresh without polling.
        while True:
            try:
                payload = service.get_market_outlook(
                    ai_cfg=ai_cfg,
                    force_refresh=False,
                    max_age_minutes=max_age_minutes,
                    feeds=[(f.name, f.url) for f in prefs.feeds],
                    prompt_template=prefs.prompt_template,
                    system_prompt=prefs.system_prompt,
                )
                yield f"event: outlook\ndata: {json.dumps(payload)}\n\n"
            except OutlookError as exc:
                cached = service.get_cached()
                if cached:
                    payload = {
                        **cached,
                        "warning": {
                            "error_code": exc.code,
                            "message": exc.message,
                            "stale_response_served": True,
                            "upstream_status": exc.status_code,
                        },
                    }
                    yield f"event: outlook\ndata: {json.dumps(payload)}\n\n"
                else:
                    err = {
                        "message": exc.message,
                        "error_code": exc.code,
                        "status_code": _status_for_outlook_error(exc),
                    }
                    yield f"event: error\ndata: {json.dumps(err)}\n\n"
            yield "event: ping\ndata: {}\n\n"
            await asyncio.sleep(interval_seconds)

    headers = {"Cache-Control": "no-cache", "Connection": "keep-alive"}
    return StreamingResponse(event_generator(), media_type="text/event-stream", headers=headers)
