"""How long `audit_log` keeps each kind of row, and the Storage screen's delete of the request log.

`audit_log` is nearly all of `users.sqlite3`, and nearly all of it is two kinds of row nothing in
the app reads: one `http_request` row per API call (`middleware/request_logger.py`) and one
`portfolio_view` / `order_view` row per page or chain load. They grow with how long a dashboard
stays open, not with trading -- the UI polls. What they give that the rotating log sink does not
is history past its 7-day / ~40 MB window, and a record of polling the sink deliberately drops
(`core/logging.QuietAccessPathFilter`), i.e. whether a tab was open at a given minute.

So the two kinds are kept for different lengths (docs/design-decisions.md #45):

* request and page-view rows -- `NOISE_KEEP_DAYS`, and deletable by date on the Storage screen;
* every other row (orders, square-off rules, GTT exits, bots, logins) -- `EVENT_KEEP_DAYS`, and
  never deleted from the screen, so freeing space cannot erase the trail behind an order incident.

Retention runs at most once per process per day, off the first audit write of the day, in a
daemon thread. A delete only frees pages inside the file; when a trim frees enough of them and
the volume has room, the file is compacted too.
"""
from __future__ import annotations

import datetime
import logging
import os
import sqlite3
import threading
from typing import Any, Optional

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.timezone import now_ist

_logger = logging.getLogger(__name__)

#: Rows that record a request or a page load rather than something that happened.
NOISE_OPS: tuple[str, ...] = ("http_request", "portfolio_view", "order_view")
NOISE_KEEP_DAYS = 14
EVENT_KEEP_DAYS = 90
#: Compact after the daily trim only when it is worth rewriting the file for.
AUTO_COMPACT_MIN_FREE_BYTES = 16 * 1024 * 1024

#: The indexes on `audit_log`, by the columns each copies (for the size estimate).
_INDEXES: tuple[tuple[str, ...], ...] = (("user_id", "timestamp"), ("operation_type",))
_COLUMNS: tuple[str, ...] = (
    "user_id", "operation_type", "resource_type", "resource_id", "action_status", "timestamp",
    "request_id", "ip_address", "error_details", "metadata",
)
_SAMPLE_ROWS = 500
_ROW_OVERHEAD = 12
_INDEX_OVERHEAD = 10
#: Lower than the backtest cache's 1.25: rows are only ever appended, so pages fill almost
#: completely. Checked on a real 25 MB file, where 1.25 read about 17% high.
_PAGE_FILL = 1.05

_IN_NOISE = f"operation_type IN ({', '.join('?' for _ in NOISE_OPS)})"

_last_pruned: dict[str, datetime.date] = {}
_prune_lock = threading.Lock()


def db_path() -> str:
    return cfg.DATA_PATH + cfg.USERS_DB


def _has_audit_table(conn: sqlite3.Connection) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'audit_log'"
    ).fetchone() is not None


def _cutoff(days: int, now: Optional[datetime.datetime] = None) -> str:
    """IST midnight `days` days before today: rows stamped before it are past retention."""
    today = (now or now_ist()).date()
    return f"{(today - datetime.timedelta(days=days)).isoformat()} 00:00:00"


# --------------------------------------------------------------------------------------
# Retention
# --------------------------------------------------------------------------------------


def prune(path: Optional[str] = None, now: Optional[datetime.datetime] = None) -> dict[str, int]:
    """Delete rows past retention. Returns {"noise": n, "events": n}."""
    path = path or db_path()
    if not os.path.exists(path):
        return {"noise": 0, "events": 0}
    with sqlite3.connect(path, timeout=30) as conn:
        if not _has_audit_table(conn):
            return {"noise": 0, "events": 0}
        noise = conn.execute(
            f"DELETE FROM audit_log WHERE {_IN_NOISE} AND timestamp < ?",
            (*NOISE_OPS, _cutoff(NOISE_KEEP_DAYS, now)),
        ).rowcount
        events = conn.execute(
            f"DELETE FROM audit_log WHERE NOT {_IN_NOISE} AND timestamp < ?",
            (*NOISE_OPS, _cutoff(EVENT_KEEP_DAYS, now)),
        ).rowcount
        conn.commit()
    return {"noise": noise or 0, "events": events or 0}


def _run_daily(path: str) -> None:
    try:
        removed = prune(path)
        if removed["noise"] or removed["events"]:
            _logger.info(
                "audit_log retention: removed %d request/page-view row(s) older than %d days and "
                "%d event row(s) older than %d days",
                removed["noise"], NOISE_KEEP_DAYS, removed["events"], EVENT_KEEP_DAYS,
            )
        if free_bytes(path) >= AUTO_COMPACT_MIN_FREE_BYTES:
            compacted, note = compact(path)
            if not compacted and note:
                _logger.info("audit_log retention: %s", note)
    except Exception:  # noqa: BLE001 -- housekeeping must never surface on a request
        _logger.warning("audit_log retention failed", exc_info=True)


def maybe_prune() -> None:
    """Run retention at most once per process per day, in the background.

    Never raises and never blocks: it hangs off every audit write. The day is stamped before the
    work so a trim that keeps failing is not retried on every request."""
    today = now_ist().date()
    with _prune_lock:
        if _last_pruned.get("day") == today:
            return
        _last_pruned["day"] = today
    threading.Thread(
        target=_run_daily, args=(db_path(),), name="audit-retention", daemon=True
    ).start()


