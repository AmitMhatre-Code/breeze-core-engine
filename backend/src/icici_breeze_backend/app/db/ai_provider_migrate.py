"""SQLite migration for per-user GenAI provider settings."""
from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger(__name__)

_NEW_USER_AI_PROVIDER_DDL = """
CREATE TABLE {name} (
    user_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    api_key_encrypted BLOB NOT NULL,
    model TEXT,
    fallback_models_json TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    model_health_json TEXT,
    last_model_health_at TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, provider),
    CHECK(provider IN ('gemini', 'openai'))
)
"""


def _table_sql(conn: sqlite3.Connection, name: str) -> str | None:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return str(row[0]) if row and row[0] else None


def _is_legacy_user_ai_provider_ddl(sql: str | None) -> bool:
    if not sql:
        return False
    s = sql.lower().replace("\n", " ")
    if "user_ai_provider" not in s:
        return False
    if "(user_id, provider)" in s.replace(" ", ""):
        return False
    return "primary key" in s and "user_id" in s


def _migrate_user_ai_provider_to_dual_pk(conn: sqlite3.Connection) -> None:
    logger.info("Migrating user_ai_provider to composite PK (user_id, provider)")
    old_cols = {str(c[1]) for c in conn.execute("PRAGMA table_info(user_ai_provider)").fetchall()}
    fb_src = "COALESCE(fallback_models_json, '[]')" if "fallback_models_json" in old_cols else "'[]'"
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(_NEW_USER_AI_PROVIDER_DDL.format(name="user_ai_provider__new"))
    conn.execute(
        f"""
        INSERT INTO user_ai_provider__new (
            user_id, provider, api_key_encrypted, model, fallback_models_json,
            enabled, model_health_json, last_model_health_at, created_at, updated_at
        )
        SELECT
            user_id,
            provider,
            api_key_encrypted,
            model,
            {fb_src},
            enabled,
            NULL,
            NULL,
            created_at,
            updated_at
        FROM user_ai_provider
        """
    )
    conn.execute("DROP TABLE user_ai_provider")
    conn.execute("ALTER TABLE user_ai_provider__new RENAME TO user_ai_provider")
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_user_ai_provider_enabled
        ON user_ai_provider(user_id, enabled)
        """
    )
    conn.commit()
    logger.info("user_ai_provider dual-provider migration complete")


def _ensure_outlook_ai_provider_column(conn: sqlite3.Connection) -> None:
    cols = {str(c[1]) for c in conn.execute("PRAGMA table_info(user_account)").fetchall()}
    if "outlook_ai_provider" not in cols:
        conn.execute("ALTER TABLE user_account ADD COLUMN outlook_ai_provider TEXT")
        conn.commit()


def ensure_ai_provider_table(db_path: str) -> None:
    with sqlite3.connect(db_path) as conn:
        existing = _table_sql(conn, "user_ai_provider")
        if existing is None:
            conn.execute(_NEW_USER_AI_PROVIDER_DDL.format(name="user_ai_provider"))
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_user_ai_provider_enabled
                ON user_ai_provider(user_id, enabled)
                """
            )
        elif _is_legacy_user_ai_provider_ddl(existing):
            _migrate_user_ai_provider_to_dual_pk(conn)
        else:
            cols = {str(c[1]) for c in conn.execute("PRAGMA table_info(user_ai_provider)").fetchall()}
            if "model_health_json" not in cols:
                conn.execute("ALTER TABLE user_ai_provider ADD COLUMN model_health_json TEXT")
            if "last_model_health_at" not in cols:
                conn.execute("ALTER TABLE user_ai_provider ADD COLUMN last_model_health_at TEXT")
            conn.commit()
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_user_ai_provider_enabled
                ON user_ai_provider(user_id, enabled)
                """
            )
            conn.commit()

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS gemini_model_catalog_cache (
                provider TEXT PRIMARY KEY NOT NULL,
                models_json TEXT NOT NULL,
                fetched_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP NOT NULL,
                last_health_check_at TIMESTAMP,
                health_json TEXT
            )
            """
        )
        conn.commit()

        try:
            _ensure_outlook_ai_provider_column(conn)
        except sqlite3.OperationalError:
            logger.exception("Could not add outlook_ai_provider to user_account")
