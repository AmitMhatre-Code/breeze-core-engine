"""Persisted, encrypted broker session store (users.sqlite3 `user_broker_session`).

Lets background work with no HTTP request in scope -- PB/SL square-off dispatch
(`squareoff_dispatcher.py`), run off `portfolio_pnl_engine`'s poll loop -- obtain a
broker session for the rest of the trading day, not just while some recent
request's cookie happened to populate the per-request ContextVar
(`app/auth/context.py`). One row per user_id, overwritten on each login;
`expires_at` mirrors the token's own end-of-day lifetime (the broker cookie's own
max_age already assumes this). See `app/services/processor.py::_resolve_broker_token`.

The row also carries the full API secret, because the token alone cannot sign a request:
every signed ICICI call is `sha256(timestamp + body + secret)`, and without request cookies
only the stored app half of the secret is available -- see docs/design-decisions.md #31.
The secret shares the token's lifetime: written at login, dropped by a login that has none,
cleared on a credential change, and **deleted** (not merely ignored) once the row expires.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

from icici_breeze_backend.app.core.timezone import ist_timestamp
from icici_breeze_backend.app.auth.credentials import (
    decrypt_broker_full_secret,
    decrypt_broker_session_token,
    encrypt_broker_full_secret,
    encrypt_broker_session_token,
)


def _db_path() -> str:
    from icici_breeze_backend.core import config as cfg

    return cfg.DATA_PATH + cfg.USERS_DB


def _encryption_key() -> str:
    import icici_breeze_backend.app.core.config as app_cfg
    import icici_breeze_backend.core.config as core_cfg

    return (app_cfg.JWT_SECRET or core_cfg.JWT_SECRET or "").strip()


def next_midnight_ist() -> datetime:
    """Expiry for a freshly-issued broker token: the coming IST midnight, offset-aware --
    matches the same end-of-day-IST assumption `home.py`'s cookie max_age and
    `breeze_session_cache`'s TTL already make about ICICI session lifetime.

    Kept offset-aware rather than flattened to an IST wall-clock string like every other
    stored timestamp, because this is an *instant* the code compares against
    (`datetime.now(timezone.utc) >= expires_at` below), not a wall clock anyone reads.
    Rendering it as `+05:30` is purely so the stored value says what it means: it used to
    be converted to UTC first and land in the database as `...T18:30:00+00:00`, which is
    the same moment but reads as half past six. Old rows still compare identically --
    `fromisoformat` honours whichever offset is written -- so there is nothing to migrate.
    """
    from icici_breeze_backend.app.core.timezone import now_ist

    now = now_ist()
    return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


def _is_expired(expires_at_raw: object) -> bool:
    """True once `expires_at` has passed. An unparseable value counts as expired: the row
    holds a secret, so doubt resolves towards deleting it."""
    try:
        expires_at = datetime.fromisoformat(str(expires_at_raw))
    except (TypeError, ValueError):
        return True
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) >= expires_at


def save_broker_session_token(user_id: str, token: str, full_secret: str | None = None) -> None:
    """Store this login's token and, when given, its full API secret.

    A login without a secret stores NULL rather than keeping the previous one, so a secret can
    never outlive the login it came from."""
    if not user_id or not token:
        return
    key = _encryption_key()
    if not key:
        return
    encrypted = encrypt_broker_session_token(token, key)
    if not encrypted:
        return
    encrypted_secret = encrypt_broker_full_secret(full_secret, key) if full_secret else ""
    expires_at = next_midnight_ist().isoformat()
    with sqlite3.connect(_db_path()) as conn:
        conn.execute(
            """
            INSERT INTO user_broker_session
                (user_id, encrypted_token, expires_at, created_at, encrypted_full_secret)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                encrypted_token = excluded.encrypted_token,
                expires_at = excluded.expires_at,
                created_at = excluded.created_at,
                encrypted_full_secret = excluded.encrypted_full_secret
            """,
            (user_id, encrypted, expires_at, ist_timestamp(), encrypted_secret or None),
        )
        conn.commit()


def _read_live(user_id: str, column: str) -> Optional[str]:
    """`column` of user_id's row while it is live. Reading an expired row deletes it."""
    with sqlite3.connect(_db_path()) as conn:
        row = conn.execute(
            f"SELECT {column}, expires_at FROM user_broker_session WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if not row:
            return None
        value, expires_at_raw = row
        if _is_expired(expires_at_raw):
            conn.execute("DELETE FROM user_broker_session WHERE user_id = ?", (user_id,))
            conn.commit()
            return None
    return str(value) if value else None


def get_broker_session_token(user_id: str) -> Optional[str]:
    """Decrypted token for user_id, or None if absent/expired/undecryptable."""
    if not user_id:
        return None
    key = _encryption_key()
    if not key:
        return None
    encrypted = _read_live(user_id, "encrypted_token")
    return decrypt_broker_session_token(encrypted, key) if encrypted else None


def get_broker_full_secret(user_id: str) -> Optional[str]:
    """Decrypted full API secret persisted with user_id's live broker session, or None.

    Only `processor._get_full_secret_for_user` should call this, and only when the request's
    own cookie secret is absent. Never log the return value."""
    if not user_id:
        return None
    key = _encryption_key()
    if not key:
        return None
    try:
        encrypted = _read_live(user_id, "encrypted_full_secret")
    except sqlite3.Error:
        return None  # column not migrated yet -- behave as "no secret stored"
    return decrypt_broker_full_secret(encrypted, key) if encrypted else None


def clear_broker_full_secret(user_id: str) -> None:
    """Drop the persisted secret but keep the token (a credential change made it stale)."""
    if not user_id:
        return
    with sqlite3.connect(_db_path()) as conn:
        conn.execute(
            "UPDATE user_broker_session SET encrypted_full_secret = NULL WHERE user_id = ?",
            (user_id,),
        )
        conn.commit()


def purge_expired_broker_sessions() -> int:
    """Delete every row past its expiry; returns how many. Called at startup and before the
    periodic user listing, so an expired secret does not wait for someone to read it."""
    try:
        with sqlite3.connect(_db_path()) as conn:
            rows = conn.execute("SELECT user_id, expires_at FROM user_broker_session").fetchall()
            expired = [(r[0],) for r in rows if _is_expired(r[1])]
            if expired:
                conn.executemany("DELETE FROM user_broker_session WHERE user_id = ?", expired)
                conn.commit()
    except sqlite3.Error:
        return 0
    return len(expired)


def list_users_with_session() -> list[str]:
    """Users holding a broker session that has not lapsed, oldest first.

    Added for the scalping candle feed, which has to subscribe from the 09:15 open whether or
    not any bot is armed (docs/bots-scalping-plan.md section 5.6) and so cannot resolve a user
    from the enabled-bot list the way the rest of the loop does.

    Expired rows are purged first, in Python, so what is left is live by construction. (The SQL
    filter alone compares ISO strings across `+05:30` and `+00:00` notations, which kept a row
    listed for five and a half hours past IST midnight.) The token itself is not decrypted
    here -- callers want to know *who* to try, and the caller that actually needs the token
    goes through `get_broker_session_token`, which is the one place that owns decryption.
    """
    purge_expired_broker_sessions()
    now = datetime.now(timezone.utc).isoformat()
    try:
        with sqlite3.connect(_db_path()) as conn:
            rows = conn.execute(
                "SELECT user_id FROM user_broker_session WHERE expires_at > ? "
                "ORDER BY created_at ASC",
                (now,),
            ).fetchall()
    except sqlite3.Error:
        return []
    return [str(r[0]) for r in rows if r and r[0]]


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
