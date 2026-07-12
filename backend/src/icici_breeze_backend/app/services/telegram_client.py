"""Thin wrapper over Telegram's Bot API: long-poll getUpdates + sendMessage.

No retry/circuit-breaker machinery here (unlike `core/icici_client.py`) — a
missed poll tick or a failed send is retried naturally on the next loop
iteration / rule fire, and failures must never propagate into the callers
(the poll loop and the order-execution path).
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


async def get_updates(client: httpx.AsyncClient, offset: int | None, timeout: int) -> list[dict[str, Any]]:
    """Long-poll for new updates since `offset`. Returns [] on any failure.

    Takes a caller-owned, reused `AsyncClient` rather than opening one per call —
    this loop runs a call roughly every `timeout` seconds for the app's entire
    uptime, and a fresh TCP+TLS handshake every cycle is pure waste on a small
    instance. `timeout` is still passed per-request (see `httpx`'s per-call
    override) since it varies with the long-poll duration, not the client.
    """
    params: dict[str, Any] = {"timeout": timeout, "allowed_updates": ["message"]}
    if offset is not None:
        params["offset"] = offset
    try:
        resp = await client.get(_bot_url("getUpdates"), params=params, timeout=timeout + 10)
        resp.raise_for_status()
        body = resp.json()
    except httpx.HTTPError as exc:
        logger.warning("telegram getUpdates request failed: %s", exc)
        return []
    except Exception as exc:  # noqa: BLE001
        logger.warning("telegram getUpdates unexpected error: %s", exc)
        return []
    if not isinstance(body, dict) or not body.get("ok"):
        return []
    result = body.get("result")
    return result if isinstance(result, list) else []


def send_message_sync(chat_id: str, text: str) -> bool:
    """Synchronous send — deliberately not async, see telegram_alerts.py for why."""
    try:
        with httpx.Client(timeout=_SEND_TIMEOUT_SEC) as client:
            resp = client.post(
                _bot_url("sendMessage"),
                json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            )
            resp.raise_for_status()
            body = resp.json()
            return isinstance(body, dict) and bool(body.get("ok"))
    except httpx.HTTPError as exc:
        logger.warning("telegram sendMessage request failed: %s", exc)
        return False
    except Exception as exc:  # noqa: BLE001
        logger.warning("telegram sendMessage unexpected error: %s", exc)
        return False
