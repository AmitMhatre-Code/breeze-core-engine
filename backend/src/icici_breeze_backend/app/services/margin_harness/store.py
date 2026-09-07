"""Persistence for harness runs.

Runs are kept rather than streamed straight to a download because the question the harness
answers is a comparison *between* runs -- an expiry-day run against an ordinary one, a run
before a SPAN revision against one after. A run you cannot go back to is a run you cannot
compare.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

import icici_breeze_backend.app.core.config as cfg

_logger = logging.getLogger(__name__)

# One run's payload is roughly 40-60 cases with their raw broker responses; a few hundred KB.
# Twenty of them is a comfortable ceiling for a file that also holds the user's account data.
_MAX_RUNS_KEPT = 20


def _db_path() -> str:
    return cfg.DATA_PATH + cfg.USERS_DB


def ensure_margin_harness_tables(db_path: str | None = None) -> None:
    with sqlite3.connect(db_path or _db_path()) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS margin_harness_runs (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                case_count INTEGER NOT NULL DEFAULT 0,
                priced_count INTEGER NOT NULL DEFAULT 0,
                failed_count INTEGER NOT NULL DEFAULT 0,
                broker_calls INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                summary_json TEXT NOT NULL DEFAULT '{}',
                payload_json TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_margin_harness_runs_started ON margin_harness_runs(started_at DESC)"
        )
        conn.commit()


def create_run(run_id: str, user_id: str, started_at: str, case_count: int) -> None:
    ensure_margin_harness_tables()
    with sqlite3.connect(_db_path()) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO margin_harness_runs
                (id, user_id, started_at, status, case_count)
            VALUES (?, ?, ?, 'running', ?)
            """,
            (run_id, user_id, started_at, int(case_count)),
        )
        conn.commit()


def finish_run(
    run_id: str,
    *,
    status: str,
    finished_at: str,
    priced_count: int,
    failed_count: int,
    broker_calls: int,
    summary: dict[str, Any],
    payload: dict[str, Any] | None,
    error: str | None = None,
) -> None:
    ensure_margin_harness_tables()
    with sqlite3.connect(_db_path()) as conn:
        conn.execute(
            """
            UPDATE margin_harness_runs
            SET status = ?, finished_at = ?, priced_count = ?, failed_count = ?,
                broker_calls = ?, summary_json = ?, payload_json = ?, error = ?
            WHERE id = ?
            """,
            (
                status,
                finished_at,
                int(priced_count),
                int(failed_count),
                int(broker_calls),
                json.dumps(summary, default=str),
                json.dumps(payload, default=str) if payload is not None else None,
                error,
                run_id,
            ),
        )
        conn.commit()
    _prune_old_runs()


def update_progress(run_id: str, *, priced_count: int, failed_count: int, broker_calls: int) -> None:
    with sqlite3.connect(_db_path()) as conn:
        conn.execute(
            """
            UPDATE margin_harness_runs
            SET priced_count = ?, failed_count = ?, broker_calls = ?
            WHERE id = ?
            """,
            (int(priced_count), int(failed_count), int(broker_calls), run_id),
        )
        conn.commit()


def _prune_old_runs() -> None:
    try:
        with sqlite3.connect(_db_path()) as conn:
            conn.execute(
                """
                DELETE FROM margin_harness_runs
                WHERE id NOT IN (
                    SELECT id FROM margin_harness_runs ORDER BY started_at DESC LIMIT ?
                )
                """,
                (_MAX_RUNS_KEPT,),
            )
            conn.commit()
    except sqlite3.Error as exc:
        _logger.warning("Pruning margin harness runs failed: %s", exc)


def list_runs(limit: int = _MAX_RUNS_KEPT) -> list[dict[str, Any]]:
    ensure_margin_harness_tables()
    with sqlite3.connect(_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, started_at, finished_at, status, case_count, priced_count,
                   failed_count, broker_calls, error, summary_json
            FROM margin_harness_runs
            ORDER BY started_at DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    out = []
    for r in rows:
        try:
            summary = json.loads(r["summary_json"] or "{}")
        except json.JSONDecodeError:
            summary = {}
        out.append(
            {
                "id": r["id"],
                "started_at": r["started_at"],
                "finished_at": r["finished_at"],
                "status": r["status"],
                "case_count": r["case_count"],
                "priced_count": r["priced_count"],
                "failed_count": r["failed_count"],
                "broker_calls": r["broker_calls"],
                "error": r["error"],
                "summary": summary,
            }
        )
    return out


def get_run_payload(run_id: str) -> dict[str, Any] | None:
    ensure_margin_harness_tables()
    with sqlite3.connect(_db_path()) as conn:
        row = conn.execute(
            "SELECT payload_json FROM margin_harness_runs WHERE id = ?", (run_id,)
        ).fetchone()
    if not row or not row[0]:
        return None
    try:
        return json.loads(row[0])
    except json.JSONDecodeError:
        return None


def active_run_id() -> str | None:
    ensure_margin_harness_tables()
    with sqlite3.connect(_db_path()) as conn:
        row = conn.execute(
            "SELECT id FROM margin_harness_runs WHERE status = 'running' ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
    return str(row[0]) if row else None
