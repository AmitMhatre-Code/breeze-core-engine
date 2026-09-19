"""The signal grid's one setting: which mechanism the navbar shows (decision 11).

Everything else about a signal is fixed by its definition (`mechanisms`), so there is nothing to
tune -- tuning a signal against its own backtest is choosing the winner after seeing the results.
One global row in users.sqlite3, as the deployment is single-tenant; no environment variable
(#30: a knob nobody on a CloudFormation instance can turn is not a knob).
"""
from __future__ import annotations

import sqlite3
import threading
from typing import Optional

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.timezone import now_ist
from icici_breeze_backend.app.db.signals_migrate import ensure_signal_tables
from icici_breeze_backend.app.services.index_signal.mechanisms import MECHANISMS

DEFAULT_NAVBAR_MECHANISM = "expansion"

_lock = threading.Lock()
_ensured: set[str] = set()
_cached: dict[str, str] = {}


def _db_path() -> str:
    return cfg.DATA_PATH + cfg.USERS_DB


def _ensure(path: str) -> None:
    if path in _ensured:
        return
    ensure_signal_tables(path)
    _ensured.add(path)


def navbar_mechanism(*, db_path: Optional[str] = None) -> str:
    path = db_path or _db_path()
    with _lock:
        if path in _cached:
            return _cached[path]
        _ensure(path)
        with sqlite3.connect(path) as conn:
            row = conn.execute("SELECT navbar_mechanism FROM signal_settings WHERE id = 1").fetchone()
        value = str(row[0]) if row and row[0] in MECHANISMS else DEFAULT_NAVBAR_MECHANISM
        _cached[path] = value
        return value


def set_navbar_mechanism(mechanism: str, *, db_path: Optional[str] = None) -> str:
    if mechanism not in MECHANISMS:
        raise ValueError(f"Unknown signal mechanism {mechanism!r}.")
    path = db_path or _db_path()
    with _lock:
        _ensure(path)
        with sqlite3.connect(path) as conn:
            conn.execute(
                """
                INSERT INTO signal_settings (id, navbar_mechanism, updated_at) VALUES (1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET navbar_mechanism = excluded.navbar_mechanism,
                                              updated_at = excluded.updated_at
                """,
                (mechanism, now_ist().isoformat(timespec="seconds")),
            )
        _cached[path] = mechanism
        return mechanism


def reset_cache_for_tests() -> None:
    with _lock:
        _cached.clear()
        _ensured.clear()
