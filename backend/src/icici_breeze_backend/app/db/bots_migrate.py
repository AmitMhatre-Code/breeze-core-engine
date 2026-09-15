"""SQLite migration for the Bots section (docs/bots-mvp-plan.md).

Four tables, one migration, because they are meaningless apart:

  bots            one row per (user_id, bot_type) -- the bot instance and its config
  bot_scrip_prefs Bot 1's per-scrip overrides (CE opt-out, PE opt-in, safety %)
  bot_runs        the shared cross-bot run log
  bot_cycles      one row per scalper round trip, hanging off a session's bot_runs row
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
# The intraday scalpers (docs/bots-scalping-plan.md). They differ from the two above in
# shape, not just in strategy: many cycles per session rather than one decision per day, so
# they own `bot_cycles` and the `heartbeat_at` column below.
BOT_MOMENTUM_LONG_SCALPER = "momentum_long_scalper"
BOT_IRON_FLY_SCALPER = "iron_fly_scalper"
SCALPER_BOT_TYPES = (BOT_MOMENTUM_LONG_SCALPER, BOT_IRON_FLY_SCALPER)
# Expiry-day closing-auction bot (docs/bots-cas-bingo-plan.md). Holds its positions as
# `bot_cycles` rows like the scalpers, but runs its own loop and is NOT a scalper: it has no
# paper-evidence gate and none of the scalpers' session/cooldown machinery.
BOT_CAS_BINGO = "cas_bingo"
BOT_TYPES = (
    BOT_HOLDINGS_WRITER,
    BOT_EXPIRY_INDEX_WRITER,
    BOT_MOMENTUM_LONG_SCALPER,
    BOT_IRON_FLY_SCALPER,
    BOT_CAS_BINGO,
)


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
        # The Telegram message a token's buttons live on. Stored so the message can be
        # edited the moment it is answered -- buttons removed, "placing orders..." shown --
        # instead of leaving live-looking buttons that invite a second tap. NULL on rows from
        # before the column existed; those messages simply keep their buttons.
        _add_column(conn, "bot_approval_tokens", "message_id", "INTEGER")
        _add_column(conn, "bot_approval_tokens", "message_text", "TEXT")
        _add_column(conn, "bot_approval_tokens", "message_closed_at", "TIMESTAMP")

        # A position whose stop could not be armed yet because its entry orders were still
        # working. Persisted rather than held in memory because the gap it covers -- orders
        # out, stop not armed -- is exactly where a restart would otherwise leave a short
        # position silently unprotected. `terms` is the arm's inputs, frozen at placement.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bot_pending_exits (
                id TEXT PRIMARY KEY NOT NULL,
                user_id TEXT NOT NULL,
                bot_type TEXT NOT NULL,
                run_id TEXT,
                stock_code TEXT NOT NULL,
                exchange_code TEXT NOT NULL,
                expiry_display TEXT NOT NULL,
                order_ids TEXT NOT NULL,
                terms TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'waiting',
                rule_id TEXT,
                last_error TEXT,
                alerted INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT (datetime('now', '+5 hours', '+30 minutes')),
                updated_at TIMESTAMP
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_bot_pending_exits_status "
            "ON bot_pending_exits(status, created_at)"
        )

        # Renamed from `scalping_charges` when the model stopped being scalper-specific.
        # Rename rather than create-and-abandon: a deployment that had already edited its
        # rates would otherwise keep them in an orphaned table while the app read defaults
        # from a new empty one -- silently reverting a number the user had corrected.
        _rename_table_if_needed(conn, "scalping_charges", "trading_charges")

        # Round-trip cost model for EVERY bot, plus the backtest harness. A singleton row
        # (the `pnl_engine_settings` pattern) rather than per-bot config: no two bots should
        # ever disagree about what a trade costs, and the backtest must use the identical
        # numbers as paper mode or the two describe the same trade differently. Edited in
        # exactly one place -- Settings > Trading Costs.
        #
        # Editable because these are set by regulation and change without notice. The shipped
        # defaults were CALIBRATED against a real ICICI contract note (140 F&O fills,
        # 2026-08-03 to 2026-09-03) -- see `services/bots/scalping/charges.py` -- rather than
        # taken from published summaries, three of which turned out to be wrong.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trading_charges (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                brokerage_per_order_inr REAL NOT NULL DEFAULT 20.0,
                brokerage_pct_of_premium REAL NOT NULL DEFAULT 0.0,
                brokerage_cap_inr REAL,
                stt_sell_pct REAL NOT NULL DEFAULT 0.15,
                exchange_txn_pct REAL NOT NULL DEFAULT 0.03545,
                exchange_txn_pct_bse REAL NOT NULL DEFAULT 0.0325,
                sebi_pct REAL NOT NULL DEFAULT 0.0001,
                ipft_pct REAL NOT NULL DEFAULT 0.0,
                stamp_buy_pct REAL NOT NULL DEFAULT 0.003,
                gst_pct REAL NOT NULL DEFAULT 18.0,
                slippage_spread_fraction REAL NOT NULL DEFAULT 0.5,
                updated_at TIMESTAMP DEFAULT (datetime('now', '+5 hours', '+30 minutes'))
            )
            """
        )
        conn.execute("INSERT OR IGNORE INTO trading_charges (id) VALUES (1)")

        # Observed bid-ask spreads, sampled by paper mode so the backtest can model the
        # spread from what this deployment actually sees rather than from a guess. Stored as
        # (premium, spread) pairs because spread scales with premium: a median in rupees
        # alone would be meaningless across a Rs 30 option and a Rs 180 one.
        #
        # Sampling is throttled to one row per contract per minute (see
        # `record_spread_sample`), so a full session is hundreds of rows, not tens of
        # thousands, and old rows are pruned by date.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scalping_spread_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stock_code TEXT NOT NULL,
                expiry_display TEXT NOT NULL,
                strike_price REAL NOT NULL,
                right TEXT NOT NULL,
                premium REAL NOT NULL,
                spread REAL NOT NULL,
                observed_at TIMESTAMP DEFAULT (datetime('now', '+5 hours', '+30 minutes'))
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_spread_samples_observed "
            "ON scalping_spread_samples(observed_at DESC)"
        )

        # Added after the MVP shipped, so they go on as ALTERs rather than into the CREATE
        # above -- an existing deployment already has these tables and never re-runs it.
        # One row per scalper round trip. Deliberately NOT a `bot_runs` row each: a scalper
        # can cycle dozens of times a session, and `bot_runs` exists to make a single
        # no-trade day explicable -- 80 rows a day there would drown the thing it is for.
        # So a session gets one run row and its cycles hang off it.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bot_cycles (
                id TEXT PRIMARY KEY NOT NULL,
                run_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                bot_type TEXT NOT NULL,
                cycle_no INTEGER NOT NULL,
                structure TEXT NOT NULL,
                legs TEXT NOT NULL DEFAULT '[]',
                lots INTEGER,
                opened_at TIMESTAMP DEFAULT (datetime('now', '+5 hours', '+30 minutes')),
                closed_at TIMESTAMP,
                entry_value REAL,
                exit_value REAL,
                gross_pnl REAL,
                friction REAL,
                net_pnl REAL,
                exit_reason_code TEXT,
                exit_reason_text TEXT,
                detail TEXT,
                paper INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        # Reads are "this session's cycles, in order" (the run log) and "today's cycles for
        # this bot" (the cumulative stop, the consecutive-loss counter and the friction
        # total, all of which are recomputed on every cycle decision).
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_bot_cycles_run ON bot_cycles(run_id, cycle_no)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_bot_cycles_user_type_opened "
            "ON bot_cycles(user_id, bot_type, opened_at DESC)"
        )

        # Added after the MVP shipped, so they go on as ALTERs rather than into the CREATE
        # above -- an existing deployment already has these tables and never re-runs it.
        _add_column(conn, "bots", "priority", "INTEGER NOT NULL DEFAULT 1")
        # Liveness for the stale-run reaper. NULL means "never beat", and every reader uses
        # COALESCE(heartbeat_at, started_at), so Bots 1 and 2 -- which never touch it -- keep
        # exactly the reap semantics they had. Only a long-lived scalper session updates it,
        # which is what lets the reaper tell a healthy all-day session from a hung one.
        _add_column(conn, "bot_runs", "heartbeat_at", "TIMESTAMP")
        # Added when the cost model was calibrated against a real contract note: BSE and NSE
        # charge different transaction rates, and one column could only ever be right for one.
        _add_column(conn, "trading_charges", "exchange_txn_pct_bse", "REAL NOT NULL DEFAULT 0.0325")
        _correct_superseded_charge_defaults(conn)
        _correct_superseded_bot_defaults(conn)
        _separate_tied_priorities(conn)
        _add_column(conn, "bot_scrip_prefs", "ce_lots", "INTEGER")
        _add_column(conn, "bot_scrip_prefs", "pe_lots", "INTEGER")
        _add_column(conn, "bot_scrip_prefs", "priority", "INTEGER NOT NULL DEFAULT 1")
        # The paper-evidence gate (docs/bots-scalping-plan.md section 11.3). A scalper may
        # only be set `live` once a paper session has run a full trading day *on the settings
        # it will trade with*, so a run has to record which settings it was.
        #
        # `config_hash` is the fingerprint of the material config (evidence.py); `mode` is
        # paper/live at the time the session opened. Both NULL on every pre-existing row and
        # on every Bot 1/2 run, which is correct -- a run that predates the gate is not
        # evidence for it, and the readers filter on an exact hash match, so NULL never
        # satisfies anything.
        _add_column(conn, "bot_runs", "config_hash", "TEXT")
        _add_column(conn, "bot_runs", "mode", "TEXT")
        # On the cycle too, so the dialog can attribute a cycle to the settings that produced
        # it even after the user has edited them -- the run's hash alone would make every
        # cycle in a session look like it belonged to whatever the session started as.
        _add_column(conn, "bot_cycles", "config_hash", "TEXT")
        # The gate reads "completed paper sessions carrying this exact hash", which is a scan
        # of one bot's runs filtered by hash; without this it is a full table scan on every
        # card render.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_bot_runs_type_hash "
            "ON bot_runs(user_id, bot_type, config_hash)"
        )
        conn.commit()


