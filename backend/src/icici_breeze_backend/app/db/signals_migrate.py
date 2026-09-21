"""Tables for the signal grid, and the retirement of the old signal's tables.

docs/signals-streamline-plan.md. Two tables are created in users.sqlite3:

* `signal_settings` -- one global row: which mechanism the navbar shows, and how many lots the
  breakeven bar is priced on. The signal is app-wide (single-tenant deployment), so its settings
  are too.
* `signal_backtest_runs` -- one row per signal backtest: its range, status, the mechanism versions
  it replayed, its headline summary and where its zip is. The 30-day availability gate reads it.

Four tables are dropped (decision 14): the shadow log of live readings (`index_signal_log`), the
user-created variants (`index_signal_variants`), the W-OBI tuning row (`index_signal_settings`)
and the constituent weights (`index_constituent_weights`). Nothing stored in them is used again:
live readings are no longer kept, and every mechanism that survives is replayed from history.
"""
from __future__ import annotations

import sqlite3

RETIRED_TABLES = (
    "index_signal_log",
    "index_signal_variants",
    "index_signal_settings",
    "index_constituent_weights",
)


def ensure_signal_tables(db_path: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS signal_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                navbar_mechanism TEXT NOT NULL DEFAULT 'expansion',
                updated_at TEXT
            )
            """
        )
        # Added after the table shipped: the size the breakeven bar is priced on. 1 keeps the
        # conservative one-lot bar every earlier run was scored against, so an existing
        # deployment reads the same until someone changes it.
        columns = {r[1] for r in conn.execute("PRAGMA table_info(signal_settings)")}
        if "cost_lots" not in columns:
            conn.execute("ALTER TABLE signal_settings ADD COLUMN cost_lots INTEGER NOT NULL DEFAULT 1")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS signal_backtest_runs (
                id TEXT PRIMARY KEY NOT NULL,
                user_id TEXT,
                triggered_at TEXT NOT NULL,
                finished_at TEXT,
                period TEXT NOT NULL,
                from_date TEXT NOT NULL,
                to_date TEXT NOT NULL,
                status TEXT NOT NULL,
                versions TEXT NOT NULL DEFAULT '{}',
                summary TEXT,
                notes TEXT,
                error TEXT,
                calls INTEGER NOT NULL DEFAULT 0,
                zip_path TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_signal_backtest_runs_triggered "
            "ON signal_backtest_runs (triggered_at DESC)"
        )
        for table in RETIRED_TABLES:
            conn.execute(f"DROP TABLE IF EXISTS {table}")
