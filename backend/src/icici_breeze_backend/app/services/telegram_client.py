"""Thin wrapper over Telegram's Bot API: sendMessage and editMessageText.

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


def _post(method: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    """One Bot API call. The parsed body when Telegram says ok, else None. Never raises."""
    try:
        with httpx.Client(timeout=_SEND_TIMEOUT_SEC) as client:
            resp = client.post(_bot_url(method), json=payload)
            resp.raise_for_status()
            body = resp.json()
            if isinstance(body, dict) and body.get("ok"):
                return body
            return None
    except httpx.HTTPError as exc:
        logger.warning("telegram %s request failed: %s", method, exc)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("telegram %s unexpected error: %s", method, exc)
        return None


def _message_payload(
    chat_id: str, text: str, reply_markup: dict[str, Any] | None
) -> dict[str, Any]:
    payload: dict[str, Any] = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return payload


def send_message_sync(
    chat_id: str, text: str, *, reply_markup: dict[str, Any] | None = None
) -> bool:
    """Synchronous send — deliberately not async, see telegram_alerts.py for why.

    `reply_markup` carries an inline keyboard for the bot-proposal approval message. The
    buttons' `callback_data` is a single-use token minted by `repositories/bots`; nothing
    here interprets it, and nothing here can authorise a trade.
    """
    return _post("sendMessage", _message_payload(chat_id, text, reply_markup)) is not None


def send_message_get_id(
    chat_id: str, text: str, *, reply_markup: dict[str, Any] | None = None
) -> int | None:
    """`send_message_sync`, returning the sent message's id (None if it was not sent).

    The id is what lets a message be edited later -- an approval request's buttons are
    removed the moment it is answered, so a second tap has nothing to land on.
    """
    body = _post("sendMessage", _message_payload(chat_id, text, reply_markup))
    try:
        return int(((body or {}).get("result") or {}).get("message_id"))
    except (TypeError, ValueError):
        return None


def edit_message_text_sync(
    chat_id: str,
    message_id: int,
    text: str,
    *,
    reply_markup: dict[str, Any] | None = None,
) -> bool:
    """Replace a sent message's text and keyboard.

    The keyboard is always sent explicitly -- an empty one when `reply_markup` is None --
    so an edit meant to retire Approve/Reject never depends on Telegram's default for an
    omitted markup.
    """
    payload = _message_payload(chat_id, text, None)
    payload["message_id"] = int(message_id)
    payload["reply_markup"] = reply_markup or {"inline_keyboard": []}
    return _post("editMessageText", payload) is not None
