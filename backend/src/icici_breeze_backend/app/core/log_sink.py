"""Rotating on-disk log sink, retained for user download.

`docker logs` is capped and unreadable to the person who actually owns the deployment,
and running the console at WARNING means the detail is gone by the time anyone asks
"why was yesterday slow?". This writes JSON lines to the data volume at its own level,
independent of what the console prints, capped so it can never fill the 2 GiB volume the
sqlite databases share.

One file per process, not per user: the API process and the `chain_builder` worker are
separate OS processes, and a shared `RotatingFileHandler` corrupts rotation because each
process rotates without knowing about the other's file handles.
"""
from __future__ import annotations

import json
import logging
import os
import time
from logging.handlers import RotatingFileHandler
from typing import Optional

import icici_breeze_backend.app.core.config as cfg

LOG_DIR_NAME = "logs"
DEFAULT_LEVEL = logging.INFO
# 5 x 4MB per process x 2 processes = 40MB worst case, on a volume that also holds the
# sqlite databases. Deliberately well under the volume size: the cap exists so a runaway
# error loop can't take writes down with it, not to maximise history.
DEFAULT_MAX_BYTES = 4 * 1024 * 1024
DEFAULT_BACKUP_COUNT = 5
DEFAULT_RETENTION_DAYS = 7


def logs_dir() -> str:
    return os.path.join(cfg.DATA_PATH, LOG_DIR_NAME)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name) or default)
    except (TypeError, ValueError):
        return default


def sink_enabled() -> bool:
    return (os.environ.get("LOG_SINK", "").strip().lower()
            not in ("0", "false", "no", "off"))


def retention_days() -> int:
    return max(1, _env_int("LOG_SINK_RETENTION_DAYS", DEFAULT_RETENTION_DAYS))


def sink_level() -> int:
    return _env_int("LOG_SINK_LEVEL", DEFAULT_LEVEL)


class JsonLinesFormatter(logging.Formatter):
    """One JSON object per line — greppable by eye, parseable by tooling."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime(
                "%Y-%m-%dT%H:%M:%S", time.localtime(record.created)
            ) + f".{int(record.msecs):03d}",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        elif record.exc_text:
            payload["exception"] = record.exc_text
        correlation_id = getattr(record, "correlation_id", None)
        if correlation_id:
            payload["correlation_id"] = correlation_id
        # default=str so an un-serialisable arg degrades to its repr instead of losing
        # the whole line.
        return json.dumps(payload, default=str, ensure_ascii=False)


def prune_expired(directory: Optional[str] = None, days: Optional[int] = None) -> int:
    """Delete rotated files older than the retention window. Returns files removed.

    Size is bounded by RotatingFileHandler; this bounds *age*, so a quiet deployment
    doesn't hand back months-old lines as if they were recent.
    """
    directory = directory or logs_dir()
    cutoff = time.time() - (days or retention_days()) * 86400
    removed = 0
    try:
        entries = os.listdir(directory)
    except OSError:
        return 0
    for name in entries:
        path = os.path.join(directory, name)
        try:
            if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                os.remove(path)
                removed += 1
        except OSError:
            continue
    return removed


def build_handler(process_name: str) -> Optional[RotatingFileHandler]:
    """Rotating JSON-lines handler for this process, or None if unavailable.

    Returns None rather than raising: a read-only or full data volume must not stop the
    application from starting.
    """
    directory = logs_dir()
    try:
        os.makedirs(directory, exist_ok=True)
        handler = RotatingFileHandler(
            os.path.join(directory, f"{process_name}.jsonl"),
            maxBytes=_env_int("LOG_SINK_MAX_BYTES", DEFAULT_MAX_BYTES),
            backupCount=_env_int("LOG_SINK_BACKUP_COUNT", DEFAULT_BACKUP_COUNT),
            encoding="utf-8",
        )
    except OSError:
        return None
    handler.setLevel(sink_level())
    handler.setFormatter(JsonLinesFormatter())
    return handler
