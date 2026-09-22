"""SQLite persistence for the daily ICICI call budget backtests may spend.

Global/singleton, not per-user -- the budget bounds one deployment's share of ICICI's daily
allowance, and the app is one backend process per deployment. Cloned from the
`pnl_engine_settings` singleton-row pattern.

Why this is a setting and not a constant: backtests are advisory work, so the budget is
deliberately a fraction of ICICI's ~5,000-a-day allowance -- the rest belongs to live trading,
the dashboard and reference data. But on a day with no trading (a holiday, or an evening after
a quiet session) most of that allowance is still unspent, and a backfill that would otherwise
take a week of 800-call days can be done in one sitting. The default stays 800; raising it is a
deliberate act for a specific day, which is why the UI shows what has already been spent.

It lives in `users.sqlite3`, not in the backtest cache (`backtest.sqlite3`): the cache is
explicitly disposable -- "deleting it costs nothing but a re-fetch" -- and a setting that
silently reverted when the cache was cleared would be a trap.

The *ledger* of calls already spent stays in the cache next to the data those calls bought
(`backtest_store.calls_spent`). Only the ceiling lives here.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Optional

import icici_breeze_backend.app.core.config as cfg

#: What the budget ships as, and what it falls back to if the row is unreadable.
DEFAULT_DAILY_CALL_BUDGET = 800

#: The hard floor. 0 is allowed and means "never fetch" -- a legitimate way to force replay on
#: cached data only, which is what a run during market hours does anyway.
MIN_DAILY_CALL_BUDGET = 0

#: The hard ceiling: ICICI's own daily allowance. Setting the budget here hands the whole day's
#: allowance to backtests, which is only ever right on a day nothing else is running.
MAX_DAILY_CALL_BUDGET = 5000

#: Above this, the UI warns -- past roughly half the daily allowance a backtest starts competing
#: with everything else the deployment does, even outside market hours (reference data, the
#: portal heartbeat's broker probes).
RECOMMENDED_MAX_DAILY_CALL_BUDGET = 2500


def _db_path() -> str:
    return cfg.DATA_PATH + cfg.USERS_DB


def _clamp(value: int) -> int:
    return max(MIN_DAILY_CALL_BUDGET, min(MAX_DAILY_CALL_BUDGET, value))


def ensure_backtest_budget_table(db_path: str | None = None) -> None:
    path = db_path or _db_path()
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS backtest_budget_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                daily_call_budget INTEGER NOT NULL DEFAULT 800
            )
            """
        )
        row = conn.execute("SELECT 1 FROM backtest_budget_settings WHERE id = 1").fetchone()
        if not row:
            conn.execute(
                "INSERT INTO backtest_budget_settings (id, daily_call_budget) VALUES (1, ?)",
                (DEFAULT_DAILY_CALL_BUDGET,),
            )
        conn.commit()


def get_daily_call_budget(db_path: Optional[str] = None) -> int:
    """Fresh read every call (no cache), so a change takes effect on the next backtest without
    a restart.

    Deliberately read-only: it never creates the table. A getter that ran DDL would have every
    replay -- including one in a test with its own temp DB -- writing to whichever
    `users.sqlite3` the config happened to point at. Creation belongs to startup
    (`main.ensure_backtest_budget_table`) and to `set_daily_call_budget`.

    Any failure -- no table yet, a locked or missing DB -- reads as the default rather than as
    zero: a deployment that cannot read its settings should still be able to fetch."""
    row: tuple[Any, ...] | None
    try:
        with sqlite3.connect(f"file:{db_path or _db_path()}?mode=ro", uri=True) as conn:
            row = conn.execute(
                "SELECT daily_call_budget FROM backtest_budget_settings WHERE id = 1"
            ).fetchone()
    except sqlite3.Error:
        row = None
    if not row or row[0] is None:
        return DEFAULT_DAILY_CALL_BUDGET
    try:
        return _clamp(int(row[0]))
    except (TypeError, ValueError):
        return DEFAULT_DAILY_CALL_BUDGET


def set_daily_call_budget(value: int, db_path: Optional[str] = None) -> int:
    """Validates against the hard bounds (raises ValueError if out of range -- routes should map
    that to a 422) and persists. Returns what was stored."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError("daily_call_budget must be a whole number")
    if not (MIN_DAILY_CALL_BUDGET <= parsed <= MAX_DAILY_CALL_BUDGET):
        raise ValueError(
            f"daily_call_budget must be between {MIN_DAILY_CALL_BUDGET} "
            f"and {MAX_DAILY_CALL_BUDGET}"
        )
    ensure_backtest_budget_table(db_path)
    with sqlite3.connect(db_path or _db_path()) as conn:
        conn.execute(
            "UPDATE backtest_budget_settings SET daily_call_budget = ? WHERE id = 1",
            (parsed,),
        )
        conn.commit()
    return parsed
