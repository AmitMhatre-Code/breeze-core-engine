"""Telegram account linking via the portal, replacing per-deployment polling.

Telegram allows exactly one `getUpdates` consumer per bot token, and the token
is shared across the whole fleet, so a deployment that polls for itself either
409s forever or steals another deployment's `/start` handshake and answers it
from the wrong SQLite. The portal is the single consumer; this module registers
our link tokens with it and claims the handshakes it routes back:

  1. register  -- when a user asks for a deep link, tell the portal the token is ours
  2. claim     -- poll for handshakes the portal has routed to us

Validation stays here, not in the portal. `consume_link_token`'s single-use and
expiry semantics live in this deployment's own database, so it is the only
place that can decide whether a token is still good -- the portal only answers
"which deployment does this token belong to".

The loop idles on an `asyncio.Event` rather than polling on a timer: with no
outstanding token there is nothing a claim could return, so a deployment whose
users aren't actively linking sends no traffic at all.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import httpx

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.repositories.user_telegram import (
    consume_link_token,
    has_outstanding_link_token,
    link_chat,
)
from icici_breeze_backend.app.services.portal_deployment_login import _public_ip_from_origin
from icici_breeze_backend.app.services.portal_policy_token import portal_host_allowed
from icici_breeze_backend.app.services.telegram_client import send_message_sync

logger = logging.getLogger(__name__)

_TIMEOUT_SEC = 10.0
# Fast enough that "tap Start" -> "Connected" feels immediate, and only ever
# active inside a 10-minute link window.
_CLAIM_INTERVAL_SEC = 2.0
# A failed claim returns immediately, so retrying at loop speed would hammer the
# portal for as long as the failure lasts.
_BACKOFF_INITIAL_SEC = 5.0
_BACKOFF_MAX_SEC = 60.0

_link_pending: asyncio.Event = asyncio.Event()


class PortalLinkUnavailable(RuntimeError):
    """The portal could not register a link token, so linking cannot start."""


def portal_linking_enabled() -> bool:
    return bool((cfg.PORTAL_API_BASE_URL or "").strip() and _public_ip_from_origin())


def notify_link_pending() -> None:
    """Wake the claim loop after a token is registered."""
    _link_pending.set()


def _identity() -> dict[str, Any]:
    payload: dict[str, Any] = {"public_ip": _public_ip_from_origin()}
    key = (cfg.DEPLOYMENT_LICENSE_KEY or "").strip()
    if key:
        payload["license_key"] = key
    return payload


def _portal_url(path: str) -> str | None:
    base = (cfg.PORTAL_API_BASE_URL or "").strip().rstrip("/")
    if not base or not portal_host_allowed(base):
        return None
    return f"{base}{path}"


async def register_link_token(token: str) -> None:
    """Claim ownership of `token` with the portal before showing the deep link.

    Raises `PortalLinkUnavailable` rather than failing quietly: a QR code the
    portal can't route is a dead end, and the user should be told to retry
    instead of tapping a link that silently does nothing.
    """
    url = _portal_url("/api/public/telegram/link-register")
    if not url:
        raise PortalLinkUnavailable("portal not configured")
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SEC) as client:
            resp = await client.post(url, json={**_identity(), "token": token})
    except httpx.HTTPError as exc:
        logger.warning("telegram link register failed: %s", exc)
        raise PortalLinkUnavailable("portal unreachable") from exc
    if resp.status_code == 403:
        # The portal declines to route for revoked/trial-denied deployments.
        logger.warning("telegram link register rejected by portal")
        raise PortalLinkUnavailable("linking not available for this deployment")
    if resp.status_code >= 400:
        logger.warning("telegram link register failed: HTTP %s", resp.status_code)
        raise PortalLinkUnavailable("portal error")
    notify_link_pending()


async def claim_link_events() -> list[dict[str, Any]] | None:
    """Drain handshakes the portal has routed to us. None signals failure."""
    url = _portal_url("/api/public/telegram/link-claim")
    if not url:
        return None
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SEC) as client:
            resp = await client.post(url, json=_identity())
            resp.raise_for_status()
            body = resp.json()
    except httpx.HTTPError as exc:
        logger.warning("telegram link claim failed: %s", exc)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("telegram link claim unexpected error: %s", exc)
        return None
    if not isinstance(body, dict):
        return None
    events = body.get("events")
    return events if isinstance(events, list) else []


def handle_link_event(event: dict[str, Any]) -> None:
    """Complete one handshake. Runs off the event loop -- SQLite writes and the
    confirmation send both block."""
    token = str(event.get("token") or "")
    chat_id = str(event.get("chat_id") or "")
    if not token or not chat_id:
        return
    user_id = consume_link_token(token)
    if not user_id:
        send_message_sync(
            chat_id,
            "This link has expired or was already used. Generate a new one from "
            "Settings › Telegram Alerts and try again.",
        )
        return
    link_chat(user_id, chat_id, event.get("username"))
    send_message_sync(
        chat_id,
        "✅ *Connected!* You'll now get an alert here whenever a stop-loss "
        "or profit-booking rule fires.",
    )


async def _claim_once() -> bool:
    """Returns False if the claim itself failed, so the caller can back off."""
    events = await claim_link_events()
    if events is None:
        return False
    for event in events:
        if not isinstance(event, dict):
            continue
        try:
            await asyncio.to_thread(handle_link_event, event)
        except Exception:  # noqa: BLE001 - one bad event must not kill the loop
            logger.exception("telegram link event handling failed")
    return True


async def run_link_claim_loop() -> None:
    logger.info("telegram link claim loop started")
    backoff = 0.0
    while True:
        await _link_pending.wait()
        if not await asyncio.to_thread(has_outstanding_link_token):
            # Every outstanding token was consumed or expired; sleep until the
            # next deep link is generated instead of polling an empty queue.
            _link_pending.clear()
            continue
        if await _claim_once():
            backoff = 0.0
            await asyncio.sleep(_CLAIM_INTERVAL_SEC)
            continue
        backoff = min(max(backoff * 2, _BACKOFF_INITIAL_SEC), _BACKOFF_MAX_SEC)
        await asyncio.sleep(backoff)


__all__ = [
    "PortalLinkUnavailable",
    "claim_link_events",
    "handle_link_event",
    "notify_link_pending",
    "portal_linking_enabled",
    "register_link_token",
    "run_link_claim_loop",
]
