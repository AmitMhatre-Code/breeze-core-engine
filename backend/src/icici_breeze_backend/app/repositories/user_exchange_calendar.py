"""Per-user exchange calendar in users.sqlite3."""
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


@dataclass(frozen=True)
class UserExchangeCalendarRow:
    user_id: str
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


def _row_from_sqlite(row: sqlite3.Row) -> UserExchangeCalendarRow:
    raw = row["holidays_json"] or "{}"
    try:
        holidays = json.loads(raw)
        if not isinstance(holidays, dict):
            holidays = {}
    except (json.JSONDecodeError, TypeError):
        holidays = {}
    return UserExchangeCalendarRow(
        user_id=str(row["user_id"]),
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


def _ensure_row(user_id: str) -> UserExchangeCalendarRow:
    db_path = _db_path()
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT * FROM user_exchange_calendar WHERE user_id = ?",
            (user_id,),
        )
        row = cur.fetchone()
        if row:
            return _row_from_sqlite(row)
        holidays = _default_holidays()
        now = now_ist().isoformat()
        conn.execute(
            """
            INSERT INTO user_exchange_calendar (
                user_id, source, open_hour, open_minute, close_hour, close_minute,
                holidays_json, local_updated_at, updated_at
            ) VALUES (?, 'local', 9, 15, 15, 30, ?, ?, ?)
            """,
            (user_id, json.dumps(holidays), now, now),
        )
        conn.commit()
        cur = conn.execute(
            "SELECT * FROM user_exchange_calendar WHERE user_id = ?",
            (user_id,),
        )
        return _row_from_sqlite(cur.fetchone())


def get_user_calendar(user_id: str) -> UserExchangeCalendarRow:
    return _ensure_row(user_id)


def save_user_calendar(
    user_id: str,
    *,
    open_hour: int,
    open_minute: int,
    close_hour: int,
    close_minute: int,
    holidays: Mapping[str, str],
    source: str = "local",
    console_updated_at: str | None = None,
) -> UserExchangeCalendarRow:
    now = now_ist().isoformat()
    db_path = _db_path()
    _ensure_row(user_id)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE user_exchange_calendar
            SET source = ?, open_hour = ?, open_minute = ?, close_hour = ?, close_minute = ?,
                holidays_json = ?, console_updated_at = COALESCE(?, console_updated_at),
                local_updated_at = ?, updated_at = ?
            WHERE user_id = ?
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
                user_id,
            ),
        )
        if source == "console_sync" and console_updated_at:
            conn.execute(
                """
                UPDATE user_exchange_calendar
                SET local_updated_at = NULL
                WHERE user_id = ?
                """,
                (user_id,),
            )
        conn.commit()
    clear_holiday_cache()
    return get_user_calendar(user_id)


def apply_console_sync(
    user_id: str,
    *,
    open_hour: int,
    open_minute: int,
    close_hour: int,
    close_minute: int,
    holidays: Mapping[str, str],
    console_updated_at: str | None,
) -> UserExchangeCalendarRow:
    return save_user_calendar(
        user_id,
        open_hour=open_hour,
        open_minute=open_minute,
        close_hour=close_hour,
        close_minute=close_minute,
        holidays=holidays,
        source="console_sync",
        console_updated_at=console_updated_at,
    )


def add_holiday(user_id: str, iso_date: str, name: str) -> UserExchangeCalendarRow:
    row = _ensure_row(user_id)
    holidays = dict(row.holidays)
    holidays[iso_date] = name.strip()
    return save_user_calendar(
        user_id,
        open_hour=row.open_hour,
        open_minute=row.open_minute,
        close_hour=row.close_hour,
        close_minute=row.close_minute,
        holidays=holidays,
        source="local",
    )


def delete_holiday(user_id: str, iso_date: str) -> UserExchangeCalendarRow | None:
    row = _ensure_row(user_id)
    holidays = dict(row.holidays)
    if iso_date not in holidays:
        return None
    del holidays[iso_date]
    return save_user_calendar(
        user_id,
        open_hour=row.open_hour,
        open_minute=row.open_minute,
        close_hour=row.close_hour,
        close_minute=row.close_minute,
        holidays=holidays,
        source="local",
    )


def has_local_edits(row: UserExchangeCalendarRow) -> bool:
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
        defaults = _default_holidays()
        if row.holidays != defaults:
            return True
        if (
            row.open_hour != 9
            or row.open_minute != 15
            or row.close_hour != 15
            or row.close_minute != 30
        ):
            return True
    return row.source == "local" and bool(row.local_updated_at)
