"""Proxy Terms & Conditions calls to breeze-saas-portal."""
from __future__ import annotations

import logging

import httpx

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.services.portal_deployment_login import _public_ip_from_origin
from icici_breeze_backend.app.services.portal_policy_token import portal_host_allowed

logger = logging.getLogger(__name__)

_TIMEOUT_SEC = 10.0


def _portal_configured() -> bool:
    return bool((cfg.PORTAL_API_BASE_URL or "").strip()) and bool(
        (cfg.DEPLOYMENT_LICENSE_KEY or "").strip()
    )


def _portal_base_payload(*, icici_user_id: str | None = None) -> dict[str, str] | None:
    public_ip = _public_ip_from_origin()
    if not public_ip:
        logger.warning("portal terms skipped: no IPv4 PUBLIC_FRONTEND_ORIGIN")
        return None
    base = (cfg.PORTAL_API_BASE_URL or "").strip().rstrip("/")
    if not base or not portal_host_allowed(base):
        logger.warning("portal terms skipped: PORTAL_API_BASE_URL host not allowed")
        return None
    key = (cfg.DEPLOYMENT_LICENSE_KEY or "").strip()
    if not key:
        return None
    payload: dict[str, str] = {"license_key": key, "public_ip": public_ip}
    if icici_user_id:
        payload["icici_user_id"] = icici_user_id.strip().upper()
    return payload


def portal_terms_skipped_status() -> dict:
    return {
        "needs_acceptance": False,
        "current_version": None,
        "accepted_version": None,
        "accepted_at": None,
        "content_markdown": None,
        "portal_configured": False,
    }


async def fetch_portal_terms_current() -> dict | None:
    if not _portal_configured():
        return None
    base = (cfg.PORTAL_API_BASE_URL or "").strip().rstrip("/")
    if not portal_host_allowed(base):
        return None
    url = f"{base}/api/public/terms/current"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SEC) as client:
            resp = await client.get(url)
            if not resp.is_success:
                logger.warning("portal terms/current failed: %s", resp.status_code)
                return None
            data = resp.json()
            return data if isinstance(data, dict) else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("portal terms/current error: %s", exc)
        return None


async def fetch_portal_terms_status(*, icici_user_id: str) -> dict:
    if not _portal_configured():
        return portal_terms_skipped_status()
    payload = _portal_base_payload(icici_user_id=icici_user_id)
    if not payload:
        return portal_terms_skipped_status()
    base = (cfg.PORTAL_API_BASE_URL or "").strip().rstrip("/")
    url = f"{base}/api/public/terms/acceptance-status"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SEC) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 403:
                return {
                    **portal_terms_skipped_status(),
                    "needs_acceptance": True,
                    "portal_configured": True,
                    "detail": "Deployment not registered for this license.",
                }
            if not resp.is_success:
                logger.warning("portal terms/status failed: %s", resp.status_code)
                return portal_terms_skipped_status()
            data = resp.json()
            if not isinstance(data, dict):
                return portal_terms_skipped_status()
            return {**data, "portal_configured": True}
    except Exception as exc:  # noqa: BLE001
        logger.warning("portal terms/status error: %s", exc)
        return portal_terms_skipped_status()


async def post_portal_terms_accept(*, icici_user_id: str, terms_version: int) -> dict:
    if not _portal_configured():
        return {"ok": False, "detail": "Portal not configured"}
    payload = _portal_base_payload(icici_user_id=icici_user_id)
    if not payload:
        return {"ok": False, "detail": "Portal terms unavailable"}
    payload["terms_version"] = str(terms_version)
    base = (cfg.PORTAL_API_BASE_URL or "").strip().rstrip("/")
    url = f"{base}/api/public/terms/accept"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SEC) as client:
            resp = await client.post(
                url,
                json={
                    "license_key": payload["license_key"],
                    "public_ip": payload["public_ip"],
                    "icici_user_id": payload["icici_user_id"],
                    "terms_version": terms_version,
                },
            )
            if not resp.is_success:
                detail = "Terms acceptance failed"
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
        logger.warning("portal terms/accept error: %s", exc)
        return {"ok": False, "detail": str(exc)}
