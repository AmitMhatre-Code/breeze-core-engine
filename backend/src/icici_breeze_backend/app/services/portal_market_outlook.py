"""Fetch the admin-managed global Market Outlook from breeze-saas-portal.

Unlike portal_deployment_heartbeat.py, this call is unauthenticated and
carries no license key or signed policy token -- the portal serves the same
non-sensitive outlook payload to every deployment regardless of license
status. The SSRF host-allowlist check is reused; JWT signature verification
is not, since there is no signed response here.

The fetch client and the route that serves it run in the same process (unlike
the portal side, where the worker is a separate OS process from the API), so
a simple in-memory, thread-lock-protected cache is a legitimate bridge here.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any

import httpx

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.services.portal_policy_token import portal_host_allowed

logger = logging.getLogger(__name__)

_FETCH_TIMEOUT_SEC = 10.0
_DEFAULT_REFRESH_INTERVAL_SEC = 300

_lock = threading.Lock()
_cached_payload: dict[str, Any] | None = None
_fetched_at: datetime | None = None


def refresh_interval_sec() -> int:
    try:
        return max(60, int(os.environ.get("MARKET_OUTLOOK_REFRESH_INTERVAL_SEC", "") or _DEFAULT_REFRESH_INTERVAL_SEC))
    except ValueError:
        return _DEFAULT_REFRESH_INTERVAL_SEC


def market_outlook_loop_enabled() -> bool:
    """No license/public-IP gate -- this endpoint is public and non-sensitive."""
    return bool((cfg.PORTAL_API_BASE_URL or "").strip())


async def fetch_market_outlook_from_portal() -> dict[str, Any] | None:
    base = (cfg.PORTAL_API_BASE_URL or "").strip().rstrip("/")
    if not base:
        return None
    if not portal_host_allowed(base):
        logger.warning("market outlook fetch skipped: PORTAL_API_BASE_URL host not allowed")
        return None
    url = f"{base}/api/public/market-outlook/current"
    try:
        async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT_SEC) as client:
            resp = await client.get(url)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, dict) else None
    except httpx.HTTPError as exc:
        logger.warning("market outlook fetch failed: %s", exc)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("market outlook fetch unexpected error: %s", exc)
        return None


async def refresh_once() -> bool:
    """On success, replace the cache. On failure, leave the previous cache untouched."""
    payload = await fetch_market_outlook_from_portal()
    if payload is None:
        return False
    global _cached_payload, _fetched_at
    with _lock:
        _cached_payload = payload
        _fetched_at = datetime.now(timezone.utc)
    return True


def get_cached_market_outlook() -> tuple[dict[str, Any], float] | None:
    """Returns (payload, age_seconds), or None if nothing has been fetched yet this process."""
    with _lock:
        if _cached_payload is None or _fetched_at is None:
            return None
        age = (datetime.now(timezone.utc) - _fetched_at).total_seconds()
        return _cached_payload, age


async def run_market_outlook_refresh_loop() -> None:
    interval = refresh_interval_sec()
    logger.info("market outlook refresh loop started (interval=%ss)", interval)
    while True:
        try:
            await refresh_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("market outlook refresh loop error: %s", exc)
        await asyncio.sleep(refresh_interval_sec())
