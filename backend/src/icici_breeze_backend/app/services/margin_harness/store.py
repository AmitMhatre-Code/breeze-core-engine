"""Persistence for harness runs.

Runs are kept rather than streamed straight to a download because the question the harness
answers is a comparison *between* runs -- an expiry-day run against an ordinary one, a run
before a SPAN revision against one after. A run you cannot go back to is a run you cannot
compare.
"""
from __future__ import annotations

import gzip
import json
import logging
import sqlite3
from typing import Any

import icici_breeze_backend.app.core.config as cfg

_logger = logging.getLogger(__name__)

# One run's payload is roughly 40-60 cases with their raw broker responses; a few hundred KB.
# Twenty of them is a comfortable ceiling for a file that also holds the user's account data.
# A calibration sweep is ~700 cases (~7 MB of JSON), so its payload is stored gzipped (~10x).
_MAX_RUNS_KEPT = 20

#: A run in one of these can be continued from its next unpriced case.
RESUMABLE_STATUSES = ("stopped", "paused", "interrupted")


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
        have = {str(r[1]) for r in conn.execute("PRAGMA table_info(margin_harness_runs)")}
        if "mode" not in have:
            conn.execute("ALTER TABLE margin_harness_runs ADD COLUMN mode TEXT NOT NULL DEFAULT 'standard'")
        if "meta_json" not in have:
            # Everything a resume needs: the case list, market context, add-on and call cap.
            conn.execute("ALTER TABLE margin_harness_runs ADD COLUMN meta_json TEXT")
        if "payload_gz" not in have:
            conn.execute("ALTER TABLE margin_harness_runs ADD COLUMN payload_gz BLOB")
        # One row per priced case, written as it is priced, so a run cut short keeps its work.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS margin_harness_results (
                run_id TEXT NOT NULL,
                idx INTEGER NOT NULL,
                result_json TEXT NOT NULL,
                PRIMARY KEY (run_id, idx)
            )
            """
        )
        conn.commit()


def create_run(
    run_id: str,
    user_id: str,
    started_at: str,
    case_count: int,
    *,
    mode: str = "standard",
    meta: dict[str, Any] | None = None,
) -> None:
    ensure_margin_harness_tables()
    with sqlite3.connect(_db_path()) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO margin_harness_runs
                (id, user_id, started_at, status, case_count, mode, meta_json)
            VALUES (?, ?, ?, 'running', ?, ?, ?)
            """,
            (
                run_id,
                user_id,
                started_at,
                int(case_count),
                mode,
                json.dumps(meta, default=str) if meta is not None else None,
            ),
        )
        conn.commit()


def get_run(run_id: str) -> dict[str, Any] | None:
    """The run row with its meta decoded (payload excluded)."""
    ensure_margin_harness_tables()
    with sqlite3.connect(_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT id, user_id, started_at, status, case_count, priced_count, failed_count,
                   broker_calls, mode, meta_json
            FROM margin_harness_runs WHERE id = ?
            """,
            (run_id,),
        ).fetchone()
    if not row:
        return None
    out = dict(row)
    try:
        out["meta"] = json.loads(out.pop("meta_json") or "null")
    except json.JSONDecodeError:
        out["meta"] = None
    return out


def set_status(run_id: str, status: str, *, error: str | None = None) -> None:
    with sqlite3.connect(_db_path()) as conn:
        conn.execute(
            "UPDATE margin_harness_runs SET status = ?, error = ? WHERE id = ?",
            (status, error, run_id),
        )
        conn.commit()


def reap_interrupted_runs() -> int:
    """Mark runs left 'running' by a previous process as 'interrupted' (resumable).

    The harness runs as a thread of the single API process, so a 'running' row with no live
    thread was cut off by a restart -- and would otherwise block every later run.
    """
    ensure_margin_harness_tables()
    with sqlite3.connect(_db_path()) as conn:
        cur = conn.execute(
            "UPDATE margin_harness_runs SET status = 'interrupted', "
            "error = 'Cut off by a restart; resume to continue.' WHERE status = 'running'"
        )
        conn.commit()
        return cur.rowcount


def append_result(run_id: str, idx: int, result: dict[str, Any]) -> None:
    with sqlite3.connect(_db_path()) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO margin_harness_results (run_id, idx, result_json) VALUES (?, ?, ?)",
            (run_id, int(idx), json.dumps(result, default=str)),
        )
        conn.commit()


def load_results(run_id: str) -> list[dict[str, Any]]:
    ensure_margin_harness_tables()
    with sqlite3.connect(_db_path()) as conn:
        rows = conn.execute(
            "SELECT result_json FROM margin_harness_results WHERE run_id = ? ORDER BY idx",
            (run_id,),
        ).fetchall()
    return [json.loads(r[0]) for r in rows]


def result_count(run_id: str) -> int:
    ensure_margin_harness_tables()
    with sqlite3.connect(_db_path()) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM margin_harness_results WHERE run_id = ?", (run_id,)
        ).fetchone()
    return int(row[0] if row else 0)


def clear_results(run_id: str) -> None:
    with sqlite3.connect(_db_path()) as conn:
        conn.execute("DELETE FROM margin_harness_results WHERE run_id = ?", (run_id,))
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
    compress: bool = False,
) -> None:
    ensure_margin_harness_tables()
    text = json.dumps(payload, default=str) if payload is not None else None
    payload_json, payload_gz = (None, gzip.compress(text.encode("utf-8"))) if (compress and text) else (text, None)
    with sqlite3.connect(_db_path()) as conn:
        conn.execute(
            """
            UPDATE margin_harness_runs
            SET status = ?, finished_at = ?, priced_count = ?, failed_count = ?,
                broker_calls = ?, summary_json = ?, payload_json = ?, payload_gz = ?, error = ?
            WHERE id = ?
            """,
            (
                status,
                finished_at,
                int(priced_count),
                int(failed_count),
                int(broker_calls),
                json.dumps(summary, default=str),
                payload_json,
                payload_gz,
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
            conn.execute(
                "DELETE FROM margin_harness_results WHERE run_id NOT IN (SELECT id FROM margin_harness_runs)"
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
                   failed_count, broker_calls, error, summary_json, mode
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
                "mode": r["mode"] or "standard",
                "resumable": r["status"] in RESUMABLE_STATUSES,
            }
        )
    return out


def get_run_payload(run_id: str) -> dict[str, Any] | None:
    ensure_margin_harness_tables()
    with sqlite3.connect(_db_path()) as conn:
        row = conn.execute(
            "SELECT payload_json, payload_gz FROM margin_harness_runs WHERE id = ?", (run_id,)
        ).fetchone()
    if not row or not (row[0] or row[1]):
        return None
    try:
        return json.loads(row[0] if row[0] else gzip.decompress(row[1]).decode("utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def active_run_id() -> str | None:
    ensure_margin_harness_tables()
    with sqlite3.connect(_db_path()) as conn:
        row = conn.execute(
            "SELECT id FROM margin_harness_runs WHERE status = 'running' ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
    return str(row[0]) if row else None
