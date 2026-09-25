"""The Storage screen's delete: one job at a time, in a daemon thread, polled by the screen.

A thread rather than the request itself because compaction is slow: `VACUUM` rewrites the whole
backtest cache (up to its 2 GB cap), which on the instance's gp3 volume can outlast nginx's
60-second proxy timeout.

Compaction runs straight after every delete from the backtest cache -- a SQLite delete only marks
pages free inside the file, and the volume gets nothing back until the file is rewritten. It needs
the file to itself and roughly its own size in free space (the rollback journal holds the original
pages while the rewrite lands), so:

* a delete from the cache is refused while a backtest is running (it is writing to that file),
  and a backtest cannot start while a cleanup runs;
* when the volume is too full to compact, the rows are still deleted and the job says so. The
  freed pages are reused by the next backtest, and handed back by the next delete that has room.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import uuid
from typing import Any, Optional

from icici_breeze_backend.app.core.timezone import now_ist
from icici_breeze_backend.app.services.storage import elements, usage

_logger = logging.getLogger(__name__)

_lock = threading.Lock()
_job: Optional[dict[str, Any]] = None
_thread: Optional[threading.Thread] = None


class Busy(RuntimeError):
    pass


def is_running() -> bool:
    with _lock:
        return _thread is not None and _thread.is_alive()


def touches_cache() -> bool:
    """A cleanup is running on the backtest cache, so a backtest must not start writing to it."""
    with _lock:
        return (
            _thread is not None and _thread.is_alive()
            and _job is not None and _job.get("element") in elements.CACHE_ELEMENTS
        )


def state() -> Optional[dict[str, Any]]:
    with _lock:
        if _job is None:
            return None
        return {**_job, "running": _thread is not None and _thread.is_alive()}


def _human(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"


def compact(path: Optional[str] = None) -> tuple[bool, Optional[str]]:
    """VACUUM the backtest cache if the volume has room. (compacted, note)."""
    path = path or elements.cache_path()
    vol = usage.volume()
    need = elements._file_bytes(path)  # noqa: SLF001
    if vol is not None and vol["free_bytes"] < need:
        return False, (
            f"Deleted, but the volume is too full to compact the backtest cache now (it needs about "
            f"{_human(need)} free; {_human(vol['free_bytes'])} is available). The space is reused by "
            "the next backtest, and handed back by the next delete that has room -- delete something "
            "outside the backtest cache (logs, signal downloads) first."
        )
    try:
        with sqlite3.connect(path, timeout=60) as conn:
            conn.execute("VACUUM")
    except sqlite3.Error as exc:
        _logger.warning("storage: compacting the backtest cache failed: %s", exc)
        return False, f"Deleted, but compacting the backtest cache failed ({exc}). The next delete retries it."
    elements.invalidate_cache_memo()
    return True, None


def start(key: str, rng: elements.DateRange) -> dict[str, Any]:
    """Start one delete. Raises ValueError for a refusal the screen should show, Busy if a
    cleanup is already running."""
    global _job, _thread
    from icici_breeze_backend.app.services.bots import backtest_jobs

    if key in elements.CACHE_ELEMENTS and backtest_jobs.is_running():
        raise ValueError(
            "A backtest is running and writing to the backtest cache. Wait for it to finish, or "
            "stop it, then delete."
        )
    with _lock:
        if _thread is not None and _thread.is_alive():
            raise Busy("A storage cleanup is already running.")
        _job = {
            "id": str(uuid.uuid4()),
            "element": key,
            "from": rng.start.isoformat(),
            "to": rng.end.isoformat(),
            "status": "running",
            "started_at": now_ist().isoformat(timespec="seconds"),
            "finished_at": None,
            "stage": "deleting",
            "deleted": None,
            "unit": None,
            "freed_bytes": None,
            "compacted": None,
            "message": None,
            "error": None,
        }

        def update(**fields: Any) -> None:
            with _lock:
                if _job is not None:
                    _job.update(fields)

        def run() -> None:
            try:
                before = usage.volume()
                result = elements.delete(key, rng)
                notes = [result["note"]] if result.get("note") else []
                compacted = None
                if key in elements.CACHE_ELEMENTS and (result["deleted"] or elements.cache_free_bytes()):
                    update(stage="compacting", deleted=result["deleted"], unit=result["unit"])
                    compacted, note = compact()
                    if note:
                        notes.append(note)
                after = usage.volume()
                freed = (before["used_bytes"] - after["used_bytes"]) if before and after else None
                summary = f"Deleted {result['deleted']} {result['unit']}."
                if freed is not None and freed > 0:
                    summary += f" Freed {_human(freed)}."
                update(
                    status="completed", stage="done", deleted=result["deleted"], unit=result["unit"],
                    freed_bytes=max(0, freed) if freed is not None else None, compacted=compacted,
                    message=" ".join([summary, *notes]),
                    finished_at=now_ist().isoformat(timespec="seconds"),
                )
            except Exception as exc:  # noqa: BLE001 -- shown on the screen, and logged
                _logger.exception("storage cleanup of %s failed", key)
                update(status="failed", stage="done", error=str(exc),
                       finished_at=now_ist().isoformat(timespec="seconds"))

        _thread = threading.Thread(target=run, name="storage-cleanup", daemon=True)
        _thread.start()
    return state() or {}
