"""In-memory job store for async Strategy Builder propose-trades runs.

Single-process only; multi-worker deployments would need a shared store (e.g. Redis).
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

JobStatus = Literal["queued", "running", "done", "error"]

_JOB_TTL_SECONDS = 15 * 60

_lock = threading.Lock()
_jobs: dict[str, ProposeTradesJob] = {}


@dataclass
class ProposeTradesJob:
    job_id: str
    user_id: str
    status: JobStatus = "queued"
    phase: str = "setup"
    message: str = "Starting…"
    progress_current: int = 0
    progress_total: int = 1
    progress_pct: int = 0
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None


def _purge_expired_locked(now: float | None = None) -> None:
    ts = now if now is not None else time.time()
    expired = [jid for jid, job in _jobs.items() if ts - job.created_at > _JOB_TTL_SECONDS]
    for jid in expired:
        _jobs.pop(jid, None)


def create_job(user_id: str) -> ProposeTradesJob:
    with _lock:
        _purge_expired_locked()
        job = ProposeTradesJob(job_id=str(uuid.uuid4()), user_id=user_id)
        _jobs[job.job_id] = job
        return job


def get_job_for_user(job_id: str, user_id: str) -> ProposeTradesJob | None:
    with _lock:
        _purge_expired_locked()
        job = _jobs.get(job_id)
        if job is None or job.user_id != user_id:
            return None
        return job


def mark_running(job_id: str) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return
        job.status = "running"


def update_progress(
    job_id: str,
    *,
    phase: str,
    message: str,
    progress_current: int,
    progress_total: int,
) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if job is None or job.status not in ("queued", "running"):
            return
        job.status = "running"
        job.phase = phase
        job.message = message
        job.progress_current = max(0, progress_current)
        job.progress_total = max(1, progress_total)
        total = job.progress_total
        current = min(job.progress_current, total)
        job.progress_pct = min(100, round(100 * current / total))


def complete_job(job_id: str, result: dict[str, Any]) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return
        job.status = "done"
        job.phase = "done"
        job.message = "Complete"
        job.progress_current = job.progress_total
        job.progress_pct = 100
        job.result = result
        job.finished_at = time.time()


def fail_job(job_id: str, error: str) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return
        job.status = "error"
        job.phase = "error"
        job.message = error
        job.error = error
        job.finished_at = time.time()


def job_to_status_dict(job: ProposeTradesJob) -> dict[str, Any]:
    out: dict[str, Any] = {
        "job_id": job.job_id,
        "status": job.status,
        "phase": job.phase,
        "message": job.message,
        "progress_pct": job.progress_pct,
        "progress_current": job.progress_current,
        "progress_total": job.progress_total,
    }
    if job.status == "done" and job.result is not None:
        out["result"] = job.result
    if job.status == "error" and job.error:
        out["error"] = job.error
    return out
