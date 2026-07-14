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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()