# Values the cost model shipped with before it was calibrated against a real contract note.
# Each was simply wrong -- not a preference someone might legitimately hold -- so a row still
# carrying one is a row that has never been corrected rather than one somebody chose.
_SUPERSEDED_CHARGE_DEFAULTS = {
    "stt_sell_pct": (0.10, 0.15),
    "exchange_txn_pct": (0.0495, 0.03545),
    "ipft_pct": (0.0005, 0.0),
}


# Bot config defaults later found to be wrong, as (key path, old default, new default).
# Configs are stored as a full dump, so a changed model default never reaches a bot that
# already exists -- the same gap `_correct_superseded_charge_defaults` closes for charges.
_SUPERSEDED_BOT_DEFAULTS = {
    # 2026-09-13: the 1,00,000 ceiling bought ~13 lots, which put the flat 1,500 stop inside
    # the entry spread's noise (docs/bots-scalping-plan.md section 4.6).
    "iron_fly_scalper": (
        (("margin_ceiling_inr",), 100000.0, 25000.0),
        (("exits", "hard_stop_loss_inr"), 1500.0, None),
    ),
}


def _correct_superseded_bot_defaults(conn: sqlite3.Connection) -> None:
    """Rewrite superseded bot config defaults, and ONLY where the stored value is still the
    exact old default. Anything else is a figure the user chose, and is left alone.
    Idempotent: a corrected value no longer matches."""
    import json

    for bot_type, fixes in _SUPERSEDED_BOT_DEFAULTS.items():
        rows = conn.execute(
            "SELECT id, config FROM bots WHERE bot_type = ?", (bot_type,)
        ).fetchall()
        for row_id, raw in rows:
            try:
                config = json.loads(raw or "{}")
            except (TypeError, ValueError):
                continue
            if not isinstance(config, dict):
                continue
            changed = False
            for path, old, new in fixes:
                parent = config
                for key in path[:-1]:
                    parent = parent.get(key) if isinstance(parent, dict) else None
                if not isinstance(parent, dict):
                    continue
                current = parent.get(path[-1])
                if (
                    isinstance(current, (int, float))
                    and not isinstance(current, bool)
                    and abs(float(current) - float(old)) < 1e-9
                ):
                    parent[path[-1]] = new
                    changed = True
            if changed:
                conn.execute(
                    "UPDATE bots SET config = ? WHERE id = ?", (json.dumps(config), row_id)
                )


