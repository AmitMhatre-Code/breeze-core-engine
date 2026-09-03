"""Thin wrapper over Telegram's Bot API: sendMessage only.

Outbound alerts go straight from each deployment to Telegram — `sendMessage`
has no single-consumer restriction, unlike `getUpdates`. Inbound linking is
routed by the portal instead (see `telegram_link_portal.py`), so nothing here
reads updates.

No retry/circuit-breaker machinery here (unlike `core/icici_client.py`) — a
failed send is retried naturally on the next rule fire, and failures must never
propagate into the order-execution path.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

import icici_breeze_backend.app.core.config as cfg

logger = logging.getLogger(__name__)

_API_BASE = "https://api.telegram.org"
_SEND_TIMEOUT_SEC = 5.0


def telegram_bot_enabled() -> bool:
    return bool((cfg.TELEGRAM_BOT_TOKEN or "").strip() and (cfg.TELEGRAM_BOT_USERNAME or "").strip())


def _bot_url(method: str) -> str:
    token = (cfg.TELEGRAM_BOT_TOKEN or "").strip()
    return f"{_API_BASE}/bot{token}/{method}"


def send_message_sync(
    chat_id: str, text: str, *, reply_markup: dict[str, Any] | None = None
) -> bool:
    """Synchronous send — deliberately not async, see telegram_alerts.py for why.

    `reply_markup` carries an inline keyboard for the bot-proposal approval message. The
    buttons' `callback_data` is a single-use token minted by `repositories/bots`; nothing
    here interprets it, and nothing here can authorise a trade.
    """
    payload: dict[str, Any] = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        with httpx.Client(timeout=_SEND_TIMEOUT_SEC) as client:
            resp = client.post(_bot_url("sendMessage"), json=payload)
            resp.raise_for_status()
            body = resp.json()
            return isinstance(body, dict) and bool(body.get("ok"))
    except httpx.HTTPError as exc:
        logger.warning("telegram sendMessage request failed: %s", exc)
        return False
    except Exception as exc:  # noqa: BLE001
        logger.warning("telegram sendMessage unexpected error: %s", exc)
        return False
