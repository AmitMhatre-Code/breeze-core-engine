"""User account helpers for registration, Google OAuth, and direct (password) auth."""
from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Optional

import bcrypt

if TYPE_CHECKING:
    from icici_breeze_backend.app.auth.credentials import CredentialManager


def hash_app_password(plain: str) -> str:
    """Hash application login password (bcrypt)."""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_app_password(plain: str, password_hash: str | None) -> bool:
    if not plain or not password_hash:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), password_hash.encode("ascii"))
    except Exception:
        return False


def ensure_user_account(conn: sqlite3.Connection, google_id: str, user_id: str, email: str) -> None:
    """Create or update Google-linked user_account. user_id is PK; google_id is unique when set."""
    cur = conn.cursor()
    cur.execute("SELECT google_id FROM user_account WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    if row is not None and row[0] != google_id:
        raise sqlite3.IntegrityError("user_id already linked to a different identity")

    cur.execute("SELECT 1 FROM user_account WHERE google_id = ?", (google_id,))
    if cur.fetchone():
        cur.execute(
            """
            UPDATE user_account
            SET user_id = ?, username = ?, email = ?, auth_provider = 'google'
            WHERE google_id = ?
            """,
            (user_id, user_id, email, google_id),
        )
    else:
        cur.execute(
            """
            INSERT INTO user_account (
                user_id, google_id, username, email, roles, auth_provider
            ) VALUES (?, ?, ?, ?, ?, 'google')
            """,
            (user_id, google_id, user_id, email, '["trader"]'),
        )
    conn.commit()


def create_direct_user_account(
    conn: sqlite3.Connection,
    user_id: str,
    email: str,
    password_plain: str,
    *,
    do_commit: bool = True,
) -> None:
    """Register a direct (non-Google) account. Raises IntegrityError if user_id exists."""
    ph = hash_app_password(password_plain)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO user_account (
            user_id, google_id, username, email, roles, auth_provider, password_hash
        ) VALUES (?, NULL, ?, ?, ?, 'direct', ?)
        """,
        (user_id, user_id, email, '["trader"]', ph),
    )
    if do_commit:
        conn.commit()


def get_user_id_by_google_id(conn: sqlite3.Connection, google_id: str) -> Optional[str]:
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM user_account WHERE google_id = ?", (google_id,))
    row = cur.fetchone()
    return row[0] if row else None


def get_google_id_by_user_id(conn: sqlite3.Connection, user_id: str) -> Optional[str]:
    cur = conn.cursor()
    cur.execute("SELECT google_id FROM user_account WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    return row[0] if row else None


def get_account_auth_row(
    conn: sqlite3.Connection, user_id: str
) -> Optional[tuple[str | None, str | None, str | None]]:
    """Returns (google_id, auth_provider, password_hash) or None."""
    cur = conn.cursor()
    cur.execute(
        "SELECT google_id, auth_provider, password_hash FROM user_account WHERE user_id = ?",
        (user_id,),
    )
    row = cur.fetchone()
    if not row:
        return None
    return (row[0], row[1], row[2])


def user_account_exists_by_user_id(conn: sqlite3.Connection, user_id: str) -> bool:
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM user_account WHERE user_id = ? AND COALESCE(is_active, 1) = 1",
        (user_id,),
    )
    return cur.fetchone() is not None


def user_account_exists_by_google_id(conn: sqlite3.Connection, google_id: str) -> bool:
    return get_user_id_by_google_id(conn, google_id) is not None


def user_has_active_broker_credentials(conn: sqlite3.Connection, user_id: str) -> bool:
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM user_credentials WHERE user_id = ? AND is_active = 1 LIMIT 1",
        (user_id,),
    )
    return cur.fetchone() is not None


def update_direct_app_password(conn: sqlite3.Connection, user_id: str, password_plain: str) -> bool:
    """Set bcrypt app password for a direct account. Returns False if not direct or missing row."""
    row = get_account_auth_row(conn, user_id)
    if not row:
        return False
    _gid, provider, _ph = row
    if (provider or "") != "direct":
        return False
    ph = hash_app_password(password_plain)
    conn.execute(
        "UPDATE user_account SET password_hash = ? WHERE user_id = ? AND auth_provider = 'direct'",
        (ph, user_id),
    )
    conn.commit()
    return True


def verify_direct_account_password(conn: sqlite3.Connection, user_id: str, password: str) -> bool:
    row = get_account_auth_row(conn, user_id)
    if not row:
        return False
    _gid, provider, ph = row
    if (provider or "") != "direct":
        return False
    return verify_app_password(password, ph)


def revoke_credentials_for_user(conn: sqlite3.Connection, user_id: str) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE user_credentials
        SET is_active = 0, rotated_at = CURRENT_TIMESTAMP
        WHERE user_id = ? AND is_active = 1
        """,
        (user_id,),
    )


def delete_user_account_by_user_id(conn: sqlite3.Connection, user_id: str) -> bool:
    """Deactivate broker creds and remove user_account row. Returns True if a row was deleted."""
    revoke_credentials_for_user(conn, user_id)
    cur = conn.cursor()
    cur.execute("DELETE FROM user_account WHERE user_id = ?", (user_id,))
    deleted = cur.rowcount > 0
    conn.commit()
    return deleted


def change_user_id(
    conn: sqlite3.Connection,
    old_user_id: str,
    new_user_id: str,
    roles: str,
    cred_manager: "CredentialManager",
    api_key: str,
    secret_fragment: str,
) -> bool:
    """
    Change ICICI user_id for account. Atomic migration keyed by old_user_id (PK).
    Returns True on success.
    """
    if new_user_id == old_user_id:
        return True
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM user_account WHERE user_id = ?", (new_user_id,))
    if cur.fetchone():
        return False

    conn.execute("PRAGMA foreign_keys = OFF")
    cur.execute(
        "UPDATE user_credentials SET is_active = 0, rotated_at = CURRENT_TIMESTAMP WHERE user_id = ? AND is_active = 1",
        (old_user_id,),
    )
    import uuid
    from base64 import urlsafe_b64encode
    import hashlib

    from cryptography.fernet import Fernet

    try:
        cipher = Fernet(
            cred_manager.encryption_key.encode()
            if isinstance(cred_manager.encryption_key, str)
            else cred_manager.encryption_key
        )
    except Exception:
        key_material = hashlib.sha256(
            cred_manager.encryption_key.encode()
            if isinstance(cred_manager.encryption_key, str)
            else cred_manager.encryption_key
        ).digest()
        cipher = Fernet(urlsafe_b64encode(key_material[:32]))
    encrypted = cipher.encrypt(secret_fragment.encode())
    credential_id = str(uuid.uuid4())
    cur.execute(
        "INSERT INTO user_credentials (credential_id, user_id, broker_api_key, secret_fragment, encryption_salt, fragment_position, created_at, is_active) VALUES (?, ?, ?, ?, ?, ?, datetime('now'), 1)",
        (credential_id, new_user_id, api_key, encrypted, b"", "first_half"),
    )
    cur.execute("UPDATE user_credentials SET user_id = ? WHERE user_id = ?", (new_user_id, old_user_id))
    for table in ("audit_log", "order_events", "user_messages", "idempotency_results"):
        try:
            cur.execute(f"UPDATE {table} SET user_id = ? WHERE user_id = ?", (new_user_id, old_user_id))
        except sqlite3.OperationalError:
            pass
    cur.execute(
        "UPDATE user_account SET user_id = ?, username = ? WHERE user_id = ?",
        (new_user_id, new_user_id, old_user_id),
    )
    conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()
    return True
