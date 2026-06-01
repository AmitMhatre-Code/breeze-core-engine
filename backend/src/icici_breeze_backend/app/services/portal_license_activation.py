"""Portal license activation after successful ICICI login (trial ledger)."""
from __future__ import annotations

import logging

import httpx

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.services.deployment_license_status import (
    record_portal_verify_failure,
    update_from_verified_policy,
)
from icici_breeze_backend.app.services.portal_deployment_login import _public_ip_from_origin
from icici_breeze_backend.app.services.portal_policy_token import (
    parse_verified_portal_body,
    portal_host_allowed,
)

logger = logging.getLogger(__name__)

_ACTIVATION_TIMEOUT_SEC = 10.0
_TRIAL_DENIED_STATUSES = frozenset({"trial_denied"})


def _portal_activation_configured() -> bool:
    return bool((cfg.PORTAL_API_BASE_URL or "").strip()) and bool(
        (cfg.DEPLOYMENT_LICENSE_KEY or "").strip()
    )


async def request_portal_license_activation(
    customer_check: dict,
    *,
    fallback_user_id: str,
) -> tuple[bool, str | None]:
    """
    Phone home to start or confirm trial for this ICICI user ID.

    Returns (allowed, error_message). When allowed is False, login must not proceed.
  """
    from icici_breeze_backend.app.services.icici_customer_identity import (
        parse_customer_details_identity,
    )

    icici_user_id, idirect_user_name = parse_customer_details_identity(
        customer_check, fallback_user_id=fallback_user_id
    )

    if not _portal_activation_configured():
        return True, None

    public_ip = _public_ip_from_origin()
    if not public_ip:
        logger.warning("portal activate-license skipped: no IPv4 PUBLIC_FRONTEND_ORIGIN")
        return True, None

    base = (cfg.PORTAL_API_BASE_URL or "").strip().rstrip("/")
    if not portal_host_allowed(base):
        logger.warning("portal activate-license skipped: PORTAL_API_BASE_URL host not allowed")
        record_portal_verify_failure()
        return True, None

    url = f"{base}/api/public/activate-license"
    payload: dict[str, str] = {
        "license_key": (cfg.DEPLOYMENT_LICENSE_KEY or "").strip(),
        "public_ip": public_ip,
        "icici_user_id": icici_user_id,
    }
    if not payload["icici_user_id"]:
        return False, "ICICI User ID is required."
    if idirect_user_name:
        payload["idirect_user_name"] = idirect_user_name

    try:
        async with httpx.AsyncClient(timeout=_ACTIVATION_TIMEOUT_SEC) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 403:
                detail = "Deployment not registered for this license."
                try:
                    body = resp.json()
                    if isinstance(body, dict) and body.get("detail"):
                        detail = str(body["detail"])
                except Exception:  # noqa: BLE001
                    pass
                return False, detail
            if not resp.is_success:
                logger.warning(
                    "portal activate-license HTTP %s: %s",
                    resp.status_code,
                    resp.text[:500],
                )
                record_portal_verify_failure()
                return True, None
            try:
                raw = resp.json()
            except Exception:  # noqa: BLE001
                raw = None
            policy = parse_verified_portal_body(
                raw if isinstance(raw, dict) else None, public_ip=public_ip
            )
            if policy is None:
                record_portal_verify_failure()
                return True, None
            update_from_verified_policy(policy, source="deployment-login")
            status = policy.get("deployment_license_status")
            if status in _TRIAL_DENIED_STATUSES:
                reason = policy.get("activation_rejected_reason")
                if reason == "icici_trial_consumed":
                    return (
                        False,
                        "Your 14-day trial has already been used for this ICICI Direct User ID. "
                        "Contact sales@breeze-ui.com to obtain a paid license.",
                    )
                return (
                    False,
                    "This ICICI Direct User ID cannot start a trial on this deployment. "
                    "Contact sales@breeze-ui.com for assistance.",
                )
            return True, None
    except Exception as exc:  # noqa: BLE001
        logger.warning("portal activate-license failed: %s", exc)
        record_portal_verify_failure()
        return True, None
