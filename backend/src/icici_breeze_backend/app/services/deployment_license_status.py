"""In-memory deployment license status from portal heartbeat / deployment-login."""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Any, Literal

import icici_breeze_backend.app.core.config as cfg

LicenseStatus = Literal["active", "expired", "revoked", "unlicensed"]
LicenseSource = Literal["heartbeat", "deployment-login"]

TRADING_READ_ONLY_MESSAGE = (
    "Read-only mode — you cannot define strategies or execute trades. "
    "Sign in at breeze-ui.com to obtain or activate a deployment license for this application."
)

# Backward-compatible alias for tests and imports.
REVOKED_TRADING_MESSAGE = TRADING_READ_ONLY_MESSAGE

_lock = threading.Lock()
_status: LicenseStatus | None = None
_updated_at: datetime | None = None
_source: LicenseSource | None = None


def _portal_configured() -> bool:
    return bool((cfg.PORTAL_API_BASE_URL or "").strip())


def _has_license_key() -> bool:
    return bool((cfg.DEPLOYMENT_LICENSE_KEY or "").strip())


def _license_env_configured() -> bool:
    """Portal URL and license key both set (full licensed deployment wiring)."""
    return _portal_configured() and _has_license_key()


def _parse_detail(detail: Any) -> LicenseStatus | None:
    text = str(detail or "").strip().lower()
    if "revoked" in text:
        return "revoked"
    if "expired" in text:
        return "expired"
    return None


def _parse_detail_from_body(body: str | dict | None) -> LicenseStatus | None:
    if body is None:
        return None
    if isinstance(body, dict):
        return _parse_detail(body.get("detail"))
    text = str(body).strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return _parse_detail(text)
    if isinstance(parsed, dict):
        return _parse_detail(parsed.get("detail"))
    return _parse_detail(text)


def _status_from_success_body(body: str | dict | None) -> LicenseStatus:
    if isinstance(body, dict):
        raw = body.get("deployment_license_status") or body.get("license_status")
        if raw in ("active", "expired", "revoked", "unlicensed"):
            return raw  # type: ignore[return-value]
    return "active"


def update_from_portal_response(
    status_code: int,
    body: str | dict | None = None,
    *,
    source: LicenseSource,
) -> None:
    """Update cached license status from a portal HTTP response."""
    if not _portal_configured():
        return

    new_status: LicenseStatus | None = None
    if 200 <= status_code < 300:
        new_status = _status_from_success_body(body)
    elif status_code == 403:
        new_status = _parse_detail_from_body(body)

    if new_status is None:
        return

    global _status, _updated_at, _source
    with _lock:
        _status = new_status
        _updated_at = datetime.now(timezone.utc)
        _source = source


def get_license_status() -> LicenseStatus | None:
    if not _portal_configured():
        return None
    if not _has_license_key():
        return "unlicensed"
    with _lock:
        return _status


def trading_mutations_allowed() -> bool:
    """True when deployment has a valid active license for trading mutations."""
    if not _portal_configured():
        return True
    if not _has_license_key():
        return False
    with _lock:
        return _status not in ("revoked", "unlicensed")


def _contact_sales_context() -> dict[str, Any]:
    from icici_breeze_backend.app.services.portal_deployment_heartbeat import _reported_version
    from icici_breeze_backend.app.services.portal_deployment_login import _public_ip_from_origin

    key = (cfg.DEPLOYMENT_LICENSE_KEY or "").strip()
    origin = (cfg.PUBLIC_FRONTEND_ORIGIN or "").strip()
    version = (_reported_version() or "").strip()
    return {
        "license_key": key or None,
        "public_ip": _public_ip_from_origin(),
        "deployment_origin": origin or None,
        "app_version": version or None,
    }


def _read_only_for_status(status: LicenseStatus) -> bool:
    return status in ("revoked", "unlicensed")


def get_license_status_for_api() -> dict[str, Any] | None:
    """Payload fields for HomeDataResponse; None when portal is not configured."""
    if not _portal_configured():
        return None
    if not _has_license_key():
        return {
            "deployment_license_status": "unlicensed",
            "deployment_license_read_only": True,
            "contact_sales": _contact_sales_context(),
        }
    with _lock:
        if _status is None:
            return None
        payload: dict[str, Any] = {
            "deployment_license_status": _status,
            "deployment_license_read_only": _read_only_for_status(_status),
        }
        if _status in ("expired", "revoked", "unlicensed"):
            payload["contact_sales"] = _contact_sales_context()
        return payload


def reset_for_tests() -> None:
    """Clear cached state (tests only)."""
    global _status, _updated_at, _source
    with _lock:
        _status = None
        _updated_at = None
        _source = None
