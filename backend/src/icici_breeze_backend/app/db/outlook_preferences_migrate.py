"""SQLite migration for per-user outlook generation preferences."""
from __future__ import annotations

import sqlite3


def ensure_outlook_preferences_table(db_path: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_outlook_preferences (
                user_id TEXT PRIMARY KEY NOT NULL,
                feeds_json TEXT,
                prompt_template TEXT,
                system_prompt TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cols = [r[1] for r in conn.execute("PRAGMA table_info(user_outlook_preferences)").fetchall()]
        if "system_prompt" not in cols:
            conn.execute("ALTER TABLE user_outlook_preferences ADD COLUMN system_prompt TEXT")
        conn.commit()
