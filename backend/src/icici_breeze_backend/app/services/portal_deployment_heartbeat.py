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


def apply_env_overrides(env_overrides: dict, version: str) -> None:
    """Recreate the deployment container with new env vars pushed from the portal
    Console's fleet settings (e.g. TELEGRAM_BOT_TOKEN) — same image, new env.

    Reuses the image-upgrade helper path since nothing short of a container
    recreate applies a new env var (os.environ is only read at import time;
    docker --env-file is only read at container creation, not on restart).
    """
    from icici_breeze_backend.app.services.deployment_container_upgrade import (
        schedule_recreate_via_helper,
    )

    image = _resolve_upgrade_image(None)
    if not image:
        logger.warning("portal heartbeat env-override apply skipped: DEPLOYMENT_GHCR_IMAGE not set")
        return

    container_name = (cfg.DEPLOYMENT_CONTAINER_NAME or "breeze-core-engine").strip() or "breeze-core-engine"

    try:
        import docker
        from docker.errors import APIError, DockerException
    except ImportError:
        logger.warning("portal heartbeat env-override apply skipped: docker SDK not installed")
        return

    try:
        client = docker.from_env()
    except DockerException as exc:
        logger.warning("portal heartbeat env-override apply: docker connection failed: %s", exc)
        return

    # Deliberately never log env_overrides' values — only that an update is happening.
    logger.info(
        "portal heartbeat env-override apply: scheduling detached helper recreate for %s (version=%s)",
        container_name,
        version,
    )
    overrides = dict(env_overrides)
    overrides["BREEZE_ENV_OVERRIDES_VERSION"] = version
    try:
        schedule_recreate_via_helper(
            client, image=image, container_name=container_name, env_overrides=overrides
        )
    except (APIError, DockerException) as exc:
        logger.warning("portal heartbeat env-override apply: container recreate failed: %s", exc)


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
    payload: dict[str, object] = {
        "public_ip": public_ip,
        "version": _reported_version(),
    }
    if key:
        payload["license_key"] = key
    try:
        from icici_breeze_backend.app.services.squareoff_watch import build_squareoff_watch

        payload["squareoff_watch"] = build_squareoff_watch()
    except Exception:  # noqa: BLE001 -- best-effort telemetry, never block heartbeat
        logger.debug("portal heartbeat: squareoff_watch build failed", exc_info=True)
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


async def _maybe_execute_upgrade(policy: dict) -> None:
    """Run the portal-approved upgrade if the policy calls for it right now.

    Shared by the startup heartbeat and the periodic tick so an upgrade queued
    while a deployment was powered off is acted on as soon as it checks back
    in, instead of only on the next periodic tick (which the portal's
    one-shot consume-on-delivery semantics may never re-offer).
    """
    if policy.get("status") != "OK":
        logger.warning("portal heartbeat unexpected status: %s", policy.get("status"))
        return

    if policy.get("trigger_upgrade"):
        if _upgrade_allowed(policy):
            target_tag = policy.get("target_tag")
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, execute_upgrade, target_tag)
        else:
            logger.info("portal heartbeat upgrade deferred: outside operator upgrade window")


def _local_env_overrides_version() -> str:
    return (os.environ.get("BREEZE_ENV_OVERRIDES_VERSION") or "").strip()


async def _maybe_apply_env_overrides(policy: dict) -> None:
    """Recreate with portal-pushed env vars (e.g. Telegram bot token) if the
    fleet config version has changed since we last applied one.

    Independent of `_maybe_execute_upgrade` — checked separately, not merged
    into one atomic recreate. If both happen to fire in the same tick, that's
    two sequential recreates instead of one (the second catches up on the next
    tick); simpler than coordinating the two, and the coincidence is rare.
    """
    env_overrides = policy.get("env_overrides")
    version = str(policy.get("env_overrides_version") or "").strip()
    if not env_overrides or not version:
        return
    if version == _local_env_overrides_version():
        return
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, apply_env_overrides, env_overrides, version)


async def send_startup_heartbeat() -> bool:
    """First portal check-in after DB/master init (before periodic loop)."""
    policy = await post_heartbeat()
    if policy:
        _apply_policy_from_body(policy)
        logger.info("portal startup heartbeat succeeded")
        await _maybe_execute_upgrade(policy)
        await _maybe_apply_env_overrides(policy)
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

    await _maybe_execute_upgrade(policy)
    await _maybe_apply_env_overrides(policy)

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
