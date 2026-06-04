"""Fire-and-forget login heartbeat to breeze-saas-portal for registered deployments."""
from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import urlparse

import httpx

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.services.deployment_license_status import (
    record_portal_verify_failure,
    update_from_verified_policy,
)
from icici_breeze_backend.app.services.portal_policy_token import (
    parse_verified_portal_body,
    portal_host_allowed,
)

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


async def _post_deployment_login(
    public_ip: str,
    *,
    icici_user_id: str | None = None,
) -> None:
    base = (cfg.PORTAL_API_BASE_URL or "").strip().rstrip("/")
    key = (cfg.DEPLOYMENT_LICENSE_KEY or "").strip()
    if not base:
        return
    if not portal_host_allowed(base):
        logger.warning("portal deployment-login skipped: PORTAL_API_BASE_URL host not allowed")
        record_portal_verify_failure()
        return
    url = f"{base}/api/public/deployment-login"
    payload: dict[str, str] = {"public_ip": public_ip}
    if key:
        payload["license_key"] = key
    if icici_user_id:
        payload["icici_user_id"] = icici_user_id.strip().upper()
    try:
        async with httpx.AsyncClient(timeout=_LOGIN_TIMEOUT_SEC) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 403:
                logger.warning("portal deployment-login rejected: %s", resp.text[:500])
                record_portal_verify_failure()
                return
            if not resp.is_success:
                record_portal_verify_failure()
                return
            try:
                raw = resp.json()
            except Exception:  # noqa: BLE001
                raw = None
            policy = parse_verified_portal_body(raw if isinstance(raw, dict) else None, public_ip=public_ip)
            if policy is None:
                record_portal_verify_failure()
                return
            update_from_verified_policy(policy, source="deployment-login")
    except Exception as exc:  # noqa: BLE001
        logger.warning("portal deployment-login failed: %s", exc)
        record_portal_verify_failure()


def notify_portal_deployment_login(*, icici_user_id: str | None = None) -> None:
    """Schedule portal login heartbeat; no-op when env or public IP is unavailable."""
    public_ip = _public_ip_from_origin()
    if not public_ip:
        return
    if not (cfg.PORTAL_API_BASE_URL or "").strip():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(_post_deployment_login(public_ip, icici_user_id=icici_user_id))
