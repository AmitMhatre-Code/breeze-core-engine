"""How full the data volume is, and the one threshold that turns on the banner and halts backtests.

The data volume is whatever filesystem `DATA_PATH` sits on: on a customer stack, the EBS volume
mounted at `/opt/breeze-core-engine/data` and bind-mounted into the container at
`backend/data`. It is read with `statvfs` from inside the container -- no AWS call, no IAM
permission, and the figure is live on every request. The root volume (OS, Docker images) is a
different EBS volume and is deliberately not measured: nothing this app can delete lives there.

The percentage is `df`'s: used / (used + available to this process). ext4 reserves ~5% of blocks
for root, and counting them as free would let the volume reach "100% full" at 95%.

The threshold is a setting, not a constant, and lives in `users.sqlite3` beside the other
singleton settings (the `backtest_budget_settings` pattern). One level does both jobs: at or past
it the app shows a banner on every page, refuses new backtests, and halts the parts of a running
backtest that write to the volume (docs/design-decisions.md #44).
"""
from __future__ import annotations

import os
import shutil
import sqlite3
from typing import Any, Optional

import icici_breeze_backend.app.core.config as cfg

DEFAULT_THRESHOLD_PCT = 85
MIN_THRESHOLD_PCT = 50
MAX_THRESHOLD_PCT = 98


class StorageFull(ValueError):
    """Refused because the data volume is past the threshold. A ValueError so every backtest
    route already maps it to a 400 carrying this message."""


def _db_path() -> str:
    return cfg.DATA_PATH + cfg.USERS_DB


def ensure_storage_settings_table(db_path: Optional[str] = None) -> None:
    with sqlite3.connect(db_path or _db_path()) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS storage_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                threshold_pct INTEGER NOT NULL DEFAULT 85
            )
            """
        )
        if not conn.execute("SELECT 1 FROM storage_settings WHERE id = 1").fetchone():
            conn.execute(
                "INSERT INTO storage_settings (id, threshold_pct) VALUES (1, ?)", (DEFAULT_THRESHOLD_PCT,)
            )
        conn.commit()


def get_threshold_pct(db_path: Optional[str] = None) -> int:
    """Fresh read, never DDL (see `backtest_budget.get_daily_call_budget`). Unreadable -> default:
    a deployment that cannot read its settings still gets its banner at the usual point."""
    try:
        with sqlite3.connect(f"file:{db_path or _db_path()}?mode=ro", uri=True) as conn:
            row = conn.execute("SELECT threshold_pct FROM storage_settings WHERE id = 1").fetchone()
    except sqlite3.Error:
        row = None
    try:
        value = int(row[0]) if row and row[0] is not None else DEFAULT_THRESHOLD_PCT
    except (TypeError, ValueError):
        return DEFAULT_THRESHOLD_PCT
    return max(MIN_THRESHOLD_PCT, min(MAX_THRESHOLD_PCT, value))


def set_threshold_pct(value: Any, db_path: Optional[str] = None) -> int:
    """Raises ValueError outside the bounds (routes map it to a 400). Returns what was stored."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError("threshold_pct must be a whole number") from None
    if not (MIN_THRESHOLD_PCT <= parsed <= MAX_THRESHOLD_PCT):
        raise ValueError(f"threshold_pct must be between {MIN_THRESHOLD_PCT} and {MAX_THRESHOLD_PCT}")
    ensure_storage_settings_table(db_path)
    with sqlite3.connect(db_path or _db_path()) as conn:
        conn.execute("UPDATE storage_settings SET threshold_pct = ? WHERE id = 1", (parsed,))
        conn.commit()
    return parsed


def data_dir() -> str:
    return os.path.abspath(cfg.DATA_PATH)


def volume() -> Optional[dict[str, Any]]:
    """{total, used, free, used_pct} of the data volume in bytes, or None if it cannot be read."""
    try:
        du = shutil.disk_usage(data_dir())
    except OSError:
        return None
    usable = du.used + du.free
    used_pct = (du.used / usable * 100.0) if usable > 0 else 0.0
    return {
        "total_bytes": du.total,
        "used_bytes": du.used,
        "free_bytes": du.free,
        "used_pct": round(used_pct, 1),
    }


def status(db_path: Optional[str] = None) -> dict[str, Any]:
    """What the banner polls. Cheap: one statvfs and one single-row read."""
    threshold = get_threshold_pct(db_path)
    vol = volume()
    over = bool(vol is not None and vol["used_pct"] >= threshold)
    return {
        "available": vol is not None,
        **(vol or {"total_bytes": None, "used_bytes": None, "free_bytes": None, "used_pct": None}),
        "threshold_pct": threshold,
        "over_threshold": over,
        "message": _message(vol["used_pct"], threshold) if over and vol else None,
    }


def _message(used_pct: float, threshold: int) -> str:
    return (
        f"Storage is {used_pct:.0f}% full (threshold {threshold}%). Free up space in "
        "Settings → Storage to keep the app running smoothly; backtests that download or "
        "write data are paused until then."
    )


def halt_reason(db_path: Optional[str] = None) -> Optional[str]:
    """The sentence a backtest stops with, or None while there is room. A volume that cannot be
    read is not treated as full -- the same rule as the memory check."""
    vol = volume()
    if vol is None:
        return None
    threshold = get_threshold_pct(db_path)
    if vol["used_pct"] < threshold:
        return None
    return _message(vol["used_pct"], threshold)


def refuse_if_full() -> None:
    reason = halt_reason()
    if reason:
        raise StorageFull(reason)
