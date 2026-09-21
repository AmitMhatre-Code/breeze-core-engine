"""The signal grid's settings: which mechanism the navbar shows (decision 11), and how many lots
the breakeven bar is priced on.

Nothing here tunes a signal. What a signal *is* stays fixed by its definition (`mechanisms`),
because tuning a signal against its own backtest is choosing the winner after seeing the results.
`cost_lots` is not a signal parameter -- it is a fact about the trader, the size a call would
actually be traded at. Roughly three-quarters of a one-lot round trip is flat brokerage, which
amortises, so the bar at ten lots is about a third of the bar at one; scoring every signal at one
lot was quietly failing signals that a real position would clear (`breakeven`). It changes what
the numbers are compared against, never what the signals say.

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
#: The conservative bar, and what every run before the setting existed was scored against.
DEFAULT_COST_LOTS = 1
#: Beyond this the flat brokerage has amortised to nothing and the bar barely moves, so a bigger
#: number would only invite reading precision into a figure that no longer has any.
MAX_COST_LOTS = 100

_lock = threading.Lock()
_ensured: set[str] = set()
_cached: dict[str, str] = {}
_cached_lots: dict[str, int] = {}


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


def cost_lots(*, db_path: Optional[str] = None) -> int:
    """How many lots the breakeven bar is priced on. See the module docstring."""
    path = db_path or _db_path()
    with _lock:
        if path in _cached_lots:
            return _cached_lots[path]
        _ensure(path)
        with sqlite3.connect(path) as conn:
            row = conn.execute("SELECT cost_lots FROM signal_settings WHERE id = 1").fetchone()
        try:
            value = int(row[0]) if row and row[0] is not None else DEFAULT_COST_LOTS
        except (TypeError, ValueError):
            value = DEFAULT_COST_LOTS
        value = min(MAX_COST_LOTS, max(1, value))
        _cached_lots[path] = value
        return value


def set_cost_lots(lots: int, *, db_path: Optional[str] = None) -> int:
    try:
        value = int(lots)
    except (TypeError, ValueError) as exc:
        raise ValueError("The size must be a whole number of lots.") from exc
    if not 1 <= value <= MAX_COST_LOTS:
        raise ValueError(f"The size must be between 1 and {MAX_COST_LOTS} lots.")
    path = db_path or _db_path()
    with _lock:
        _ensure(path)
        with sqlite3.connect(path) as conn:
            conn.execute(
                """
                INSERT INTO signal_settings (id, cost_lots, updated_at) VALUES (1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET cost_lots = excluded.cost_lots,
                                              updated_at = excluded.updated_at
                """,
                (value, now_ist().isoformat(timespec="seconds")),
            )
        _cached_lots[path] = value
        return value


def reset_cache_for_tests() -> None:
    with _lock:
        _cached.clear()
        _cached_lots.clear()
        _ensured.clear()
