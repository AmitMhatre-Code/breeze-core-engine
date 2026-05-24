"""In-memory deployment license status from portal heartbeat / deployment-login."""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Any, Literal

import icici_breeze_backend.app.core.config as cfg

LicenseStatus = Literal["active", "expired", "revoked"]
LicenseSource = Literal["heartbeat", "deployment-login"]

REVOKED_TRADING_MESSAGE = (
    "Read-only mode — you cannot define strategies or execute trades. "
    "Sign in at breeze-ui.com and follow the instructions for your license "
    "to reactivate this application."
)

_lock = threading.Lock()
_status: LicenseStatus | None = None
_updated_at: datetime | None = None
_source: LicenseSource | None = None


def _license_env_configured() -> bool:
    return bool((cfg.DEPLOYMENT_LICENSE_KEY or "").strip()) and bool(
        (cfg.PORTAL_API_BASE_URL or "").strip()
    )


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


def update_from_portal_response(
    status_code: int,
    body: str | dict | None = None,
    *,
    source: LicenseSource,
) -> None:
    """Update cached license status from a portal HTTP response."""
    if not _license_env_configured():
        return

    new_status: LicenseStatus | None = None
    if 200 <= status_code < 300:
        new_status = "active"
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
    if not _license_env_configured():
        return None
    with _lock:
        return _status


def trading_mutations_allowed() -> bool:
    """True when license is not revoked (expired still allows trading)."""
    if not _license_env_configured():
        return True
    with _lock:
        return _status != "revoked"


def get_license_status_for_api() -> dict[str, Any] | None:
    """Payload fields for HomeDataResponse; None when license env is not configured."""
    if not _license_env_configured():
        return None
    with _lock:
        if _status is None:
            return None
        return {
            "deployment_license_status": _status,
            "deployment_license_read_only": _status == "revoked",
        }


def reset_for_tests() -> None:
    """Clear cached state (tests only)."""
    global _status, _updated_at, _source
    with _lock:
        _status = None
        _updated_at = None
        _source = None
