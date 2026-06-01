"""Verify signed deployment heartbeat/login policy JWTs from breeze-saas-portal."""
from __future__ import annotations

import logging
import os
import re
from typing import Any
from urllib.parse import urlparse

import jwt

logger = logging.getLogger(__name__)

JWT_ISSUER = "breeze-portal"
JWT_AUDIENCE = "breeze-core-engine"
JWT_ALGORITHM = "ES256"

_PUBLIC_KEY_PATH = "/etc/breeze/portal_heartbeat_public.pem"
_ALLOWED_HOSTS_PATH = "/etc/breeze/portal_allowed_hosts.txt"

_POLICY_CLAIM_KEYS = (
    "status",
    "deployment_license_status",
    "deployment_license_read_only",
    "activation_rejected_reason",
    "trigger_upgrade",
    "target_tag",
    "upgrade_allowed_now",
    "heartbeat_interval_sec",
    "latest_version",
)

_TARGET_TAG_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")


def _load_public_key_pem() -> str | None:
    path = (os.environ.get("PORTAL_HEARTBEAT_JWT_PUBLIC_KEY_PATH") or _PUBLIC_KEY_PATH).strip()
    try:
        with open(path, encoding="utf-8") as fh:
            pem = fh.read().strip()
            return pem or None
    except OSError:
        return None


def _load_allowed_hosts() -> frozenset[str]:
    path = (os.environ.get("PORTAL_ALLOWED_HOSTS_PATH") or _ALLOWED_HOSTS_PATH).strip()
    hosts: set[str] = set()
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                host = line.strip().lower()
                if host and not host.startswith("#"):
                    hosts.add(host)
    except OSError:
        pass
    return frozenset(hosts)


def portal_host_allowed(portal_base_url: str) -> bool:
    """True when portal URL hostname is on the image-baked allowlist."""
    allowed = _load_allowed_hosts()
    if not allowed:
        logger.warning("portal allowed-hosts file missing or empty; rejecting portal URL")
        return False
    try:
        host = (urlparse(portal_base_url.strip()).hostname or "").lower()
    except Exception:
        return False
    if not host:
        return False
    return host in allowed


def _policy_from_claims(claims: dict[str, Any]) -> dict[str, Any]:
    policy: dict[str, Any] = {}
    for key in _POLICY_CLAIM_KEYS:
        if key in claims:
            policy[key] = claims[key]
    return policy


def _validate_target_tag(tag: Any) -> None:
    if tag is None:
        return
    text = str(tag).strip()
    if not text:
        return
    if text != "latest" and not _TARGET_TAG_RE.match(text):
        raise ValueError("invalid target_tag in policy token")


def parse_verified_portal_body(body: dict[str, Any] | None, *, public_ip: str) -> dict[str, Any] | None:
    """Verify ``policy_token`` in a portal JSON body; return trusted policy or None."""
    if not isinstance(body, dict):
        return None
    token = body.get("policy_token")
    if not isinstance(token, str) or not token.strip():
        return None
    try:
        return verify_policy_token(token.strip(), public_ip=public_ip)
    except Exception as exc:  # noqa: BLE001
        logger.warning("portal policy token verification failed: %s", exc)
        return None


def verify_policy_token(token: str, *, public_ip: str) -> dict[str, Any]:
    """
    Validate JWT signature and claims; return trusted unsigned policy dict.

    Raises jwt.PyJWTError or ValueError on failure.
    """
    pem = _load_public_key_pem()
    if not pem:
        raise ValueError("portal heartbeat public key is not configured")

    claims = jwt.decode(
        token,
        pem,
        algorithms=[JWT_ALGORITHM],
        audience=JWT_AUDIENCE,
        issuer=JWT_ISSUER,
    )
    bound_ip = str(claims.get("public_ip") or "").strip()
    if bound_ip != public_ip.strip():
        raise ValueError("policy token public_ip mismatch")

    policy = _policy_from_claims(claims)
    if policy.get("trigger_upgrade"):
        _validate_target_tag(policy.get("target_tag"))
    return policy
