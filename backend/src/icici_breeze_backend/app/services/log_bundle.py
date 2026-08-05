"""Assembles the downloadable log bundle from the on-disk sink.

Scope is the **whole deployment**, not the requesting user's own lines. The records that
make this worth downloading — chain-builder timings, ICICI pacing, the reference-data
scheduler, portal heartbeats — carry no user at all, so a per-user filter would hand back
the least useful half. A deployment is one customer's own instance on their own EC2 host,
and anyone with the SSH key can already read these files directly.
"""
from __future__ import annotations

import io
import os
import time
import zipfile
from dataclasses import dataclass
from typing import List, Optional

from icici_breeze_backend.app.core.log_sink import logs_dir, retention_days

MAX_DAYS = 30


@dataclass(frozen=True)
class LogFile:
    name: str
    size_bytes: int
    modified_at: float


def _is_log_file(name: str) -> bool:
    # RotatingFileHandler backups are "<name>.jsonl.1", "<name>.jsonl.2", ...
    return ".jsonl" in name


def list_log_files(days: int, directory: Optional[str] = None) -> List[LogFile]:
    """Log files modified within `days`, newest first."""
    directory = directory or logs_dir()
    cutoff = time.time() - clamp_days(days) * 86400
    found: List[LogFile] = []
    try:
        entries = os.listdir(directory)
    except OSError:
        return []
    for name in sorted(entries):
        path = os.path.join(directory, name)
        try:
            if not os.path.isfile(path) or not _is_log_file(name):
                continue
            stat = os.stat(path)
        except OSError:
            continue
        if stat.st_mtime >= cutoff:
            found.append(LogFile(name, stat.st_size, stat.st_mtime))
    return sorted(found, key=lambda f: f.modified_at, reverse=True)


def clamp_days(days: int) -> int:
    """Bound the window to what's actually retained on disk.

    Asking for 90 days can't produce more than `retention_days()` of data, and letting an
    unbounded value through just invites a pointless full-directory scan.
    """
    try:
        value = int(days)
    except (TypeError, ValueError):
        value = retention_days()
    return max(1, min(value, MAX_DAYS))


def total_bytes(days: int, directory: Optional[str] = None) -> int:
    return sum(f.size_bytes for f in list_log_files(days, directory))


def build_zip(days: int, directory: Optional[str] = None) -> bytes:
    """Zip the in-window log files.

    Built in memory deliberately: the cap on the sink keeps the total small, and writing
    a temp file would put the bundle on the same volume the cap exists to protect.
    """
    directory = directory or logs_dir()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for log_file in list_log_files(days, directory):
            path = os.path.join(directory, log_file.name)
            try:
                archive.write(path, arcname=log_file.name)
            except OSError:
                # A file rotated away mid-build is expected, not an error.
                continue
    return buffer.getvalue()


def bundle_filename(days: int) -> str:
    return f"breeze-logs-{time.strftime('%Y%m%d-%H%M%S')}-{clamp_days(days)}d.zip"
