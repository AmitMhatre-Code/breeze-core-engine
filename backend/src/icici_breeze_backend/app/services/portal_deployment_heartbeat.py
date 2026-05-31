"""Periodic portal heartbeat and admin-approved container upgrades."""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, time
from zoneinfo import ZoneInfo

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

_IST = ZoneInfo("Asia/Kolkata")
_MARKET_OPEN = time(9, 0)
_MARKET_CLOSE = time(16, 0)
_HEARTBEAT_TIMEOUT_SEC = 10.0
_INTERVAL_MIN_SEC = 300
_INTERVAL_MAX_SEC = 3600

_last_interval_sec: int = _INTERVAL_MIN_SEC


def is_ist_market_hours(now: datetime | None = None) -> bool:
    """True when local IST time is in [09:00, 16:00) — legacy upgrade guard."""
    dt = now or datetime.now(_IST)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_IST)
    else:
        dt = dt.astimezone(_IST)
    t = dt.time()
    return _MARKET_OPEN <= t < _MARKET_CLOSE


def _clamp_interval(sec: int | float | str | None) -> int:
    try:
        n = int(sec)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        n = int(cfg.PORTAL_HEARTBEAT_INTERVAL_SEC or 300)
    return max(_INTERVAL_MIN_SEC, min(_INTERVAL_MAX_SEC, n))


def _read_baked_app_version() -> str:
    path = (os.environ.get("APP_VERSION_FILE") or "").strip() or "/etc/breeze_app_version"
    try:
        with open(path, encoding="utf-8") as fh:
            val = fh.read().strip()
            if val:
                return val[:512]
    except OSError:
        pass
    return ""


def _reported_version() -> str:
    for key in ("APP_VERSION", "IMAGE_TAG", "DEPLOYMENT_VERSION"):
        val = (os.environ.get(key) or "").strip()
        if val:
            return val[:512]
    baked = _read_baked_app_version()
    if baked:
        return baked
    return "unknown"


def _upgrade_allowed(policy: dict) -> bool:
    if "upgrade_allowed_now" in policy:
        return bool(policy.get("upgrade_allowed_now"))
    return not is_ist_market_hours()


def _apply_policy_from_body(policy: dict) -> None:
    global _last_interval_sec
    if "heartbeat_interval_sec" in policy:
        _last_interval_sec = _clamp_interval(policy.get("heartbeat_interval_sec"))


def current_heartbeat_interval_sec() -> int:
    return _last_interval_sec


def _resolve_upgrade_image(target_tag: str | None) -> str | None:
    base = (cfg.DEPLOYMENT_GHCR_IMAGE or "").strip()
    if not base:
        return None
    tag = (target_tag or "").strip()
    if not tag:
        return base
    if ":" in base:
        repo = base.rsplit(":", 1)[0]
        return f"{repo}:{tag}"
    return f"{base}:{tag}"


def execute_upgrade(target_tag: str | None) -> None:
    """Pull target image and recreate the deployment container with the host .env file."""
    from icici_breeze_backend.app.services.deployment_container_upgrade import (
        schedule_recreate_via_helper,
    )

    image = _resolve_upgrade_image(target_tag)
    if not image:
        logger.warning("portal heartbeat upgrade skipped: DEPLOYMENT_GHCR_IMAGE not set")
        return

    container_name = (cfg.DEPLOYMENT_CONTAINER_NAME or "breeze-core-engine").strip() or "breeze-core-engine"

    try:
        import docker
        from docker.errors import APIError, DockerException
    except ImportError:
        logger.warning("portal heartbeat upgrade skipped: docker SDK not installed")
        return

    try:
        client = docker.from_env()
    except DockerException as exc:
        logger.warning("portal heartbeat upgrade: docker connection failed: %s", exc)
        return

    try:
        logger.info("portal heartbeat upgrade: pulling %s", image)
        client.images.pull(image)
    except (APIError, DockerException) as exc:
        logger.warning("portal heartbeat upgrade: image pull failed: %s", exc)
        return

    logger.info(
        "portal heartbeat upgrade: scheduling detached helper recreate for %s (app container stays up until helper runs)",
        container_name,
    )
    try:
        schedule_recreate_via_helper(client, image=image, container_name=container_name)
    except (APIError, DockerException) as exc:
        logger.warning("portal heartbeat upgrade: container recreate failed: %s", exc)


