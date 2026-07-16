"""Tests for the one-time rebuild that corrects the stale DEFAULT 5 baked into
user_account.icici_rate_limit_pause_seconds by an old ALTER TABLE ADD COLUMN."""
from __future__ import annotations

import sqlite3

from icici_breeze_backend.app.services.user_rate_limit_prefs import (
    rebuild_rate_limit_pause_column_default,
)


def _create_legacy_user_account(db_path: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE "user_account" (
                user_id TEXT PRIMARY KEY NOT NULL,
                google_id TEXT UNIQUE,
                username TEXT NOT NULL,
                email TEXT NOT NULL,
                is_admin INTEGER DEFAULT 0,
                icici_rate_limit_pause_seconds INTEGER NOT NULL DEFAULT 5
            )
            """
        )
        conn.execute(
            "CREATE INDEX idx_user_account_username ON user_account(username)"
        )
        conn.commit()


def _insert_user(db_path: str, user_id: str, pause_seconds: float | None = None) -> None:
    with sqlite3.connect(db_path) as conn:
        if pause_seconds is None:
            conn.execute(
                "INSERT INTO user_account (user_id, username, email) VALUES (?, ?, ?)",
                (user_id, user_id, f"{user_id}@example.com"),
            )
        else:
            conn.execute(
                "INSERT INTO user_account (user_id, username, email, "
                "icici_rate_limit_pause_seconds) VALUES (?, ?, ?, ?)",
                (user_id, user_id, f"{user_id}@example.com", pause_seconds),
            )
        conn.commit()


def _column_default(db_path: str) -> str | None:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='user_account'"
        ).fetchone()
    return row[0] if row else None


def _pause_values(db_path: str) -> dict[str, float]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT user_id, icici_rate_limit_pause_seconds FROM user_account"
        ).fetchall()
    return {r[0]: r[1] for r in rows}


def test_rebuilds_stale_default_and_resets_all_rows(tmp_path):
    db_path = str(tmp_path / "users.sqlite3")
    _create_legacy_user_account(db_path)
    _insert_user(db_path, "untouched-new-row")  # inherits stale DEFAULT 5
    _insert_user(db_path, "custom-value-user", pause_seconds=2.0)  # user-picked value

    changed = rebuild_rate_limit_pause_column_default(db_path)

    assert changed is True
    assert "DEFAULT 0.5" in _column_default(db_path)
    values = _pause_values(db_path)
    assert values == {"untouched-new-row": 0.5, "custom-value-user": 0.5}


def test_idempotent_second_call_is_noop(tmp_path):
    db_path = str(tmp_path / "users.sqlite3")
    _create_legacy_user_account(db_path)
    _insert_user(db_path, "u1")

    assert rebuild_rate_limit_pause_column_default(db_path) is True
    assert rebuild_rate_limit_pause_column_default(db_path) is False

    # A value explicitly saved after the rebuild must survive a repeat call.
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE user_account SET icici_rate_limit_pause_seconds = 1.5 WHERE user_id = 'u1'"
        )
        conn.commit()
    assert rebuild_rate_limit_pause_column_default(db_path) is False
    assert _pause_values(db_path)["u1"] == 1.5


def test_indexes_preserved_after_rebuild(tmp_path):
    db_path = str(tmp_path / "users.sqlite3")
    _create_legacy_user_account(db_path)
    _insert_user(db_path, "u1")

    rebuild_rate_limit_pause_column_default(db_path)

    with sqlite3.connect(db_path) as conn:
        names = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='user_account'"
            ).fetchall()
        }
    assert "idx_user_account_username" in names


def test_missing_table_returns_false(tmp_path):
    db_path = str(tmp_path / "users.sqlite3")
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE placeholder (id INTEGER)")
        conn.commit()

    assert rebuild_rate_limit_pause_column_default(db_path) is False
