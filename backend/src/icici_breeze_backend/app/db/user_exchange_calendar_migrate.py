"""SQLite migration for per-user exchange calendar settings."""
from __future__ import annotations

import sqlite3


def ensure_user_exchange_calendar_table(db_path: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_exchange_calendar (
                user_id TEXT PRIMARY KEY NOT NULL,
                source TEXT NOT NULL DEFAULT 'local',
                open_hour INTEGER NOT NULL DEFAULT 9,
                open_minute INTEGER NOT NULL DEFAULT 15,
                close_hour INTEGER NOT NULL DEFAULT 15,
                close_minute INTEGER NOT NULL DEFAULT 30,
                holidays_json TEXT NOT NULL DEFAULT '{}',
                console_updated_at TEXT,
                local_updated_at TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()
