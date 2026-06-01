"""In-memory deployment license status from portal heartbeat / deployment-login."""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Literal

import icici_breeze_backend.app.core.config as cfg

LicenseStatus = Literal[
    "active",
    "expired",
    "revoked",
    "unlicensed",
    "pending_activation",
    "trial_denied",
]
LicenseSource = Literal["heartbeat", "deployment-login"]

TRADING_READ_ONLY_MESSAGE = (
    "Read-only mode — you cannot define strategies or execute trades. "
    "Sign in at breeze-ui.com to obtain or activate a deployment license for this application."
)

# Backward-compatible alias for tests and imports.
REVOKED_TRADING_MESSAGE = TRADING_READ_ONLY_MESSAGE

_lock = threading.Lock()
_status: LicenseStatus | None = None
_verified_at: datetime | None = None
_source: LicenseSource | None = None
_heartbeat_interval_sec: int = 300
_startup_at: datetime = datetime.now(timezone.utc)


def _portal_configured() -> bool:
    return bool((cfg.PORTAL_API_BASE_URL or "").strip())


def _has_license_key() -> bool:
    return bool((cfg.DEPLOYMENT_LICENSE_KEY or "").strip())


def _effective_status_locked(now: datetime) -> LicenseStatus:
    """License status used for enforcement, accounting for staleness."""
    if _status is None:
        return "unlicensed"
    if _is_stale_locked(now):
        return "unlicensed"
    return _status


def _is_stale_locked(now: datetime) -> bool:
    if _verified_at is None:
        return True
    interval = max(300, min(3600, int(_heartbeat_interval_sec or 300)))
    age = (now - _verified_at).total_seconds()
    return age > (2 * interval)


def update_from_verified_policy(
    policy: dict[str, Any],
    *,
    source: LicenseSource,
) -> None:
    """Update cached license state from a cryptographically verified portal policy."""
    if not _portal_configured():
        return

    raw = policy.get("deployment_license_status") or policy.get("license_status")
    if raw not in (
        "active",
        "expired",
        "revoked",
        "unlicensed",
        "pending_activation",
        "trial_denied",
    ):
        return

    new_status: LicenseStatus = raw  # type: ignore[assignment]
    interval = policy.get("heartbeat_interval_sec")
    try:
        interval_sec = int(interval) if interval is not None else None
    except (TypeError, ValueError):
        interval_sec = None

    global _status, _verified_at, _source, _heartbeat_interval_sec
    with _lock:
        _status = new_status
        _verified_at = datetime.now(timezone.utc)
        _source = source
        if interval_sec is not None:
            _heartbeat_interval_sec = max(300, min(3600, interval_sec))


def record_portal_verify_failure() -> None:
    """Mark that the latest portal call did not yield a verified policy (staleness only)."""
    # Intentionally does not clear _verified_at; trading falls back via _is_stale_locked.
    pass


def get_license_status() -> LicenseStatus | None:
    if not _portal_configured():
        return None
    if not _has_license_key():
        return "unlicensed"
    with _lock:
        if _status is None:
            return None
        return _effective_status_locked(datetime.now(timezone.utc))


def trading_mutations_allowed() -> bool:
    """True when deployment has a valid active license for trading mutations."""
    if not _portal_configured():
        return True
    if not _has_license_key():
        return False
    now = datetime.now(timezone.utc)
    with _lock:
        effective = _effective_status_locked(now)
        return effective not in ("revoked", "unlicensed", "pending_activation", "trial_denied")


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
    return status in ("revoked", "unlicensed", "pending_activation", "trial_denied")


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
    now = datetime.now(timezone.utc)
    with _lock:
        if _status is None:
            return {
                "deployment_license_status": "unlicensed",
                "deployment_license_read_only": True,
                "contact_sales": _contact_sales_context(),
            }
        effective = _effective_status_locked(now)
        payload: dict[str, Any] = {
            "deployment_license_status": effective,
            "deployment_license_read_only": _read_only_for_status(effective),
        }
        if effective in ("expired", "revoked", "unlicensed", "trial_denied"):
            payload["contact_sales"] = _contact_sales_context()
        return payload


def reset_for_tests() -> None:
    """Clear cached state (tests only)."""
    global _status, _verified_at, _source, _heartbeat_interval_sec, _startup_at
    with _lock:
        _status = None
        _verified_at = None
        _source = None
        _heartbeat_interval_sec = 300
        _startup_at = datetime.now(timezone.utc)
