"""Tests for the one-time backfill from the legacy per-user calendar table
into the new global `exchange_calendar` singleton table."""
from __future__ import annotations

import json
import sqlite3

import pytest

from icici_breeze_backend.app.db.exchange_calendar_migrate import (
    _LEGACY_BACKUP_TABLE,
    _LEGACY_TABLE,
    ensure_exchange_calendar_table,
)
from icici_breeze_backend.app.repositories import exchange_calendar as ec_repo


def _create_legacy_table(db_path: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE user_exchange_calendar (
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


def _insert_legacy_row(
    db_path: str,
    *,
    user_id: str,
    open_hour: int = 9,
    open_minute: int = 15,
    close_hour: int = 15,
    close_minute: int = 30,
    holidays: dict[str, str] | None = None,
    updated_at: str = "2026-01-01T00:00:00+05:30",
) -> None:
    from icici_breeze_backend.app.core.exchange_calendar import _load_holidays

    # A "default" row (holidays=None) must carry the real bundled holiday
    # set, not {} — otherwise it looks customized too (diverges from
    # bundled defaults) and the "prefer customized" heuristic can't
    # distinguish it from a genuinely hand-edited row.
    effective_holidays = holidays if holidays is not None else dict(_load_holidays())
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO user_exchange_calendar (
                user_id, source, open_hour, open_minute, close_hour, close_minute,
                holidays_json, local_updated_at, updated_at
            ) VALUES (?, 'local', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                open_hour,
                open_minute,
                close_hour,
                close_minute,
                json.dumps(effective_holidays),
                updated_at,
                updated_at,
            ),
        )
        conn.commit()


def _table_names(db_path: str) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    return {r[0] for r in rows}


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = str(tmp_path / "users.sqlite3")
    monkeypatch.setattr(ec_repo, "_db_path", lambda: path)
    return path


def test_no_legacy_table_seeds_defaults(db_path):
    ensure_exchange_calendar_table(db_path)
    row = ec_repo.get_calendar()
    assert row.open_hour == 9
    assert row.close_hour == 15
    assert len(row.holidays) > 0
    assert _LEGACY_BACKUP_TABLE not in _table_names(db_path)


def test_legacy_customized_row_wins_over_default(db_path):
    _create_legacy_table(db_path)
    _insert_legacy_row(db_path, user_id="user-default", updated_at="2026-06-01T00:00:00+05:30")
    _insert_legacy_row(
        db_path,
        user_id="user-custom",
        open_hour=10,
        open_minute=0,
        close_hour=16,
        close_minute=0,
        holidays={"2026-11-10": "Muhurat trading eve"},
        updated_at="2026-01-01T00:00:00+05:30",  # older, but customized wins over default
    )

    ensure_exchange_calendar_table(db_path)

    row = ec_repo.get_calendar()
    assert row.open_hour == 10
    assert row.close_hour == 16
    assert row.holidays == {"2026-11-10": "Muhurat trading eve"}

    tables = _table_names(db_path)
    assert _LEGACY_TABLE not in tables
    assert _LEGACY_BACKUP_TABLE in tables
    with sqlite3.connect(db_path) as conn:
        backed_up = conn.execute(f"SELECT user_id FROM {_LEGACY_BACKUP_TABLE}").fetchall()
    assert {r[0] for r in backed_up} == {"user-default", "user-custom"}


def test_legacy_all_default_rows_falls_back_to_most_recent(db_path):
    _create_legacy_table(db_path)
    _insert_legacy_row(db_path, user_id="user-a", updated_at="2026-01-01T00:00:00+05:30")
    _insert_legacy_row(db_path, user_id="user-b", updated_at="2026-06-01T00:00:00+05:30")

    ensure_exchange_calendar_table(db_path)

    row = ec_repo.get_calendar()
    assert row.open_hour == 9
    assert row.close_hour == 15
    assert _LEGACY_BACKUP_TABLE in _table_names(db_path)


def test_legacy_table_with_no_rows_seeds_defaults(db_path):
    _create_legacy_table(db_path)
    ensure_exchange_calendar_table(db_path)
    row = ec_repo.get_calendar()
    assert row.open_hour == 9
    assert len(row.holidays) > 0
    assert _LEGACY_BACKUP_TABLE in _table_names(db_path)


def test_idempotent_second_call_is_noop(db_path):
    _create_legacy_table(db_path)
    _insert_legacy_row(
        db_path,
        user_id="user-custom",
        open_hour=10,
        holidays={"2026-11-10": "Muhurat trading eve"},
    )
    ensure_exchange_calendar_table(db_path)
    ec_repo.save_calendar(
        open_hour=11,
        open_minute=0,
        close_hour=17,
        close_minute=0,
        holidays={},
        source="local",
    )

    ensure_exchange_calendar_table(db_path)  # second call must not re-run backfill

    row = ec_repo.get_calendar()
    assert row.open_hour == 11
    assert row.close_hour == 17
