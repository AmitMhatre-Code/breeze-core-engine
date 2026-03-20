"""User account helpers for registration and Google OAuth."""
import sqlite3
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from icici_breeze_backend.app.auth.credentials import CredentialManager

import icici_breeze_backend.app.core.config as cfg


def ensure_user_account(conn: sqlite3.Connection, google_id: str, user_id: str, email: str) -> None:
    """Create or update user_account. google_id is PK. Used at registration and correction."""
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM user_account WHERE google_id = ?", (google_id,))
    if cur.fetchone():
        cur.execute(
            "UPDATE user_account SET user_id = ?, username = ?, email = ? WHERE google_id = ?",
            (user_id, user_id, email, google_id),
        )
    else:
        cur.execute(
            "INSERT INTO user_account (google_id, user_id, username, email, roles) VALUES (?, ?, ?, ?, ?)",
            (google_id, user_id, user_id, email, '["trader"]'),
        )
    conn.commit()


def get_user_id_by_google_id(conn: sqlite3.Connection, google_id: str) -> Optional[str]:
    """Look up user_id by google_id (PK). Returns None if not found."""
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM user_account WHERE google_id = ?", (google_id,))
    row = cur.fetchone()
    return row[0] if row else None


def get_google_id_by_user_id(conn: sqlite3.Connection, user_id: str) -> Optional[str]:
    """Look up google_id by user_id. Returns None if not found."""
    cur = conn.cursor()
    cur.execute("SELECT google_id FROM user_account WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    return row[0] if row else None


def user_account_exists_by_google_id(conn: sqlite3.Connection, google_id: str) -> bool:
    """True if user_account with this google_id exists."""
    return get_user_id_by_google_id(conn, google_id) is not None


def change_user_id(
    conn: sqlite3.Connection,
    old_user_id: str,
    new_user_id: str,
    google_id: str,
    roles: str,
    cred_manager: "CredentialManager",
    api_key: str,
    secret_fragment: str,
) -> bool:
    """
    Change ICICI user_id for account. Same google_id (ownership). Atomic migration.
    Returns True on success. Caller must rollback conn on False.
    """
    cur = conn.cursor()
    cur.execute("SELECT google_id FROM user_account WHERE user_id = ?", (new_user_id,))
    row = cur.fetchone()
    if row is not None and row[0] != google_id:
        return False  # user_id registered by different Google account
    conn.execute("PRAGMA foreign_keys = OFF")
    cur.execute(
        "UPDATE user_credentials SET is_active = 0, rotated_at = CURRENT_TIMESTAMP WHERE user_id = ? AND is_active = 1",
        (old_user_id,),
    )
    import uuid
    from cryptography.fernet import Fernet
    from base64 import urlsafe_b64encode
    import hashlib
    try:
        cipher = Fernet(cred_manager.encryption_key.encode() if isinstance(cred_manager.encryption_key, str) else cred_manager.encryption_key)
    except Exception:
        key_material = hashlib.sha256(cred_manager.encryption_key.encode() if isinstance(cred_manager.encryption_key, str) else cred_manager.encryption_key).digest()
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
        "UPDATE user_account SET user_id = ?, username = ? WHERE google_id = ?",
        (new_user_id, new_user_id, google_id),
    )
    conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()
    return True
