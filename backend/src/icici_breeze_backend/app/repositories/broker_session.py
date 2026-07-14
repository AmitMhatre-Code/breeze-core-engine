"""Persisted, encrypted broker session token store (users.sqlite3 `user_broker_session`).

Lets background work with no HTTP request in scope -- PB/SL square-off dispatch
(`squareoff_dispatcher.py`), run off `portfolio_pnl_engine`'s poll loop -- obtain a
broker session for the rest of the trading day, not just while some recent
request's cookie happened to populate the per-request ContextVar
(`app/auth/context.py`). One row per user_id, overwritten on each login;
`expires_at` mirrors the token's own end-of-day lifetime (the broker cookie's own
max_age already assumes this). See `app/services/processor.py::_resolve_broker_token`.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

from icici_breeze_backend.app.auth.credentials import (
    decrypt_broker_session_token,
    encrypt_broker_session_token,
)


def _db_path() -> str:
    from icici_breeze_backend.core import config as cfg

    return cfg.DATA_PATH + cfg.USERS_DB


def _encryption_key() -> str:
    import icici_breeze_backend.app.core.config as app_cfg
    import icici_breeze_backend.core.config as core_cfg

    return (app_cfg.JWT_SECRET or core_cfg.JWT_SECRET or "").strip()


def next_midnight_ist_utc() -> datetime:
    """Expiry timestamp for a freshly-issued broker token, stored in UTC --
    matches the same end-of-day-IST assumption `home.py`'s cookie max_age and
    `breeze_session_cache`'s TTL already make about ICICI session lifetime."""
    from icici_breeze_backend.app.core.timezone import now_ist

    now = now_ist()
    next_midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return next_midnight.astimezone(timezone.utc)


def save_broker_session_token(user_id: str, token: str) -> None:
    if not user_id or not token:
        return
    key = _encryption_key()
    if not key:
        return
    encrypted = encrypt_broker_session_token(token, key)
    if not encrypted:
        return
    expires_at = next_midnight_ist_utc().isoformat()
    with sqlite3.connect(_db_path()) as conn:
        conn.execute(
            """
            INSERT INTO user_broker_session (user_id, encrypted_token, expires_at, created_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET
                encrypted_token = excluded.encrypted_token,
                expires_at = excluded.expires_at,
                created_at = CURRENT_TIMESTAMP
            """,
            (user_id, encrypted, expires_at),
        )
        conn.commit()


def get_broker_session_token(user_id: str) -> Optional[str]:
    """Decrypted token for user_id, or None if absent/expired/undecryptable."""
    if not user_id:
        return None
    key = _encryption_key()
    if not key:
        return None
    with sqlite3.connect(_db_path()) as conn:
        row = conn.execute(
            "SELECT encrypted_token, expires_at FROM user_broker_session WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if not row:
        return None
    encrypted, expires_at_raw = row
    try:
        expires_at = datetime.fromisoformat(str(expires_at_raw))
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None
    if datetime.now(timezone.utc) >= expires_at:
        return None
    return decrypt_broker_session_token(encrypted, key)


def get_broker_session_expiry(user_id: str) -> Optional[str]:
    """Raw ISO expires_at for user_id regardless of whether it has already
    lapsed -- used to report `broker_session_valid_until` in the portal
    heartbeat so the portal can decide freshness against its own clock."""
    if not user_id:
        return None
    with sqlite3.connect(_db_path()) as conn:
        row = conn.execute(
            "SELECT expires_at FROM user_broker_session WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    return str(row[0]) if row else None


def clear_broker_session_token(user_id: str) -> None:
    if not user_id:
        return
    with sqlite3.connect(_db_path()) as conn:
        conn.execute("DELETE FROM user_broker_session WHERE user_id = ?", (user_id,))
        conn.commit()