def reset_for_tests() -> None:
    _last_pruned.clear()


# --------------------------------------------------------------------------------------
# Compaction
# --------------------------------------------------------------------------------------


def _pages(conn: sqlite3.Connection) -> tuple[int, int, int]:
    page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])
    page_count = int(conn.execute("PRAGMA page_count").fetchone()[0])
    free_pages = int(conn.execute("PRAGMA freelist_count").fetchone()[0])
    return page_size, page_count, free_pages


def free_bytes(path: Optional[str] = None) -> int:
    """Pages inside `users.sqlite3` that a delete freed and compaction has not handed back."""
    path = path or db_path()
    if not os.path.exists(path):
        return 0
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30) as conn:
            page_size, _count, free_pages = _pages(conn)
            return page_size * free_pages
    except sqlite3.Error:
        return 0


def compact(path: Optional[str] = None) -> tuple[bool, Optional[str]]:
    """VACUUM `users.sqlite3` if the volume has room, then shrink its WAL. (compacted, note).

    Under WAL the rewrite lands in the WAL before it is checkpointed back, so it needs about twice
    the live data free: a temporary copy plus the WAL. Only the live pages are copied -- after the
    request log is trimmed that is a few MB, so writers (timeout 5 s) wait well under a second."""
    from icici_breeze_backend.app.services.storage import usage

    path = path or db_path()
    if not os.path.exists(path):
        return False, None
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30) as conn:
            page_size, page_count, free_pages = _pages(conn)
    except sqlite3.Error as exc:
        return False, f"Deleted, but the accounts database could not be read to compact it ({exc})."
    need = 2 * page_size * max(0, page_count - free_pages)
    vol = usage.volume()
    if vol is not None and vol["free_bytes"] < need:
        return False, (
            f"Deleted, but the volume is too full to compact the accounts database now (it needs "
            f"about {_human(need)} free; {_human(vol['free_bytes'])} is available). New rows reuse the "
            "space, and the next delete that has room hands it back."
        )
    try:
        with sqlite3.connect(path, timeout=60) as conn:
            conn.execute("VACUUM")
            # Without this the WAL keeps the size the rewrite grew it to.
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.Error as exc:
        _logger.warning("storage: compacting the accounts database failed: %s", exc)
        return False, f"Deleted, but compacting the accounts database failed ({exc}). The next delete retries it."
    return True, None


def _human(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"


# --------------------------------------------------------------------------------------
# The Storage screen's element
# --------------------------------------------------------------------------------------


def noise_breakdown(path: Optional[str] = None) -> dict[str, Any]:
    """Rows, estimated bytes and date span of the request and page-view rows.

    Estimated like the backtest cache's small tables (no `dbstat` in the shipped SQLite): row
    count x sampled row width, index entries included. The span is read by rowid order -- the
    table is AUTOINCREMENT and stamped at insert -- so it stops at the first matching row rather
    than scanning a table that can hold millions."""
    empty = {"rows": 0, "bytes": 0, "from": None, "to": None}
    path = path or db_path()
    if not os.path.exists(path):
        return empty
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30) as conn:
        if not _has_audit_table(conn):
            return empty
        rows = int(conn.execute(
            f"SELECT COUNT(*) FROM audit_log WHERE {_IN_NOISE}", NOISE_OPS
        ).fetchone()[0] or 0)
        if not rows:
            return empty
        first = conn.execute(
            f"SELECT timestamp FROM audit_log WHERE {_IN_NOISE} ORDER BY id ASC LIMIT 1", NOISE_OPS
        ).fetchone()[0]
        last = conn.execute(
            f"SELECT timestamp FROM audit_log WHERE {_IN_NOISE} ORDER BY id DESC LIMIT 1", NOISE_OPS
        ).fetchone()[0]

        def width(names: tuple[str, ...]) -> float:
            expr = " + ".join(f'COALESCE(LENGTH("{c}"), 0)' for c in names)
            value = conn.execute(
                f"SELECT AVG({expr}) FROM (SELECT * FROM audit_log WHERE {_IN_NOISE} "
                f"ORDER BY id DESC LIMIT {_SAMPLE_ROWS})",
                NOISE_OPS,
            ).fetchone()[0]
            return float(value or 0.0)

        per_row = width(_COLUMNS) + _ROW_OVERHEAD
        per_row += sum(width(idx) + _INDEX_OVERHEAD for idx in _INDEXES)
    return {
        "rows": rows,
        "bytes": int(rows * per_row * _PAGE_FILL),
        "from": str(first)[:10] if first else None,
        "to": str(last)[:10] if last else None,
    }


def delete_noise(start: datetime.date, end: datetime.date, path: Optional[str] = None) -> int:
    """Delete request and page-view rows stamped on IST days `start`..`end` inclusive. Event rows
    are never touched."""
    path = path or db_path()
    if not os.path.exists(path):
        return 0
    with sqlite3.connect(path, timeout=30) as conn:
        if not _has_audit_table(conn):
            return 0
        cur = conn.execute(
            f"DELETE FROM audit_log WHERE {_IN_NOISE} AND timestamp >= ? AND timestamp < ?",
            (*NOISE_OPS, f"{start.isoformat()} 00:00:00",
             f"{(end + datetime.timedelta(days=1)).isoformat()} 00:00:00"),
        )
        conn.commit()
        return cur.rowcount or 0
