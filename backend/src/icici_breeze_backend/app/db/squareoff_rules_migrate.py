"""SQLite migration for per-user, per-group (stock+expiry) profit/loss
square-off rules."""
from __future__ import annotations

import sqlite3


def ensure_squareoff_rules_table(db_path: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS portfolio_squareoff_rules (
                id TEXT PRIMARY KEY NOT NULL,
                user_id TEXT NOT NULL,
                stock_code TEXT NOT NULL,
                expiry_display TEXT NOT NULL,
                exchange_code TEXT NOT NULL DEFAULT 'NFO',
                profit_target_pnl REAL NOT NULL,
                loss_limit_pnl REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'armed',
                leg_results TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                fired_at TIMESTAMP
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_squareoff_rules_user_status "
            "ON portfolio_squareoff_rules(user_id, status)"
        )
        conn.commit()
