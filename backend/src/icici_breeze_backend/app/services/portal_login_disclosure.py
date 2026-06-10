"""Proxy login risk disclosure calls to breeze-saas-portal."""
from __future__ import annotations

import logging

import httpx

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.services.portal_deployment_login import _public_ip_from_origin
from icici_breeze_backend.app.services.portal_policy_token import portal_host_allowed

logger = logging.getLogger(__name__)

_TIMEOUT_SEC = 10.0


def portal_login_disclosure_configured() -> bool:
    return bool((cfg.PORTAL_API_BASE_URL or "").strip()) and bool(
        (cfg.DEPLOYMENT_LICENSE_KEY or "").strip()
    )


def _portal_base_payload(*, icici_user_id: str) -> dict[str, str] | None:
    public_ip = _public_ip_from_origin()
    if not public_ip:
        logger.warning("portal login disclosure skipped: no IPv4 PUBLIC_FRONTEND_ORIGIN")
        return None
    base = (cfg.PORTAL_API_BASE_URL or "").strip().rstrip("/")
    if not base or not portal_host_allowed(base):
        logger.warning("portal login disclosure skipped: PORTAL_API_BASE_URL host not allowed")
        return None
    key = (cfg.DEPLOYMENT_LICENSE_KEY or "").strip()
    if not key:
        return None
    user_id = icici_user_id.strip().upper()
    if not user_id:
        return None
    return {
        "license_key": key,
        "public_ip": public_ip,
        "icici_user_id": user_id,
    }


async def fetch_portal_login_disclosure_current() -> dict | None:
    if not portal_login_disclosure_configured():
        return None
    base = (cfg.PORTAL_API_BASE_URL or "").strip().rstrip("/")
    if not portal_host_allowed(base):
        return None
    url = f"{base}/api/public/login-disclosure/current"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SEC) as client:
            resp = await client.get(url)
            if not resp.is_success:
                logger.warning("portal login-disclosure/current failed: %s", resp.status_code)
                return None
            data = resp.json()
            return data if isinstance(data, dict) else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("portal login-disclosure/current error: %s", exc)
        return None


async def post_portal_login_disclosure_accept(
    *, icici_user_id: str, disclosure_version: int
) -> dict:
    if not portal_login_disclosure_configured():
        return {"ok": False, "detail": "Portal not configured"}
    payload = _portal_base_payload(icici_user_id=icici_user_id)
    if not payload:
        return {"ok": False, "detail": "Portal login disclosure unavailable"}
    base = (cfg.PORTAL_API_BASE_URL or "").strip().rstrip("/")
    url = f"{base}/api/public/login-disclosure/accept"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SEC) as client:
            resp = await client.post(
                url,
                json={
                    "license_key": payload["license_key"],
                    "public_ip": payload["public_ip"],
                    "icici_user_id": payload["icici_user_id"],
                    "disclosure_version": disclosure_version,
                },
            )
            if not resp.is_success:
                detail = "Login disclosure acceptance failed"
                try:
                    body = resp.json()
                    if isinstance(body, dict) and body.get("detail"):
                        detail = str(body["detail"])
                except Exception:  # noqa: BLE001
                    pass
                return {"ok": False, "detail": detail, "status_code": resp.status_code}
            data = resp.json()
            return {"ok": True, **(data if isinstance(data, dict) else {})}
    except Exception as exc:  # noqa: BLE001
        logger.warning("portal login-disclosure/accept error: %s", exc)
        return {"ok": False, "detail": str(exc)}
