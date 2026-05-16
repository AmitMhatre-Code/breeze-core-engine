"""Fire-and-forget login heartbeat to breeze-saas-portal for registered deployments."""
from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import urlparse

import httpx

import icici_breeze_backend.app.core.config as cfg

logger = logging.getLogger(__name__)

_IPV4_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")
_LOGIN_TIMEOUT_SEC = 3.0


def _public_ip_from_origin() -> str | None:
    origin = (cfg.PUBLIC_FRONTEND_ORIGIN or "").strip()
    if not origin:
        return None
    try:
        host = urlparse(origin).hostname
    except Exception:
        return None
    if not host or not _IPV4_RE.match(host):
        return None
    return host


async def _post_deployment_login(public_ip: str) -> None:
    base = (cfg.PORTAL_API_BASE_URL or "").strip().rstrip("/")
    key = (cfg.DEPLOYMENT_LICENSE_KEY or "").strip()
    if not base or not key:
        return
    url = f"{base}/api/public/deployment-login"
    try:
        async with httpx.AsyncClient(timeout=_LOGIN_TIMEOUT_SEC) as client:
            await client.post(
                url,
                json={"license_key": key, "public_ip": public_ip},
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("portal deployment-login failed: %s", exc)


def notify_portal_deployment_login() -> None:
    """Schedule portal login heartbeat; no-op when env or public IP is unavailable."""
    public_ip = _public_ip_from_origin()
    if not public_ip:
        return
    if not (cfg.DEPLOYMENT_LICENSE_KEY or "").strip() or not (cfg.PORTAL_API_BASE_URL or "").strip():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(_post_deployment_login(public_ip))
