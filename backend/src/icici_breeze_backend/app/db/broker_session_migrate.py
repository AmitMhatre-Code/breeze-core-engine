"""SQLite migration for the persisted, encrypted broker session token store.

Lets background work with no HTTP request in scope (PB/SL square-off dispatch)
obtain a broker session for the rest of the trading day, not just while some
recent request's cookie populated the per-request ContextVar. See
`app/repositories/broker_session.py`.
"""
from __future__ import annotations

import sqlite3


def ensure_broker_session_table(db_path: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_broker_session (
                user_id TEXT PRIMARY KEY NOT NULL,
                encrypted_token TEXT NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                created_at TIMESTAMP DEFAULT (datetime('now', '+5 hours', '+30 minutes')),
                encrypted_full_secret TEXT
            )
            """
        )
        # Added for docs/design-decisions.md #31. Nullable with no backfill: a row written before
        # the column existed simply has no secret until that user's next login, which is exactly
        # the behaviour the deployment had before (no worse, and it expires at midnight anyway).
        cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(user_broker_session)").fetchall()}
        if "encrypted_full_secret" not in cols:
            conn.execute("ALTER TABLE user_broker_session ADD COLUMN encrypted_full_secret TEXT")
        conn.commit()
