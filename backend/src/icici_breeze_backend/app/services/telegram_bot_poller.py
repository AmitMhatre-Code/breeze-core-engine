"""Long-poll loop that captures Telegram deep-link `/start <token>` handshakes.

Mirrors `portal_deployment_heartbeat.run_heartbeat_loop`'s idiom: infinite
loop created as an `asyncio.Task` in the app lifespan, cancelled on shutdown,
all errors logged and swallowed except CancelledError. Using long-polling
(getUpdates) instead of a webhook means this works identically under
`./dev.sh` and in production — no public HTTPS endpoint required.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import httpx

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.repositories.user_telegram import consume_link_token, link_chat
from icici_breeze_backend.app.services.telegram_client import (
    get_updates,
    send_message_sync,
    telegram_bot_enabled,
)

logger = logging.getLogger(__name__)

_START_RE = re.compile(r"^/start(?:@\w+)?\s+(\S+)")

# Backoff after a failed poll. A failed `getUpdates` returns immediately rather
# than blocking for the long-poll timeout, so without this the loop hammers
# api.telegram.org at connection speed for as long as the failure lasts — and
# some failures (a 409 from a second poller sharing the bot token) never clear
# on their own. Capped well under the long-poll timeout's own cadence so a
# recovered bot is picked up promptly.
_POLL_BACKOFF_INITIAL_SEC = 5.0
_POLL_BACKOFF_MAX_SEC = 60.0

# In-memory only: re-processing an already-handled update just re-links the
# same chat (idempotent), so losing this across a restart is harmless.
_offset: int | None = None


def _handle_message(message: dict[str, Any]) -> None:
    text = (message.get("text") or "").strip()
    match = _START_RE.match(text)
    if not match:
        return
    token = match.group(1)
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None:
        return
    user_id = consume_link_token(token)
    if not user_id:
        send_message_sync(
            str(chat_id),
            "This link has expired or was already used. Generate a new one from "
            "Settings › Telegram Alerts and try again.",
        )
        return
    from_user = message.get("from") or {}
    username = from_user.get("username")
    link_chat(user_id, str(chat_id), username)
    send_message_sync(
        str(chat_id),
        "✅ *Connected!* You'll now get an alert here whenever a stop-loss "
        "or profit-booking rule fires.",
    )


async def _poll_once(client: httpx.AsyncClient) -> bool:
    """Returns False if the poll itself failed, so the caller can back off."""
    global _offset
    updates = await get_updates(client, _offset, cfg.TELEGRAM_POLL_TIMEOUT_SEC)
    if updates is None:
        return False
    for update in updates:
        _offset = int(update["update_id"]) + 1
        message = update.get("message")
        if isinstance(message, dict):
            try:
                _handle_message(message)
            except Exception:  # noqa: BLE001 - one bad update must not kill the loop
                logger.exception("telegram update handling failed for update_id=%s", update.get("update_id"))
    return True


async def run_telegram_poll_loop() -> None:
    """One `AsyncClient` lives for the whole loop so `getUpdates` reuses its
    TCP/TLS connection across cycles instead of reconnecting every ~25s."""
    logger.info("telegram bot poll loop started")
    backoff = 0.0
    async with httpx.AsyncClient() as client:
        while True:
            try:
                succeeded = await _poll_once(client)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("telegram poll tick failed")
                succeeded = False
            if succeeded:
                backoff = 0.0
                continue
            backoff = min(max(backoff * 2, _POLL_BACKOFF_INITIAL_SEC), _POLL_BACKOFF_MAX_SEC)
            await asyncio.sleep(backoff)


__all__ = ["run_telegram_poll_loop", "telegram_bot_enabled"]
