"""Persisted state for the PB/SL protection-suspension reminder.

Suspension means: a user holds live SGs, but the P&L engine's position registry could not
be warmed (no usable broker session), so those rules are armed on paper and evaluating
nothing. See `services/squareoff_protection_guard`.
"""
from __future__ import annotations

import sqlite3
from typing import Any

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.timezone import ist_timestamp


def _db_path() -> str:
    return cfg.DATA_PATH + cfg.USERS_DB


def get_state(user_id: str) -> dict[str, Any] | None:
    """Suspension row for a user, or None if they are not currently suspended."""
    try:
        with sqlite3.connect(_db_path()) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT user_id, suspended_since, last_reminder_at, reminders_sent "
                "FROM squareoff_protection_reminders WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            return dict(row) if row else None
    except sqlite3.OperationalError:
        # Table not yet migrated (e.g. a partially-initialised test DB). Absent state is
        # indistinguishable from "not suspended" and both are safe to read as None.
        return None


def mark_suspended(user_id: str) -> None:
    """Record the start of a suspension. Idempotent: re-marking an already-suspended user
    must not move `suspended_since`, or the "suspended for N minutes" framing resets on
    every tick and the user can never tell how long they have been unprotected."""
    now = ist_timestamp()
    with sqlite3.connect(_db_path()) as conn:
        conn.execute(
            "INSERT INTO squareoff_protection_reminders "
            "(user_id, suspended_since, last_reminder_at, reminders_sent, updated_at) "
            "VALUES (?, ?, NULL, 0, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET updated_at = excluded.updated_at",
            (user_id, now, now),
        )
        conn.commit()


def mark_reminder_sent(user_id: str) -> None:
    now = ist_timestamp()
    with sqlite3.connect(_db_path()) as conn:
        conn.execute(
            "UPDATE squareoff_protection_reminders "
            "SET last_reminder_at = ?, reminders_sent = reminders_sent + 1, updated_at = ? "
            "WHERE user_id = ?",
            (now, now, user_id),
        )
        conn.commit()


def clear_suspension(user_id: str) -> bool:
    """Drop the suspension row. Returns True if a row was actually removed — the caller
    uses that to decide whether a "monitoring resumed" alert is warranted, so that a user
    who was never suspended does not get told their protection came back."""
    with sqlite3.connect(_db_path()) as conn:
        cur = conn.execute(
            "DELETE FROM squareoff_protection_reminders WHERE user_id = ?", (user_id,)
        )
        conn.commit()
        return cur.rowcount > 0
