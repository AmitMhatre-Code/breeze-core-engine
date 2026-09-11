"""Persisted, runtime-tunable index-signal settings (Settings -> Index Signal).

One global row in users.sqlite3, cloned from `pnl_engine_settings`: the signal is app-wide, so its
tuning is too. The publisher loop reads it fresh every iteration, which is how a change reaches the
running engines and depth feed without a restart. Defaults live here, in code -- there is
deliberately no environment override (docs/design-decisions.md #30).

Bounds are the safety net; the recommended ranges drive the Settings screen's warnings, the same
split `pnl_engine_settings` uses.
"""
from __future__ import annotations

import sqlite3
import threading
from dataclasses import asdict, dataclass, fields, replace
from typing import Any

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.services.index_signal.engine import SignalParams


@dataclass(frozen=True)
class FieldBound:
    min: float
    max: float
    recommended_min: float
    recommended_max: float
    default: float
    integer: bool = False


BOUNDS: dict[str, FieldBound] = {
    # Each tracked name is one depth room per index; past ~10 the added weight is small.
    "top_n": FieldBound(3, 15, 8, 10, 10, integer=True),
    "tau_seconds": FieldBound(1.0, 30.0, 2.0, 5.0, 3.0),
    "enter_threshold": FieldBound(0.05, 0.90, 0.25, 0.40, 0.30),
    # Must also not exceed enter_threshold. Recommended is ~0.6-0.7x the default enter.
    "exit_threshold": FieldBound(0.0, 0.90, 0.15, 0.25, 0.20),
    "min_coverage": FieldBound(0.30, 1.00, 0.60, 0.80, 0.70),
    "book_stale_seconds": FieldBound(5.0, 120.0, 15.0, 45.0, 30.0),
    # Both exchange depth feeds carry five levels, so five is also the ceiling.
    "depth_levels": FieldBound(1, 5, 5, 5, 5, integer=True),
    "shadow_retention_days": FieldBound(7, 365, 60, 180, 90, integer=True),
}


@dataclass(frozen=True)
class IndexSignalSettings:
    enabled: bool = True
    top_n: int = 10
    tau_seconds: float = 3.0
    enter_threshold: float = 0.30
    exit_threshold: float = 0.20
    min_coverage: float = 0.70
    book_stale_seconds: float = 30.0
    depth_levels: int = 5
    shadow_retention_days: int = 90

    def signal_params(self) -> SignalParams:
        return SignalParams(
            tau_seconds=self.tau_seconds,
            enter_threshold=self.enter_threshold,
            exit_threshold=self.exit_threshold,
            min_coverage=self.min_coverage,
            book_stale_seconds=self.book_stale_seconds,
            warmup_seconds=2.0 * self.tau_seconds,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


DEFAULTS = IndexSignalSettings()
_FIELD_NAMES = tuple(f.name for f in fields(IndexSignalSettings))

_lock = threading.Lock()
_ensured_paths: set[str] = set()


def _db_path() -> str:
    return cfg.DATA_PATH + cfg.USERS_DB


def ensure_index_signal_settings_table(db_path: str | None = None) -> None:
    path = db_path or _db_path()
    with _lock:
        if path in _ensured_paths:
            return
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS index_signal_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                enabled INTEGER NOT NULL,
                top_n INTEGER NOT NULL,
                tau_seconds REAL NOT NULL,
                enter_threshold REAL NOT NULL,
                exit_threshold REAL NOT NULL,
                min_coverage REAL NOT NULL,
                book_stale_seconds REAL NOT NULL,
                depth_levels INTEGER NOT NULL,
                shadow_retention_days INTEGER NOT NULL
            )
            """
        )
        if conn.execute("SELECT 1 FROM index_signal_settings WHERE id = 1").fetchone() is None:
            _write(conn, DEFAULTS)
        conn.commit()
    with _lock:
        _ensured_paths.add(path)


def _write(conn: sqlite3.Connection, s: IndexSignalSettings) -> None:
    columns = ", ".join(_FIELD_NAMES)
    placeholders = ", ".join("?" for _ in _FIELD_NAMES)
    updates = ", ".join(f"{name} = excluded.{name}" for name in _FIELD_NAMES)
    values = [int(s.enabled)] + [getattr(s, name) for name in _FIELD_NAMES[1:]]
    conn.execute(
        f"INSERT INTO index_signal_settings (id, {columns}) VALUES (1, {placeholders}) "
        f"ON CONFLICT(id) DO UPDATE SET {updates}",
        values,
    )


def _sanitise(raw: dict[str, Any]) -> IndexSignalSettings:
    """Clamp a stored row into bounds -- a hand-edited or pre-bounds row must never reach the
    engines as something `save` would have refused."""
    out: dict[str, Any] = {"enabled": bool(raw.get("enabled", DEFAULTS.enabled))}
    for name, b in BOUNDS.items():
        try:
            value = float(raw.get(name, b.default))
        except (TypeError, ValueError):
            value = b.default
        value = max(b.min, min(b.max, value))
        out[name] = int(round(value)) if b.integer else value
    out["exit_threshold"] = min(out["exit_threshold"], out["enter_threshold"])
    return IndexSignalSettings(**out)


def load_index_signal_settings() -> IndexSignalSettings:
    """Fresh read every call (no cache): the publisher polling this each loop is how a change
    applies live. Falls back to the defaults if the table cannot be read."""
    try:
        path = _db_path()
        ensure_index_signal_settings_table(path)
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM index_signal_settings WHERE id = 1").fetchone()
    except sqlite3.Error:
        return DEFAULTS
    if row is None:
        return DEFAULTS
    return _sanitise(dict(row))


def _coerce(name: str, value: Any) -> Any:
    if name == "enabled":
        if not isinstance(value, bool):
            raise ValueError("enabled must be true or false")
        return value
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    if BOUNDS[name].integer:
        if number != int(number):
            raise ValueError(f"{name} must be a whole number")
        return int(number)
    return number


def _fmt(value: float) -> str:
    return f"{value:g}"


def validate(s: IndexSignalSettings) -> None:
    for name, b in BOUNDS.items():
        value = getattr(s, name)
        if not b.min <= value <= b.max:
            raise ValueError(f"{name} must be between {_fmt(b.min)} and {_fmt(b.max)}")
    if s.exit_threshold > s.enter_threshold:
        raise ValueError("exit_threshold must not exceed enter_threshold")


def save_index_signal_settings(**changes: Any) -> IndexSignalSettings:
    """Partial update: omitted fields keep their value. Raises ValueError on an unknown field or
    anything out of bounds (routes map that to a 422); nothing is written in that case."""
    unknown = sorted(set(changes) - set(_FIELD_NAMES))
    if unknown:
        raise ValueError(f"unknown index signal setting(s): {', '.join(unknown)}")
    coerced = {name: _coerce(name, value) for name, value in changes.items()}
    merged = replace(load_index_signal_settings(), **coerced)
    validate(merged)
    path = _db_path()
    ensure_index_signal_settings_table(path)
    with sqlite3.connect(path) as conn:
        _write(conn, merged)
        conn.commit()
    return merged


def bounds() -> dict[str, dict[str, Any]]:
    return {name: asdict(b) for name, b in BOUNDS.items()}
