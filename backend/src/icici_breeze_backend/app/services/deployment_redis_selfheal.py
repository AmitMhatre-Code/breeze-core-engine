"""First-boot Redis provisioning for instances upgraded by a pre-Redis (2.0.x) image.

2.0.x's upgrade helper predates the Redis sidecar. When the portal approves the
2.0.x -> 2.1.x upgrade, the *old* image writes the recreate script, so the new
container comes up on the default bridge with no ``breeze-redis``, no
``breeze-core-net`` and no memory caps — and the portal cannot fill the gap either,
since its env-override allowlist is Telegram-only. Customer EC2 hosts are not
reachable, so there is no out-of-band way to fix this after the fact.

What 2.0.x *does* do is mount ``/var/run/docker.sock`` into the new container. That
is the whole opening: the first 2.1.x boot can finish its own installation by
provisioning the sidecar and then recreating itself through **this** image's helper
(:func:`schedule_recreate_via_helper`), which does attach the network and caps.

Silent degradation is the failure mode this exists to prevent. Without it the app
starts "fine" and falls back to the in-process memory store — ``REDIS_REQUIRE_CONNECTED``
defaults off, and it must stay off, because a fail-fast first boot would leave an
unreachable instance in a crash loop with no way back in.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import icici_breeze_backend.app.core.config as cfg

logger = logging.getLogger(__name__)

#: Two attempts, not one: the first can legitimately lose a race with a slow
#: sidecar start. Beyond that a retry is unlikely to be what's wrong, and an
#: uncapped loop on an unreachable host is far worse than staying degraded.
_MAX_ATTEMPTS = 2

_STATE_FILENAME = ".redis-selfheal.json"


def _self_heal_enabled() -> bool:
    """Off unless this looks like a portal-managed deployment (and not opted out)."""
    if (os.environ.get("DEPLOYMENT_REDIS_SELF_HEAL") or "").strip().lower() in ("0", "false", "no"):
        return False
    # Set by CFN user-data on real deployments; absent in dev/compose, where
    # recreating the container from under the developer would be hostile.
    return bool((getattr(cfg, "DEPLOYMENT_GHCR_IMAGE", "") or "").strip())


def _state_path() -> str:
    """Marker lives in the data volume — the only mount that survives a recreate."""
    users_db = (getattr(cfg, "USERS_DB", "") or "").strip()
    data_dir = os.path.dirname(users_db) if users_db else "/app/backend/data"
    return os.path.join(data_dir, _STATE_FILENAME)


def _read_attempts() -> int:
    try:
        with open(_state_path(), encoding="utf-8") as fh:
            return int(json.load(fh).get("attempts") or 0)
    except (OSError, ValueError, TypeError):
        return 0


def _write_attempts(attempts: int) -> None:
    try:
        with open(_state_path(), "w", encoding="utf-8") as fh:
            json.dump({"attempts": attempts}, fh)
    except OSError as exc:
        logger.warning("redis self-heal: could not persist attempt count: %s", exc)


def _clear_attempts() -> None:
    try:
        os.remove(_state_path())
    except OSError:
        pass


def _on_redis_network(container: Any) -> bool:
    from icici_breeze_backend.app.services.deployment_container_upgrade import (
        REDIS_NETWORK_NAME,
    )

    networks = ((container.attrs or {}).get("NetworkSettings") or {}).get("Networks") or {}
    return REDIS_NETWORK_NAME in networks


def _self_image_ref(container: Any, client: Any) -> str | None:
    """
    Image to recreate from — the one we are running, resolved to a digest when possible.

    Deliberately *not* ``DEPLOYMENT_GHCR_IMAGE``: on a 2.0.x host that still reads
    ``...:latest``, which is the 2.0.1 release. Recreating from it would silently
    downgrade the instance we were sent to upgrade.
    """
    config_image = ((container.attrs or {}).get("Config") or {}).get("Image") or ""
    try:
        image = client.images.get(container.attrs["Image"])
        digests = getattr(image, "attrs", {}).get("RepoDigests") or []
        repo = config_image.rsplit(":", 1)[0] if ":" in config_image else config_image
        for digest in digests:
            if repo and digest.startswith(f"{repo}@"):
                return digest
        if digests:
            return digests[0]
    except Exception as exc:  # noqa: BLE001 — any lookup failure falls back to the tag
        logger.debug("redis self-heal: could not resolve image digest: %s", exc)
    return config_image or None


def run_redis_self_heal_if_needed() -> None:
    """
    Provision the Redis sidecar (and, if needed, re-home this container onto its
    network). Blocking; call from a worker thread. Never raises — a failure here
    must not stop the app from starting in degraded mode.
    """
    if not _self_heal_enabled():
        return

    try:
        from icici_breeze_backend.app.db.redis_client import redis_available

        if redis_available():
            _clear_attempts()
            return
    except Exception as exc:  # noqa: BLE001
        logger.debug("redis self-heal: redis probe failed, continuing: %s", exc)

    try:
        import docker
        from icici_breeze_backend.app.services.deployment_container_upgrade import (
            REDIS_NETWORK_NAME,
            ensure_redis_sidecar_sdk,
            schedule_recreate_via_helper,
        )
    except ImportError as exc:
        logger.warning("redis self-heal: docker SDK unavailable: %s", exc)
        return

    container_name = (
        getattr(cfg, "DEPLOYMENT_CONTAINER_NAME", "") or "breeze-core-engine"
    ).strip() or "breeze-core-engine"

    try:
        client = docker.from_env()
        container = client.containers.get(container_name)
    except Exception as exc:  # noqa: BLE001 — no socket, or not a managed container
        logger.info(
            "redis self-heal: skipped (no Docker access to container %s: %s)",
            container_name,
            exc,
        )
        return

    logger.warning(
        "redis self-heal: Redis unreachable — provisioning %s sidecar", REDIS_NETWORK_NAME
    )
    try:
        ensure_redis_sidecar_sdk(client)
    except Exception as exc:  # noqa: BLE001
        logger.error("redis self-heal: could not provision Redis sidecar: %s", exc)
        return

    if _on_redis_network(container):
        # Already correctly homed, so the sidecar was simply down. It is back now,
        # but this process cached the in-memory fallback at init, so it stays
        # degraded until something restarts it. Not worth a self-inflicted
        # recreate: a transient Redis outage would then bounce the app.
        logger.warning(
            "redis self-heal: sidecar restored; this process keeps its in-memory "
            "fallback until the next restart"
        )
        return

    attempts = _read_attempts()
    if attempts >= _MAX_ATTEMPTS:
        logger.error(
            "redis self-heal: giving up after %d attempts — instance is running "
            "degraded (in-memory cache, no %s). Manual recreate required.",
            attempts,
            REDIS_NETWORK_NAME,
        )
        return

    image = _self_image_ref(container, client)
    if not image:
        logger.error("redis self-heal: could not determine own image; not recreating")
        return

    # Persist *before* acting: the recreate tears this process down, so an attempt
    # recorded afterwards would never be written and the cap would never bind.
    _write_attempts(attempts + 1)

    logger.warning(
        "redis self-heal: recreating %s from %s onto %s (attempt %d/%d)",
        container_name,
        image,
        REDIS_NETWORK_NAME,
        attempts + 1,
        _MAX_ATTEMPTS,
    )
    try:
        schedule_recreate_via_helper(client, image=image, container_name=container_name)
    except Exception as exc:  # noqa: BLE001
        logger.error("redis self-heal: recreate could not be scheduled: %s", exc)
