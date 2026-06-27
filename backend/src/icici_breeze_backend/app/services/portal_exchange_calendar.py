"""Fetch exchange calendar from breeze-saas-portal (Breeze Console Admin Settings)."""
from __future__ import annotations

import logging

import httpx

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.services.portal_policy_token import portal_host_allowed

logger = logging.getLogger(__name__)

_TIMEOUT_SEC = 10.0


def portal_exchange_calendar_configured() -> bool:
    base = (cfg.PORTAL_API_BASE_URL or "").strip()
    return bool(base) and portal_host_allowed(base.rstrip("/"))


async def fetch_console_exchange_calendar() -> dict | None:
    base = (cfg.PORTAL_API_BASE_URL or "").strip().rstrip("/")
    if not base or not portal_host_allowed(base):
        logger.warning("console exchange calendar skipped: PORTAL_API_BASE_URL not allowed")
        return None
    url = f"{base}/api/public/exchange-calendar/current"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SEC) as client:
            resp = await client.get(url)
            if not resp.is_success:
                logger.warning("console exchange calendar failed: %s", resp.status_code)
                return None
            data = resp.json()
            return data if isinstance(data, dict) else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("console exchange calendar error: %s", exc)
        return None
