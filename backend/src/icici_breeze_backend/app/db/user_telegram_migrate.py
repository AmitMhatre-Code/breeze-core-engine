"""SQLite migration for per-user Telegram alert linking."""
from __future__ import annotations

import sqlite3


def ensure_user_telegram_table(db_path: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_telegram (
                user_id TEXT PRIMARY KEY NOT NULL,
                telegram_chat_id TEXT,
                telegram_username TEXT,
                alerts_enabled INTEGER NOT NULL DEFAULT 1,
                onboarding_dismissed INTEGER NOT NULL DEFAULT 0,
                link_token TEXT,
                link_token_expires_at TIMESTAMP,
                linked_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_telegram_link_token ON user_telegram(link_token)"
        )
        conn.commit()
