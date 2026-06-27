"""SQLite persistence for reference data load schedule and progress."""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from typing import Any

import icici_breeze_backend.app.core.config as cfg

_logger = logging.getLogger(__name__)
_MAX_HISTORY = 80


def _db_path() -> str:
    return cfg.DATA_PATH + cfg.USERS_DB


def ensure_reference_data_tables(db_path: str | None = None) -> None:
    path = db_path or _db_path()
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reference_data_schedule (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                enabled INTEGER NOT NULL DEFAULT 1,
                hour_ist INTEGER NOT NULL DEFAULT 18,
                minute_ist INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reference_data_load_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                state_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reference_data_ingest_history (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                display_name TEXT NOT NULL,
                source_file_date TEXT,
                row_count INTEGER NOT NULL DEFAULT 0,
                ingested_at TEXT NOT NULL,
                ok INTEGER NOT NULL DEFAULT 0,
                notes TEXT,
                source_url TEXT
            )
            """
        )
        row = conn.execute("SELECT 1 FROM reference_data_schedule WHERE id = 1").fetchone()
        if not row:
            conn.execute(
                "INSERT INTO reference_data_schedule (id, enabled, hour_ist, minute_ist) VALUES (1, 1, ?, ?)",
                (cfg.REFERENCE_DATA_REFRESH_HOUR_IST, cfg.REFERENCE_DATA_REFRESH_MINUTE_IST),
            )
        row = conn.execute("SELECT 1 FROM reference_data_load_state WHERE id = 1").fetchone()
        if not row:
            conn.execute(
                "INSERT INTO reference_data_load_state (id, state_json) VALUES (1, '{}')",
            )
        conn.commit()


def load_schedule() -> dict[str, Any]:
    ensure_reference_data_tables()
    with sqlite3.connect(_db_path()) as conn:
        row = conn.execute(
            "SELECT enabled, hour_ist, minute_ist FROM reference_data_schedule WHERE id = 1"
        ).fetchone()
    if not row:
        return {"enabled": True, "hour_ist": 18, "minute_ist": 0}
    return {
        "enabled": bool(row[0]),
        "hour_ist": int(row[1]),
        "minute_ist": int(row[2]),
    }


def save_schedule(enabled: bool, hour_ist: int, minute_ist: int) -> None:
    ensure_reference_data_tables()
    with sqlite3.connect(_db_path()) as conn:
        conn.execute(
            """
            INSERT INTO reference_data_schedule (id, enabled, hour_ist, minute_ist)
            VALUES (1, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              enabled = excluded.enabled,
              hour_ist = excluded.hour_ist,
              minute_ist = excluded.minute_ist
            """,
            (1 if enabled else 0, max(0, min(23, hour_ist)), max(0, min(59, minute_ist))),
        )
        conn.commit()


def load_progress_state() -> dict[str, Any]:
    ensure_reference_data_tables()
    with sqlite3.connect(_db_path()) as conn:
        row = conn.execute("SELECT state_json FROM reference_data_load_state WHERE id = 1").fetchone()
    if not row or not row[0]:
        return _default_progress_state()
    try:
        data = json.loads(row[0])
        return data if isinstance(data, dict) else _default_progress_state()
    except json.JSONDecodeError:
        return _default_progress_state()


def save_progress_state(state: dict[str, Any]) -> None:
    ensure_reference_data_tables()
    with sqlite3.connect(_db_path()) as conn:
        conn.execute(
            """
            INSERT INTO reference_data_load_state (id, state_json) VALUES (1, ?)
            ON CONFLICT(id) DO UPDATE SET state_json = excluded.state_json
            """,
            (json.dumps(state, default=str),),
        )
        conn.commit()


def _default_progress_state() -> dict[str, Any]:
    return {
        "refresh_in_progress": False,
        "last_refresh_message": None,
        "nse_fo_refresh_in_progress": False,
        "nse_fo_progress_pct": 0,
        "nse_fo_message": None,
        "bse_fo_refresh_in_progress": False,
        "bse_fo_progress_pct": 0,
        "bse_fo_message": None,
        "scrip_refresh_in_progress": False,
        "scrip_progress_pct": 0,
        "scrip_message": None,
        "span_refresh_in_progress": False,
        "span_progress_pct": 0,
        "span_message": None,
    }


def append_ingest_history(entry: dict[str, Any]) -> None:
    ensure_reference_data_tables()
    eid = str(entry.get("id") or uuid.uuid4())
    with sqlite3.connect(_db_path()) as conn:
        conn.execute(
            """
            INSERT INTO reference_data_ingest_history (
              id, kind, display_name, source_file_date, row_count, ingested_at, ok, notes, source_url
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              kind = excluded.kind,
              display_name = excluded.display_name,
              source_file_date = excluded.source_file_date,
              row_count = excluded.row_count,
              ingested_at = excluded.ingested_at,
              ok = excluded.ok,
              notes = excluded.notes,
              source_url = excluded.source_url
            """,
            (
                eid,
                str(entry.get("kind") or ""),
                str(entry.get("display_name") or ""),
                entry.get("source_file_date"),
                int(entry.get("row_count") or 0),
                str(entry.get("ingested_at") or ""),
                1 if entry.get("ok") else 0,
                entry.get("notes"),
                entry.get("source_url"),
            ),
        )
        conn.commit()


def fetch_ingest_history(limit: int = _MAX_HISTORY) -> list[dict[str, Any]]:
    ensure_reference_data_tables()
    with sqlite3.connect(_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, kind, display_name, source_file_date, row_count, ingested_at, ok, notes, source_url
            FROM reference_data_ingest_history
            ORDER BY ingested_at DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "id": r["id"],
                "kind": r["kind"],
                "display_name": r["display_name"],
                "source_file_date": r["source_file_date"],
                "row_count": r["row_count"],
                "ingested_at": r["ingested_at"],
                "ok": bool(r["ok"]),
                "notes": r["notes"],
                "source_url": r["source_url"],
                "upload_filename": None,
            }
        )
    return out
