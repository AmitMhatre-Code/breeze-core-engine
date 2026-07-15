"""SQLite migration for the global (singleton) exchange calendar.

Replaces the old per-user `user_exchange_calendar` table: market hours and
holidays are one shared fact about the exchange, not a per-user preference.
On first run against a database that still has the legacy per-user table,
this does a one-time backfill (see `_backfill_from_legacy_table`) and
renames the legacy table to a `_legacy_*_backup` name rather than dropping
it, so the pre-migration data stays recoverable.
"""
from __future__ import annotations

import json
import logging
import sqlite3

_logger = logging.getLogger(__name__)

_LEGACY_TABLE = "user_exchange_calendar"
_LEGACY_BACKUP_TABLE = "_legacy_user_exchange_calendar_backup"
_ROW_ID = 1


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    cur = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    )
    return cur.fetchone() is not None


def ensure_exchange_calendar_table(db_path: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS exchange_calendar (
                id INTEGER PRIMARY KEY CHECK (id = 1),
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

        already_seeded = conn.execute(
            "SELECT 1 FROM exchange_calendar WHERE id = ?", (_ROW_ID,)
        ).fetchone()
        if already_seeded:
            return

        if _table_exists(conn, _LEGACY_TABLE):
            _backfill_from_legacy_table(conn)
        else:
            _seed_defaults(conn)
        conn.commit()


def _seed_defaults(conn: sqlite3.Connection) -> None:
    from icici_breeze_backend.app.core.exchange_calendar import _load_holidays

    now_row = conn.execute("SELECT CURRENT_TIMESTAMP").fetchone()[0]
    conn.execute(
        """
        INSERT OR IGNORE INTO exchange_calendar (
            id, source, open_hour, open_minute, close_hour, close_minute,
            holidays_json, local_updated_at, updated_at
        ) VALUES (?, 'local', 9, 15, 15, 30, ?, ?, ?)
        """,
        (_ROW_ID, json.dumps(dict(_load_holidays())), now_row, now_row),
    )
    _logger.info("exchange_calendar: no legacy per-user rows found, seeded bundled defaults")


def _backfill_from_legacy_table(conn: sqlite3.Connection) -> None:
    from icici_breeze_backend.app.repositories.exchange_calendar import _is_customized

    rows = conn.execute(f"SELECT * FROM {_LEGACY_TABLE}").fetchall()
    if not rows:
        _seed_defaults(conn)
        _rename_legacy_table(conn)
        return

    parsed = []
    for row in rows:
        try:
            holidays = json.loads(row["holidays_json"] or "{}")
            if not isinstance(holidays, dict):
                holidays = {}
        except (json.JSONDecodeError, TypeError):
            holidays = {}
        parsed.append(
            {
                "user_id": row["user_id"],
                "source": row["source"] or "local",
                "open_hour": row["open_hour"],
                "open_minute": row["open_minute"],
                "close_hour": row["close_hour"],
                "close_minute": row["close_minute"],
                "holidays": {str(k): str(v) for k, v in holidays.items()},
                "console_updated_at": row["console_updated_at"],
                "local_updated_at": row["local_updated_at"],
                "updated_at": row["updated_at"] or "",
            }
        )

    customized = [
        r
        for r in parsed
        if _is_customized(
            open_hour=r["open_hour"],
            open_minute=r["open_minute"],
            close_hour=r["close_hour"],
            close_minute=r["close_minute"],
            holidays=r["holidays"],
        )
    ]
    candidates = customized or parsed
    winner = max(candidates, key=lambda r: (r["updated_at"] or "", r["user_id"]))

    conn.execute(
        """
        INSERT OR REPLACE INTO exchange_calendar (
            id, source, open_hour, open_minute, close_hour, close_minute,
            holidays_json, console_updated_at, local_updated_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _ROW_ID,
            winner["source"],
            winner["open_hour"],
            winner["open_minute"],
            winner["close_hour"],
            winner["close_minute"],
            json.dumps(winner["holidays"]),
            winner["console_updated_at"],
            winner["local_updated_at"],
            winner["updated_at"] or None,
        ),
    )
    _logger.info(
        "exchange_calendar: backfilled from legacy per-user table, "
        "%d row(s) found, chose user_id=%s (customized=%s), "
        "%d holiday(s), hours %02d:%02d-%02d:%02d",
        len(parsed),
        winner["user_id"],
        bool(customized),
        len(winner["holidays"]),
        winner["open_hour"],
        winner["open_minute"],
        winner["close_hour"],
        winner["close_minute"],
    )
    _rename_legacy_table(conn)


def _rename_legacy_table(conn: sqlite3.Connection) -> None:
    if _table_exists(conn, _LEGACY_BACKUP_TABLE):
        _logger.info(
            "exchange_calendar: %s already exists, leaving %s in place",
            _LEGACY_BACKUP_TABLE,
            _LEGACY_TABLE,
        )
        return
    conn.execute(f"ALTER TABLE {_LEGACY_TABLE} RENAME TO {_LEGACY_BACKUP_TABLE}")
