"""Periodic portal heartbeat and admin-approved container upgrades."""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, time
from zoneinfo import ZoneInfo

import httpx

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.services.portal_deployment_login import _public_ip_from_origin

logger = logging.getLogger(__name__)

_IST = ZoneInfo("Asia/Kolkata")
_MARKET_OPEN = time(9, 0)
_MARKET_CLOSE = time(16, 0)
_HEARTBEAT_TIMEOUT_SEC = 10.0
_WATCHTOWER_IMAGE = "containrrr/watchtower"


def is_ist_market_hours(now: datetime | None = None) -> bool:
    """True when local IST time is in [09:00, 16:00) — upgrades must not run."""
    dt = now or datetime.now(_IST)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_IST)
    else:
        dt = dt.astimezone(_IST)
    t = dt.time()
    return _MARKET_OPEN <= t < _MARKET_CLOSE


def _reported_version() -> str:
    for key in ("APP_VERSION", "IMAGE_TAG", "DEPLOYMENT_VERSION"):
        val = (os.environ.get(key) or "").strip()
        if val:
            return val[:512]
    return "unknown"


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
    """Pull target image and run Watchtower once against the deployment container."""
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

    try:
        watch = client.containers.run(
            _WATCHTOWER_IMAGE,
            command=["--run-once", container_name],
            volumes={"/var/run/docker.sock": {"bind": "/var/run/docker.sock", "mode": "rw"}},
            detach=True,
            remove=True,
        )
        logger.info(
            "portal heartbeat upgrade: started watchtower id=%s for container=%s",
            getattr(watch, "id", watch),
            container_name,
        )
    except (APIError, DockerException) as exc:
        logger.warning("portal heartbeat upgrade: watchtower failed: %s", exc)


async def post_heartbeat() -> dict | None:
    """POST heartbeat to portal; return JSON body or None on failure."""
    base = (cfg.PORTAL_API_BASE_URL or "").strip().rstrip("/")
    key = (cfg.DEPLOYMENT_LICENSE_KEY or "").strip()
    public_ip = _public_ip_from_origin()
    if not base or not key or not public_ip:
        return None

    url = f"{base}/api/public/heartbeat"
    payload = {
        "license_key": key,
        "public_ip": public_ip,
        "version": _reported_version(),
    }
    try:
        async with httpx.AsyncClient(timeout=_HEARTBEAT_TIMEOUT_SEC) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError as exc:
        logger.warning("portal heartbeat request failed: %s", exc)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("portal heartbeat unexpected error: %s", exc)
        return None


async def heartbeat_tick() -> None:
    if is_ist_market_hours():
        logger.debug("portal heartbeat skipped: IST market hours")
        return

    body = await post_heartbeat()
    if not body:
        return

    if body.get("status") != "OK":
        logger.warning("portal heartbeat unexpected status: %s", body.get("status"))
        return

    if not body.get("trigger_upgrade"):
        return

    target_tag = body.get("target_tag")
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, execute_upgrade, target_tag)


def heartbeat_loop_enabled() -> bool:
    if not (cfg.DEPLOYMENT_LICENSE_KEY or "").strip():
        return False
    if not (cfg.PORTAL_API_BASE_URL or "").strip():
        return False
    if not _public_ip_from_origin():
        return False
    return True


async def run_heartbeat_loop() -> None:
    """Sleep interval loop; exits when cancelled."""
    interval = max(60, int(cfg.PORTAL_HEARTBEAT_INTERVAL_SEC or 300))
    logger.info("portal heartbeat loop started (interval=%ss)", interval)
    while True:
        try:
            await heartbeat_tick()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("portal heartbeat tick error: %s", exc)
        await asyncio.sleep(interval)