async def post_heartbeat() -> dict | None:
    """POST heartbeat to portal; return verified policy dict or None on failure."""
    base = (cfg.PORTAL_API_BASE_URL or "").strip().rstrip("/")
    key = (cfg.DEPLOYMENT_LICENSE_KEY or "").strip()
    public_ip = _public_ip_from_origin()
    if not base or not public_ip:
        return None
    if not portal_host_allowed(base):
        logger.warning("portal heartbeat skipped: PORTAL_API_BASE_URL host not allowed")
        record_portal_verify_failure()
        return None

    url = f"{base}/api/public/heartbeat"
    payload: dict[str, str] = {
        "public_ip": public_ip,
        "version": _reported_version(),
    }
    if key:
        payload["license_key"] = key
    try:
        async with httpx.AsyncClient(timeout=_HEARTBEAT_TIMEOUT_SEC) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 403:
                logger.warning("portal heartbeat rejected: %s", resp.text[:500])
                record_portal_verify_failure()
                return None
            resp.raise_for_status()
            try:
                raw = resp.json()
            except Exception:  # noqa: BLE001
                raw = None
            policy = parse_verified_portal_body(raw if isinstance(raw, dict) else None, public_ip=public_ip)
            if policy is None:
                record_portal_verify_failure()
                return None
            update_from_verified_policy(policy, source="heartbeat")
            return policy
    except httpx.HTTPError as exc:
        logger.warning("portal heartbeat request failed: %s", exc)
        record_portal_verify_failure()
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("portal heartbeat unexpected error: %s", exc)
        record_portal_verify_failure()
        return None


async def send_startup_heartbeat() -> bool:
    """First portal check-in after DB/master init (before periodic loop)."""
    policy = await post_heartbeat()
    if policy:
        _apply_policy_from_body(policy)
        logger.info("portal startup heartbeat succeeded")
        return True
    logger.warning("portal startup heartbeat failed or skipped")
    return False


async def heartbeat_tick() -> int:
    """Phone home; run upgrade when portal approves. Returns next sleep interval (seconds)."""
    global _last_interval_sec

    policy = await post_heartbeat()
    if policy:
        _apply_policy_from_body(policy)

    if not policy:
        return _last_interval_sec

    if policy.get("status") != "OK":
        logger.warning("portal heartbeat unexpected status: %s", policy.get("status"))
        return _last_interval_sec

    if policy.get("trigger_upgrade"):
        if _upgrade_allowed(policy):
            target_tag = policy.get("target_tag")
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, execute_upgrade, target_tag)
        else:
            logger.info("portal heartbeat upgrade deferred: outside operator upgrade window")

    return _last_interval_sec


def heartbeat_loop_enabled() -> bool:
    if not (cfg.PORTAL_API_BASE_URL or "").strip():
        return False
    if not _public_ip_from_origin():
        return False
    return True


async def run_heartbeat_loop() -> None:
    """Periodic heartbeat loop (startup heartbeat runs separately in app lifespan)."""
    global _last_interval_sec
    _last_interval_sec = _clamp_interval(cfg.PORTAL_HEARTBEAT_INTERVAL_SEC)
    logger.info("portal heartbeat loop started (interval=%ss)", _last_interval_sec)
    while True:
        try:
            await asyncio.sleep(_last_interval_sec)
            interval = await heartbeat_tick()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("portal heartbeat tick error: %s", exc)
            interval = _last_interval_sec
        _last_interval_sec = _clamp_interval(interval)
