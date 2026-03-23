"""One-time SQLite migration: user_account PK google_id -> user_id, add direct-auth columns."""
from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger(__name__)


def _table_columns(conn: sqlite3.Connection, table: str) -> dict[str, dict]:
    cur = conn.execute(f'PRAGMA table_info("{table}")')
    out: dict[str, dict] = {}
    for cid, name, ctype, notnull, default, pk in cur.fetchall():
        out[name] = {"type": ctype, "notnull": notnull, "default": default, "pk": pk}
    return out


def _pk_column(cols: dict[str, dict]) -> str | None:
    for name, meta in cols.items():
        if meta.get("pk"):
            return name
    return None


def migrate_user_account_if_needed(db_path: str) -> bool:
    """
    If user_account still has google_id as PRIMARY KEY, rebuild table with user_id as PK.
    Adds password_hash, auth_provider when missing.
    Returns True if a structural migration ran, False if already current or no table.
    """
    try:
        with sqlite3.connect(db_path) as conn:
            cur = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='user_account'"
            )
            if not cur.fetchone():
                return False

            cols = _table_columns(conn, "user_account")
            if not cols:
                return False

            pk = _pk_column(cols)
            has_password = "password_hash" in cols
            has_provider = "auth_provider" in cols

            # Already migrated: user_id is PK
            if pk == "user_id" and has_password and has_provider:
                return False

            if pk == "google_id":
                logger.info("Migrating user_account: PK google_id -> user_id")
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    """
                    CREATE TABLE user_account_new (
                        user_id TEXT PRIMARY KEY NOT NULL,
                        google_id TEXT UNIQUE,
                        username TEXT NOT NULL,
                        email TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        is_active BOOLEAN DEFAULT 1,
                        roles TEXT DEFAULT '["trader"]',
                        is_admin INTEGER DEFAULT 0,
                        password_hash TEXT,
                        auth_provider TEXT NOT NULL DEFAULT 'google'
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO user_account_new (
                        user_id, google_id, username, email, created_at, updated_at,
                        is_active, roles, is_admin, password_hash, auth_provider
                    )
                    SELECT
                        user_id, google_id, username, email, created_at, updated_at,
                        is_active, roles, is_admin, NULL, 'google'
                    FROM user_account
                    """
                )
                conn.execute("DROP TABLE user_account")
                conn.execute("ALTER TABLE user_account_new RENAME TO user_account")
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_user_account_username ON user_account(username)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_user_account_email ON user_account(email)"
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_user_account_active_created
                    ON user_account(is_active, created_at)
                    """
                )
                conn.commit()
                logger.info("user_account PK migration complete")
                return True

            # Partial upgrade: PK already user_id but missing columns
            if pk == "user_id" and (not has_password or not has_provider):
                logger.info("Adding user_account columns password_hash / auth_provider")
                if not has_password:
                    conn.execute("ALTER TABLE user_account ADD COLUMN password_hash TEXT")
                if not has_provider:
                    conn.execute(
                        "ALTER TABLE user_account ADD COLUMN auth_provider TEXT NOT NULL DEFAULT 'google'"
                    )
                conn.commit()
                return True

    except Exception:
        logger.exception("user_account migration failed for %s", db_path)
        raise

    return False
