"""SQLite migration for the durable last-known-good websocket quote snapshot.

Lives in scrips.sqlite3 rather than users.sqlite3 because the snapshot is
regenerable cache data (rebuilt from the next session's ticks), not user state.
"""
from __future__ import annotations

import sqlite3


def ensure_quote_snapshot_table(db_path: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ws_quote_snapshot (
                trading_date TEXT NOT NULL,
                exchange_code TEXT NOT NULL,
                stock_code TEXT NOT NULL,
                expiry_display TEXT NOT NULL,
                strike_key TEXT NOT NULL,
                option_right TEXT NOT NULL,
                cell_json TEXT NOT NULL,
                captured_at REAL NOT NULL,
                PRIMARY KEY (
                    trading_date, exchange_code, stock_code,
                    expiry_display, strike_key, option_right
                )
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ws_quote_snapshot_date_chain "
            "ON ws_quote_snapshot(trading_date, exchange_code, stock_code, expiry_display)"
        )
        conn.commit()
