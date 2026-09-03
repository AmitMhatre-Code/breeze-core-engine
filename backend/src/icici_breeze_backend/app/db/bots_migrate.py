"""SQLite migration for the Bots section (docs/bots-mvp-plan.md).

Four tables, one migration, because they are meaningless apart:

  bots            one row per (user_id, bot_type) -- the bot instance and its config
  bot_scrip_prefs Bot 1's per-scrip overrides (CE opt-out, PE opt-in, safety %)
  bot_runs        the shared cross-bot run log
  bot_proposals   Bot 1's propose -> approve -> place artefacts
  bot_approval_tokens  single-use tokens backing Telegram approve/reject taps

Why `bot_runs` is a first-class table rather than log lines
-----------------------------------------------------------
Bot 2 trades unattended. A day on which it did nothing is indistinguishable from a day on
which it was broken, unless the no-trade is recorded with a *reason the user can read*.
So every terminal outcome writes a row carrying both `reason_code` (machine-readable,
stable, testable) and `reason_text` (human). "Skipped: no broker session by 12:00" and
"Skipped: one lot exceeded the margin cap" must never collapse into the same row.

Config lives in a JSON blob rather than columns because the two bots share nothing: Bot 1
has a delivery-cash budget and per-scrip prefs, Bot 2 has per-index margin caps, a priority
and an entry time. Splitting them into typed columns would give one wide table that is
mostly NULL for whichever bot owns the row. The blob is validated by pydantic on the way in
and out (`app/domain/bots.py`), so the typing lives there instead.
"""
from __future__ import annotations

import sqlite3

# Bot type discriminators. Stable strings -- they are persisted and appear in the run log.
BOT_HOLDINGS_WRITER = "holdings_writer"
BOT_EXPIRY_INDEX_WRITER = "expiry_index_writer"
BOT_TYPES = (BOT_HOLDINGS_WRITER, BOT_EXPIRY_INDEX_WRITER)


def ensure_bots_tables(db_path: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bots (
                id TEXT PRIMARY KEY NOT NULL,
                user_id TEXT NOT NULL,
                bot_type TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 0,
                config TEXT NOT NULL DEFAULT '{}',
                created_at TIMESTAMP DEFAULT (datetime('now', '+5 hours', '+30 minutes')),
                updated_at TIMESTAMP DEFAULT (datetime('now', '+5 hours', '+30 minutes'))
            )
            """
        )
        # One instance per bot type per user. Bot 2 covers NIFTY *and* SENSEX from a single
        # instance (per-index caps plus a priority live in its config) rather than two rows,
        # so that a same-day expiry collision is arbitrated inside one config the user can
        # see whole, instead of across two rows that can drift apart.
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_bots_user_type ON bots(user_id, bot_type)"
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bot_scrip_prefs (
                user_id TEXT NOT NULL,
                stock_code TEXT NOT NULL,
                ce_enabled INTEGER NOT NULL DEFAULT 1,
                pe_enabled INTEGER NOT NULL DEFAULT 0,
                safety_pct_ce REAL,
                safety_pct_pe REAL,
                updated_at TIMESTAMP DEFAULT (datetime('now', '+5 hours', '+30 minutes')),
                PRIMARY KEY (user_id, stock_code)
            )
            """
        )
        # Defaults encode the agreed policy directly: CE default-on (the genuinely covered
        # trade), PE opt-in (assignment costs cash, not stock). A scrip with no row here
        # therefore behaves correctly without one -- rows exist only to record a deviation.
        # NULL safety_pct means "inherit the bot's global default", which is why these are
        # nullable rather than defaulted to a number.

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bot_runs (
                id TEXT PRIMARY KEY NOT NULL,
                user_id TEXT NOT NULL,
                bot_type TEXT NOT NULL,
                trigger TEXT NOT NULL,
                status TEXT NOT NULL,
                reason_code TEXT,
                reason_text TEXT,
                detail TEXT,
                started_at TIMESTAMP DEFAULT (datetime('now', '+5 hours', '+30 minutes')),
                finished_at TIMESTAMP
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_bot_runs_user_started "
            "ON bot_runs(user_id, started_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_bot_runs_user_type_started "
            "ON bot_runs(user_id, bot_type, started_at DESC)"
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bot_proposals (
                id TEXT PRIMARY KEY NOT NULL,
                run_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                bot_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                legs TEXT NOT NULL DEFAULT '[]',
                totals TEXT,
                created_at TIMESTAMP DEFAULT (datetime('now', '+5 hours', '+30 minutes')),
                expires_at TIMESTAMP,
                resolved_at TIMESTAMP,
                resolution_note TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_bot_proposals_user_status "
            "ON bot_proposals(user_id, status, created_at DESC)"
        )
        # A proposal is a *priced snapshot*, so it expires. At most one pending proposal per
        # (user, bot) at a time: a second scan supersedes the first rather than leaving the
        # user to choose between two sets of stale prices.
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_bot_proposals_one_pending "
            "ON bot_proposals(user_id, bot_type) WHERE status = 'pending'"
        )

        # Single-use approval tokens for the Telegram HITL path. The portal routes a
        # callback to this deployment by token, but only this table decides whether the
        # token is still good -- the same split as `user_telegram`'s link tokens, and for
        # the same reason: the router must never be able to authorise a trade.
        #
        # `chat_id` is stored so an approval can be checked against the chat it was sent
        # to, rather than trusting whichever chat the callback claims to come from.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bot_approval_tokens (
                token TEXT PRIMARY KEY NOT NULL,
                user_id TEXT NOT NULL,
                bot_type TEXT NOT NULL,
                proposal_id TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT (datetime('now', '+5 hours', '+30 minutes')),
                expires_at TIMESTAMP NOT NULL,
                consumed_at TIMESTAMP
            )
            """
        )
        # The claim loop asks "is anything outstanding?" on every wake, so this is the
        # index that keeps an idle deployment's wake cheap.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_bot_approval_tokens_live "
            "ON bot_approval_tokens(consumed_at, expires_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_bot_approval_tokens_proposal "
            "ON bot_approval_tokens(proposal_id)"
        )

        # Added after the MVP shipped, so they go on as ALTERs rather than into the CREATE
        # above -- an existing deployment already has these tables and never re-runs it.
        _add_column(conn, "bots", "priority", "INTEGER NOT NULL DEFAULT 1")
        _add_column(conn, "bot_scrip_prefs", "ce_lots", "INTEGER")
        _add_column(conn, "bot_scrip_prefs", "pe_lots", "INTEGER")
        _add_column(conn, "bot_scrip_prefs", "priority", "INTEGER NOT NULL DEFAULT 1")
        conn.commit()


def _add_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    """Add a column if it isn't there yet.

    SQLite has no `ADD COLUMN IF NOT EXISTS`, and this migration runs on every boot, so the
    existence check has to be explicit. `ce_lots` is deliberately nullable with no default:
    NULL means "write every lot the holding covers", which is exactly what the bot did
    before the column existed -- so a deployment upgrading into this keeps its behaviour
    without a backfill.
    """
    cols = {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
