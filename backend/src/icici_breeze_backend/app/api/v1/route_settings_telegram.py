"""Settings > Telegram Alerts: deep-link based account linking + alert prefs."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.auth.context import RequestContext, get_request_context
from icici_breeze_backend.app.services.telegram_link_portal import (
    PortalLinkUnavailable,
    register_link_token,
)
from icici_breeze_backend.app.domain.telegram_alerts import (
    SetAlertsEnabledRequest,
    SetOnboardingDismissedRequest,
    TelegramLinkTokenResponse,
    TelegramStatusResponse,
)
from icici_breeze_backend.app.repositories import user_telegram as repo

router = APIRouter(prefix="/api/settings/telegram", tags=["settings"])


def _bot_username() -> str:
    return cfg.TELEGRAM_BOT_USERNAME or ""


@router.get("/status", response_model=TelegramStatusResponse)
async def get_status(ctx: RequestContext = Depends(get_request_context)):
    status = repo.get_status(ctx.user_id)
    return TelegramStatusResponse(
        connected=status["connected"],
        telegram_username=status["telegram_username"],
        alerts_enabled=status["alerts_enabled"],
        onboarding_dismissed=status["onboarding_dismissed"],
        link_token=status["link_token"],
        link_token_expires_at=status["link_token_expires_at"],
        bot_username=_bot_username(),
        bot_configured=bool(_bot_username()),
    )


@router.post("/link-token", response_model=TelegramLinkTokenResponse)
async def create_link_token(ctx: RequestContext = Depends(get_request_context)):
    """Issue a deep-link token and register it with the portal.

    Registration is awaited rather than fired off in the background: the portal
    owns the single Telegram consumer, so a token it doesn't know about routes
    nowhere. Surfacing the failure here means the user is told to retry instead
    of being handed a QR code that silently does nothing.
    """
    token, expires_at = repo.generate_link_token(ctx.user_id)
    try:
        await register_link_token(token)
    except PortalLinkUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail="Could not start Telegram linking right now. Please try again in a moment.",
        ) from exc
    return TelegramLinkTokenResponse(
        link_token=token,
        link_token_expires_at=expires_at,
        bot_username=_bot_username(),
    )


@router.delete("/unlink")
async def unlink(ctx: RequestContext = Depends(get_request_context)):
    repo.unlink(ctx.user_id)
    return {"ok": True}


@router.put("/onboarding-dismissed", response_model=TelegramStatusResponse)
async def set_onboarding_dismissed(
    body: SetOnboardingDismissedRequest,
    ctx: RequestContext = Depends(get_request_context),
):
    repo.set_onboarding_dismissed(ctx.user_id, body.dismissed)
    return await get_status(ctx)


@router.put("/alerts-enabled", response_model=TelegramStatusResponse)
async def set_alerts_enabled(
    body: SetAlertsEnabledRequest,
    ctx: RequestContext = Depends(get_request_context),
):
    repo.set_alerts_enabled(ctx.user_id, body.enabled)
    return await get_status(ctx)
