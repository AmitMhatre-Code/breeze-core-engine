"""SQLite migration for per-user parked (draft) orders."""
from __future__ import annotations

import sqlite3


def ensure_parked_orders_table(db_path: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS parked_orders (
                id TEXT PRIMARY KEY NOT NULL,
                user_id TEXT NOT NULL,
                product_type TEXT NOT NULL DEFAULT 'Options',
                stock_code TEXT NOT NULL,
                exchange_code TEXT NOT NULL DEFAULT 'NFO',
                expiry_date TEXT NOT NULL,
                right TEXT NOT NULL,
                strike_price TEXT NOT NULL,
                quantity TEXT NOT NULL,
                price TEXT NOT NULL DEFAULT '0',
                action TEXT NOT NULL,
                chunk_qty TEXT,
                batch_group_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_parked_orders_user_created "
            "ON parked_orders(user_id, created_at DESC)"
        )
        conn.commit()
