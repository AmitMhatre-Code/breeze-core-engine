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
                target_premium_pct INTEGER NOT NULL DEFAULT 1,
                stop_loss_premium_pct INTEGER NOT NULL DEFAULT 1,
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
        cols = {row[1] for row in conn.execute('PRAGMA table_info("portfolio_squareoff_rules")')}
        if "target_premium_pct" not in cols:
            conn.execute(
                "ALTER TABLE portfolio_squareoff_rules "
                "ADD COLUMN target_premium_pct INTEGER NOT NULL DEFAULT 1"
            )
        if "stop_loss_premium_pct" not in cols:
            conn.execute(
                "ALTER TABLE portfolio_squareoff_rules "
                "ADD COLUMN stop_loss_premium_pct INTEGER NOT NULL DEFAULT 1"
            )
        conn.commit()
