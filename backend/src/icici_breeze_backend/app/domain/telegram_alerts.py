"""Request/response schemas for Telegram alert linking (Settings > Telegram Alerts)."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class TelegramStatusResponse(BaseModel):
    connected: bool
    telegram_username: Optional[str] = None
    alerts_enabled: bool
    onboarding_dismissed: bool
    link_token: Optional[str] = None
    link_token_expires_at: Optional[str] = None
    bot_username: str
    bot_configured: bool


class TelegramLinkTokenResponse(BaseModel):
    link_token: str
    link_token_expires_at: str
    bot_username: str


class SetOnboardingDismissedRequest(BaseModel):
    dismissed: bool


class SetAlertsEnabledRequest(BaseModel):
    enabled: bool