def _separate_tied_priorities(conn: sqlite3.Connection) -> None:
    """Give every bot of a user whose priorities collide a distinct slot.

    The `priority` column arrived as an ALTER with DEFAULT 1, so every bot that already
    existed -- Bots 1 and 2 on any deployment that ran them before 2.10.0 -- landed on 1
    together. The scheduler then broke that tie on bot_type spelling. Only users with a tie
    are touched; each is renumbered 1..n in their existing order, ties going to the
    `BOT_TYPES` order the seeded defaults follow. Idempotent: a renumbered user has no tie.
    """
    rank = {bot_type: i for i, bot_type in enumerate(BOT_TYPES)}
    tied_users = {
        row[0]
        for row in conn.execute(
            "SELECT user_id FROM bots GROUP BY user_id, priority HAVING COUNT(*) > 1"
        ).fetchall()
    }
    for user_id in tied_users:
        rows = conn.execute(
            "SELECT id, bot_type, priority FROM bots WHERE user_id = ?", (user_id,)
        ).fetchall()
        ordered = sorted(rows, key=lambda r: (r[2], rank.get(r[1], len(rank)), r[1]))
        for slot, (row_id, _bot_type, priority) in enumerate(ordered, start=1):
            if priority != slot:
                conn.execute("UPDATE bots SET priority = ? WHERE id = ?", (slot, row_id))


