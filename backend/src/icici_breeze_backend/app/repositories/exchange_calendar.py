"""Global (single, not per-user) exchange calendar in users.sqlite3.

Market operating hours and the holiday list are a physical fact about the
exchange, not a per-user preference — this table has exactly one row
(id = 1). See docs/design-decisions.md for why this replaced a per-user
table.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from icici_breeze_backend.app.core.exchange_calendar import (
    _load_holidays,
    clear_holiday_cache,
)
from icici_breeze_backend.app.core.timezone import now_ist

_ROW_ID = 1
DEFAULT_OPEN_HOUR = 9
DEFAULT_OPEN_MINUTE = 15
DEFAULT_CLOSE_HOUR = 15
DEFAULT_CLOSE_MINUTE = 30


@dataclass(frozen=True)
class ExchangeCalendarRow:
    source: str
    open_hour: int
    open_minute: int
    close_hour: int
    close_minute: int
    holidays: dict[str, str]
    console_updated_at: str | None
    local_updated_at: str | None
    updated_at: str | None


def _db_path() -> str:
    from icici_breeze_backend.core import config as cfg

    return cfg.DATA_PATH + cfg.USERS_DB


def _default_holidays() -> dict[str, str]:
    return dict(_load_holidays())


def _row_from_sqlite(row: sqlite3.Row) -> ExchangeCalendarRow:
    raw = row["holidays_json"] or "{}"
    try:
        holidays = json.loads(raw)
        if not isinstance(holidays, dict):
            holidays = {}
    except (json.JSONDecodeError, TypeError):
        holidays = {}
    return ExchangeCalendarRow(
        source=str(row["source"] or "local"),
        open_hour=int(row["open_hour"]),
        open_minute=int(row["open_minute"]),
        close_hour=int(row["close_hour"]),
        close_minute=int(row["close_minute"]),
        holidays={str(k): str(v) for k, v in holidays.items()},
        console_updated_at=row["console_updated_at"],
        local_updated_at=row["local_updated_at"],
        updated_at=row["updated_at"],
    )


def _is_customized(
    *,
    open_hour: int,
    open_minute: int,
    close_hour: int,
    close_minute: int,
    holidays: Mapping[str, str],
) -> bool:
    """True if hours/holidays diverge from the bundled defaults.

    Shared by `has_local_edits` and the legacy per-user backfill (which
    needs the same "did this row actually change anything" predicate to
    pick a winner among old per-user rows).
    """
    if dict(holidays) != _default_holidays():
        return True
    return (
        open_hour != DEFAULT_OPEN_HOUR
        or open_minute != DEFAULT_OPEN_MINUTE
        or close_hour != DEFAULT_CLOSE_HOUR
        or close_minute != DEFAULT_CLOSE_MINUTE
    )


def _ensure_row() -> ExchangeCalendarRow:
    db_path = _db_path()
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT * FROM exchange_calendar WHERE id = ?", (_ROW_ID,))
        row = cur.fetchone()
        if row:
            return _row_from_sqlite(row)
        holidays = _default_holidays()
        now = now_ist().isoformat()
        conn.execute(
            """
            INSERT INTO exchange_calendar (
                id, source, open_hour, open_minute, close_hour, close_minute,
                holidays_json, local_updated_at, updated_at
            ) VALUES (?, 'local', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _ROW_ID,
                DEFAULT_OPEN_HOUR,
                DEFAULT_OPEN_MINUTE,
                DEFAULT_CLOSE_HOUR,
                DEFAULT_CLOSE_MINUTE,
                json.dumps(holidays),
                now,
                now,
            ),
        )
        conn.commit()
        cur = conn.execute("SELECT * FROM exchange_calendar WHERE id = ?", (_ROW_ID,))
        return _row_from_sqlite(cur.fetchone())


def get_calendar() -> ExchangeCalendarRow:
    return _ensure_row()


def save_calendar(
    *,
    open_hour: int,
    open_minute: int,
    close_hour: int,
    close_minute: int,
    holidays: Mapping[str, str],
    source: str = "local",
    console_updated_at: str | None = None,
) -> ExchangeCalendarRow:
    now = now_ist().isoformat()
    db_path = _db_path()
    _ensure_row()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE exchange_calendar
            SET source = ?, open_hour = ?, open_minute = ?, close_hour = ?, close_minute = ?,
                holidays_json = ?, console_updated_at = COALESCE(?, console_updated_at),
                local_updated_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                source,
                open_hour,
                open_minute,
                close_hour,
                close_minute,
                json.dumps(dict(holidays)),
                console_updated_at,
                now if source == "local" else None,
                now,
                _ROW_ID,
            ),
        )
        if source == "console_sync" and console_updated_at:
            conn.execute(
                "UPDATE exchange_calendar SET local_updated_at = NULL WHERE id = ?",
                (_ROW_ID,),
            )
        conn.commit()
    clear_holiday_cache()
    return get_calendar()


def apply_console_sync(
    *,
    open_hour: int,
    open_minute: int,
    close_hour: int,
    close_minute: int,
    holidays: Mapping[str, str],
    console_updated_at: str | None,
) -> ExchangeCalendarRow:
    return save_calendar(
        open_hour=open_hour,
        open_minute=open_minute,
        close_hour=close_hour,
        close_minute=close_minute,
        holidays=holidays,
        source="console_sync",
        console_updated_at=console_updated_at,
    )


def add_holiday(iso_date: str, name: str) -> ExchangeCalendarRow:
    row = _ensure_row()
    holidays = dict(row.holidays)
    holidays[iso_date] = name.strip()
    return save_calendar(
        open_hour=row.open_hour,
        open_minute=row.open_minute,
        close_hour=row.close_hour,
        close_minute=row.close_minute,
        holidays=holidays,
        source="local",
    )


def delete_holiday(iso_date: str) -> ExchangeCalendarRow | None:
    row = _ensure_row()
    holidays = dict(row.holidays)
    if iso_date not in holidays:
        return None
    del holidays[iso_date]
    return save_calendar(
        open_hour=row.open_hour,
        open_minute=row.open_minute,
        close_hour=row.close_hour,
        close_minute=row.close_minute,
        holidays=holidays,
        source="local",
    )


def has_local_edits(row: ExchangeCalendarRow) -> bool:
    if row.source == "console_sync" and not row.local_updated_at:
        return False
    if row.source == "local":
        if row.local_updated_at and row.console_updated_at:
            try:
                local_dt = datetime.fromisoformat(row.local_updated_at.replace("Z", "+00:00"))
                console_dt = datetime.fromisoformat(row.console_updated_at.replace("Z", "+00:00"))
                if local_dt > console_dt:
                    return True
            except ValueError:
                return True
        if _is_customized(
            open_hour=row.open_hour,
            open_minute=row.open_minute,
            close_hour=row.close_hour,
            close_minute=row.close_minute,
            holidays=row.holidays,
        ):
            return True
    return row.source == "local" and bool(row.local_updated_at)
