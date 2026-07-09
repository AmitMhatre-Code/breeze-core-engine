"""SQLite persistence for the portfolio P&L engine's runtime-tunable clock
intervals (WS quote flush + P&L recompute). Global/singleton, not per-user —
these are process-wide loop timers, cloned from the
`reference_data.state.reference_data_schedule` singleton-row pattern.

Bounds rationale: the original design fixed the quote flush at a strict
1.5-2.0s window and the P&L recompute floor at 1.0s to bound CPU/Redis load
on a small burstable instance. Making these user-configurable means the hard
floor/ceiling below now live here as a safety net — the Settings UI is
responsible for warning the user well before they reach them.
"""
from __future__ import annotations

import sqlite3
from typing import Any

import icici_breeze_backend.app.core.config as cfg

QUOTE_FLUSH_MIN_SECONDS = 0.5
QUOTE_FLUSH_MAX_SECONDS = 10.0
QUOTE_FLUSH_RECOMMENDED_MIN_SECONDS = 1.5
QUOTE_FLUSH_RECOMMENDED_MAX_SECONDS = 2.0

PNL_RECOMPUTE_MIN_SECONDS = 1.0
PNL_RECOMPUTE_MAX_SECONDS = 30.0
PNL_RECOMPUTE_RECOMMENDED_MIN_SECONDS = 2.0
PNL_RECOMPUTE_RECOMMENDED_MAX_SECONDS = 5.0


def _db_path() -> str:
    return cfg.DATA_PATH + cfg.USERS_DB


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _default_quote_flush_seconds() -> float:
    try:
        v = float(getattr(cfg, "PNL_QUOTE_FLUSH_INTERVAL_SECONDS", 2.0))
    except (TypeError, ValueError):
        v = 2.0
    return _clamp(v, QUOTE_FLUSH_MIN_SECONDS, QUOTE_FLUSH_MAX_SECONDS)


def _default_pnl_recompute_seconds() -> float:
    try:
        v = float(getattr(cfg, "PNL_ENGINE_INTERVAL_SECONDS", 2.0))
    except (TypeError, ValueError):
        v = 2.0
    return _clamp(v, PNL_RECOMPUTE_MIN_SECONDS, PNL_RECOMPUTE_MAX_SECONDS)


def ensure_pnl_engine_settings_table(db_path: str | None = None) -> None:
    path = db_path or _db_path()
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pnl_engine_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                quote_flush_interval_seconds REAL NOT NULL DEFAULT 2.0,
                pnl_recompute_interval_seconds REAL NOT NULL DEFAULT 2.0
            )
            """
        )
        row = conn.execute("SELECT 1 FROM pnl_engine_settings WHERE id = 1").fetchone()
        if not row:
            conn.execute(
                """
                INSERT INTO pnl_engine_settings
                  (id, quote_flush_interval_seconds, pnl_recompute_interval_seconds)
                VALUES (1, ?, ?)
                """,
                (_default_quote_flush_seconds(), _default_pnl_recompute_seconds()),
            )
        conn.commit()


def load_pnl_engine_settings() -> dict[str, Any]:
    """Fresh read every call (no cache) — callers polling this every loop
    iteration is how live reload without a restart works."""
    row: tuple[Any, ...] | None
    try:
        ensure_pnl_engine_settings_table()
        with sqlite3.connect(_db_path()) as conn:
            row = conn.execute(
                "SELECT quote_flush_interval_seconds, pnl_recompute_interval_seconds "
                "FROM pnl_engine_settings WHERE id = 1"
            ).fetchone()
    except sqlite3.Error:
        row = None
    if not row:
        return {
            "quote_flush_interval_seconds": _default_quote_flush_seconds(),
            "pnl_recompute_interval_seconds": _default_pnl_recompute_seconds(),
        }
    return {
        "quote_flush_interval_seconds": _clamp(
            float(row[0]), QUOTE_FLUSH_MIN_SECONDS, QUOTE_FLUSH_MAX_SECONDS
        ),
        "pnl_recompute_interval_seconds": _clamp(
            float(row[1]), PNL_RECOMPUTE_MIN_SECONDS, PNL_RECOMPUTE_MAX_SECONDS
        ),
    }


def save_pnl_engine_settings(
    *,
    quote_flush_interval_seconds: float | None = None,
    pnl_recompute_interval_seconds: float | None = None,
) -> dict[str, Any]:
    """Validates against the hard bounds (raises ValueError if out of range —
    routes should map that to a 422) and persists. Either field may be
    omitted to leave it unchanged."""
    if quote_flush_interval_seconds is not None and not (
        QUOTE_FLUSH_MIN_SECONDS <= quote_flush_interval_seconds <= QUOTE_FLUSH_MAX_SECONDS
    ):
        raise ValueError(
            f"quote_flush_interval_seconds must be between {QUOTE_FLUSH_MIN_SECONDS} "
            f"and {QUOTE_FLUSH_MAX_SECONDS}"
        )
    if pnl_recompute_interval_seconds is not None and not (
        PNL_RECOMPUTE_MIN_SECONDS <= pnl_recompute_interval_seconds <= PNL_RECOMPUTE_MAX_SECONDS
    ):
        raise ValueError(
            f"pnl_recompute_interval_seconds must be between {PNL_RECOMPUTE_MIN_SECONDS} "
            f"and {PNL_RECOMPUTE_MAX_SECONDS}"
        )
    ensure_pnl_engine_settings_table()
    current = load_pnl_engine_settings()
    new_flush = (
        quote_flush_interval_seconds
        if quote_flush_interval_seconds is not None
        else current["quote_flush_interval_seconds"]
    )
    new_recompute = (
        pnl_recompute_interval_seconds
        if pnl_recompute_interval_seconds is not None
        else current["pnl_recompute_interval_seconds"]
    )
    with sqlite3.connect(_db_path()) as conn:
        conn.execute(
            """
            INSERT INTO pnl_engine_settings
              (id, quote_flush_interval_seconds, pnl_recompute_interval_seconds)
            VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              quote_flush_interval_seconds = excluded.quote_flush_interval_seconds,
              pnl_recompute_interval_seconds = excluded.pnl_recompute_interval_seconds
            """,
            (new_flush, new_recompute),
        )
        conn.commit()
    return {
        "quote_flush_interval_seconds": new_flush,
        "pnl_recompute_interval_seconds": new_recompute,
    }


def bounds() -> dict[str, float]:
    return {
        "quote_flush_min_seconds": QUOTE_FLUSH_MIN_SECONDS,
        "quote_flush_max_seconds": QUOTE_FLUSH_MAX_SECONDS,
        "quote_flush_recommended_min_seconds": QUOTE_FLUSH_RECOMMENDED_MIN_SECONDS,
        "quote_flush_recommended_max_seconds": QUOTE_FLUSH_RECOMMENDED_MAX_SECONDS,
        "pnl_recompute_min_seconds": PNL_RECOMPUTE_MIN_SECONDS,
        "pnl_recompute_max_seconds": PNL_RECOMPUTE_MAX_SECONDS,
        "pnl_recompute_recommended_min_seconds": PNL_RECOMPUTE_RECOMMENDED_MIN_SECONDS,
        "pnl_recompute_recommended_max_seconds": PNL_RECOMPUTE_RECOMMENDED_MAX_SECONDS,
    }