def _correct_superseded_charge_defaults(conn: sqlite3.Connection) -> None:
    """Replace the pre-calibration defaults, and ONLY those exact values.

    A CREATE TABLE default cannot reach a row that already exists, so a deployment that
    booted before the calibration would keep rates that were never right -- silently, and in
    a number that decides whether the strategy is viable.

    Matching on the exact superseded value is what makes this safe: anything else is a figure
    the user or a later calibration put there deliberately, and is left alone. Idempotent by
    construction, since the corrected value no longer matches.
    """
    for column, (old, new) in _SUPERSEDED_CHARGE_DEFAULTS.items():
        conn.execute(
            f"UPDATE trading_charges SET {column} = ? "
            f"WHERE id = 1 AND ABS({column} - ?) < 1e-9",
            (new, old),
        )


def _rename_table_if_needed(conn: sqlite3.Connection, old: str, new: str) -> None:
    """Rename `old` to `new`, but only when that is unambiguous.

    Does nothing if the old table is absent (a fresh install) or the new one already exists
    (already migrated, or a re-run). Idempotent, and it never merges two tables -- if both
    somehow exist, the newer one wins and the old is left in place for inspection rather
    than silently dropped.
    """
    names = {
        str(r[0])
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    }
    if old in names and new not in names:
        conn.execute(f"ALTER TABLE {old} RENAME TO {new}")


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
