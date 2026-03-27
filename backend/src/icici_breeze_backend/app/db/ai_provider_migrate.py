"""SQLite migration for per-user GenAI provider settings."""
from __future__ import annotations

import sqlite3


def ensure_ai_provider_table(db_path: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_ai_provider (
                user_id TEXT PRIMARY KEY NOT NULL,
                provider TEXT NOT NULL,
                api_key_encrypted BLOB NOT NULL,
                model TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_user_ai_provider_enabled
            ON user_ai_provider(enabled)
            """
        )
        conn.commit()
