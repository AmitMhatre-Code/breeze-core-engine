"""Per-user Telegram alert linking: single-use deep-link tokens and chat_id storage."""
from __future__ import annotations

import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import icici_breeze_backend.app.core.config as cfg

_LINK_TOKEN_TTL_MINUTES = 10


def _db_path() -> str:
    return cfg.DATA_PATH + cfg.USERS_DB


def _connect() -> sqlite3.Connection:
    return sqlite3.connect(_db_path())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_status(row: sqlite3.Row | tuple | None) -> dict[str, Any]:
    if not row:
        return {
            "connected": False,
            "telegram_chat_id": None,
            "telegram_username": None,
            "alerts_enabled": True,
            "onboarding_dismissed": False,
            "link_token": None,
            "link_token_expires_at": None,
        }
    (
        chat_id,
        username,
        alerts_enabled,
        onboarding_dismissed,
        link_token,
        link_token_expires_at,
    ) = row
    token_valid = bool(link_token) and _token_not_expired(link_token_expires_at)
    return {
        "connected": bool(chat_id),
        "telegram_chat_id": chat_id,
        "telegram_username": username,
        "alerts_enabled": bool(alerts_enabled),
        "onboarding_dismissed": bool(onboarding_dismissed),
        "link_token": link_token if token_valid else None,
        "link_token_expires_at": link_token_expires_at if token_valid else None,
    }


def _token_not_expired(expires_at: str | None) -> bool:
    if not expires_at:
        return False
    try:
        exp = datetime.fromisoformat(expires_at)
    except ValueError:
        return False
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    return exp > datetime.now(timezone.utc)


def _ensure_row(conn: sqlite3.Connection, user_id: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO user_telegram (user_id) VALUES (?)",
        (user_id,),
    )


def get_status(user_id: str) -> dict[str, Any]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT telegram_chat_id, telegram_username, alerts_enabled, "
            "onboarding_dismissed, link_token, link_token_expires_at "
            "FROM user_telegram WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    return _row_to_status(row)


def generate_link_token(user_id: str) -> tuple[str, str]:
    """Issue a fresh single-use token, overwriting any prior one (also used to
    re-link a different Telegram account: caller unlinks first, then regenerates)."""
    token = secrets.token_urlsafe(24)
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=_LINK_TOKEN_TTL_MINUTES)).isoformat()
    with _connect() as conn:
        _ensure_row(conn, user_id)
        conn.execute(
            "UPDATE user_telegram SET link_token = ?, link_token_expires_at = ?, "
            "updated_at = ? WHERE user_id = ?",
            (token, expires_at, _now_iso(), user_id),
        )
        conn.commit()
    return token, expires_at


def has_outstanding_link_token() -> bool:
    """True while any user has an unexpired link token awaiting a handshake.

    Drives the portal claim loop's duty cycle: with no token outstanding there
    is nothing a claim could ever return, so the loop idles instead of polling.
    """
    with _connect() as conn:
        rows = conn.execute(
            "SELECT link_token_expires_at FROM user_telegram WHERE link_token IS NOT NULL"
        ).fetchall()
    return any(_token_not_expired(row[0]) for row in rows)


def consume_link_token(token: str) -> Optional[str]:
    """Look up the user owning an unexpired token and clear it (single-use)."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT user_id, link_token_expires_at FROM user_telegram WHERE link_token = ?",
            (token,),
        ).fetchone()
        if not row:
            return None
        user_id, expires_at = row
        if not _token_not_expired(expires_at):
            return None
        conn.execute(
            "UPDATE user_telegram SET link_token = NULL, link_token_expires_at = NULL, "
            "updated_at = ? WHERE user_id = ?",
            (_now_iso(), user_id),
        )
        conn.commit()
    return str(user_id)


def link_chat(user_id: str, chat_id: str, username: str | None) -> None:
    with _connect() as conn:
        _ensure_row(conn, user_id)
        conn.execute(
            "UPDATE user_telegram SET telegram_chat_id = ?, telegram_username = ?, "
            "linked_at = ?, updated_at = ? WHERE user_id = ?",
            (str(chat_id), username, _now_iso(), _now_iso(), user_id),
        )
        conn.commit()


def unlink(user_id: str) -> None:
    with _connect() as conn:
        _ensure_row(conn, user_id)
        conn.execute(
            "UPDATE user_telegram SET telegram_chat_id = NULL, telegram_username = NULL, "
            "linked_at = NULL, updated_at = ? WHERE user_id = ?",
            (_now_iso(), user_id),
        )
        conn.commit()


def set_onboarding_dismissed(user_id: str, dismissed: bool) -> None:
    with _connect() as conn:
        _ensure_row(conn, user_id)
        conn.execute(
            "UPDATE user_telegram SET onboarding_dismissed = ?, updated_at = ? WHERE user_id = ?",
            (1 if dismissed else 0, _now_iso(), user_id),
        )
        conn.commit()


def set_alerts_enabled(user_id: str, enabled: bool) -> None:
    with _connect() as conn:
        _ensure_row(conn, user_id)
        conn.execute(
            "UPDATE user_telegram SET alerts_enabled = ?, updated_at = ? WHERE user_id = ?",
            (1 if enabled else 0, _now_iso(), user_id),
        )
        conn.commit()
