"""SQLite migration for PB/SL protection-suspension reminder state.

One row per user who has (or had) live SGs while the position registry could not be
warmed. Exists purely so the recurring "log back in" reminder survives a restart.

That persistence is the whole point, not incidental: the defect this table supports is
*restarts leaving armed SGs inert*, so a crash-loop is exactly the condition under which
reminders fire. Holding `last_reminder_at` only in memory would make every boot look like
"never reminded" and turn a restart loop into a Telegram flood — the failure mode would
be caused by the thing meant to report it.
"""
from __future__ import annotations

import sqlite3


def ensure_squareoff_protection_table(db_path: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS squareoff_protection_reminders (
                user_id TEXT PRIMARY KEY NOT NULL,
                suspended_since TIMESTAMP,
                last_reminder_at TIMESTAMP,
                reminders_sent INTEGER NOT NULL DEFAULT 0,
                updated_at TIMESTAMP DEFAULT (datetime('now', '+5 hours', '+30 minutes'))
            )
            """
        )
        conn.commit()
