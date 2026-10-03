"""Tables for Dynamic Iron Condor campaigns (docs/dynamic-iron-condor-plan.md section 2).

All in users.sqlite3, beside the bots':

* `condor_campaigns` -- one campaign: its settings (frozen at creation, replaced only by an
  explicit edit), status and how it ended.
* `condor_cycles` -- one expiry the campaign traded; a time roll opens the next.
* `condor_fills` -- the ledger. Every rupee in and out over the campaign's life, one row per
  filled order (or per adopted / assigned leg), with its charges. Sells add, buys subtract;
  the sum is the plan's "total net credit".
* `condor_decisions` -- what each check decided and why, manual tickets included.
* `condor_left_out` -- position differences the user chose to keep outside the ledger.

A campaign owns its NIFTY + cycle-expiry group; one active campaign per group is enforced
in `repositories/condor.py`, since the expiry lives on the cycle, not the campaign.
"""
from __future__ import annotations

import sqlite3


def ensure_condor_tables(db_path: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS condor_campaigns (
                id TEXT PRIMARY KEY NOT NULL,
                user_id TEXT NOT NULL,
                underlying TEXT NOT NULL,
                exchange_code TEXT NOT NULL,
                origin TEXT NOT NULL,
                mode TEXT NOT NULL,
                status TEXT NOT NULL,
                settings TEXT NOT NULL,
                created_at TEXT NOT NULL,
                closed_at TEXT,
                close_reason TEXT,
                note TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_condor_campaigns_user ON condor_campaigns(user_id, status);

            CREATE TABLE IF NOT EXISTS condor_cycles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id TEXT NOT NULL REFERENCES condor_campaigns(id) ON DELETE CASCADE,
                expiry TEXT NOT NULL,
                opened_at TEXT NOT NULL,
                closed_at TEXT,
                close_reason TEXT,
                tranches_entered INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_condor_cycles_campaign ON condor_cycles(campaign_id);

            CREATE TABLE IF NOT EXISTS condor_fills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id TEXT NOT NULL REFERENCES condor_campaigns(id) ON DELETE CASCADE,
                cycle_id INTEGER REFERENCES condor_cycles(id) ON DELETE CASCADE,
                order_id TEXT,
                tag TEXT,
                expiry TEXT NOT NULL,
                strike REAL NOT NULL,
                right TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                price REAL NOT NULL,
                charges REAL NOT NULL DEFAULT 0,
                kind TEXT NOT NULL,
                filled_at TEXT NOT NULL,
                note TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_condor_fills_campaign ON condor_fills(campaign_id);
            -- One ledger row per order: a fill read twice must not be booked twice.
            CREATE UNIQUE INDEX IF NOT EXISTS ux_condor_fills_order
                ON condor_fills(campaign_id, order_id) WHERE order_id IS NOT NULL;

            CREATE TABLE IF NOT EXISTS condor_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id TEXT NOT NULL REFERENCES condor_campaigns(id) ON DELETE CASCADE,
                at TEXT NOT NULL,
                check_kind TEXT NOT NULL,
                action TEXT NOT NULL,
                reason TEXT NOT NULL,
                text TEXT NOT NULL,
                snapshot TEXT,
                outcome TEXT,
                note TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_condor_decisions_campaign ON condor_decisions(campaign_id, at);

            -- One row per executed ticket (an engine suggestion or a manual Adjust ticket).
            -- `steps` is written before each order goes out and after it settles, so a crash
            -- mid-sequence leaves the order ids and states it had reached.
            CREATE TABLE IF NOT EXISTS condor_executions (
                id TEXT PRIMARY KEY NOT NULL,
                campaign_id TEXT NOT NULL REFERENCES condor_campaigns(id) ON DELETE CASCADE,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                finished_at TEXT,
                steps TEXT NOT NULL DEFAULT '[]',
                message TEXT,
                note TEXT,
                warnings TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_condor_executions_campaign ON condor_executions(campaign_id, created_at);

            CREATE TABLE IF NOT EXISTS condor_left_out (
                campaign_id TEXT NOT NULL REFERENCES condor_campaigns(id) ON DELETE CASCADE,
                expiry TEXT NOT NULL,
                strike REAL NOT NULL,
                right TEXT NOT NULL,
                units INTEGER NOT NULL,
                at TEXT NOT NULL,
                PRIMARY KEY (campaign_id, expiry, strike, right)
            );
            """
        )
        conn.commit()
