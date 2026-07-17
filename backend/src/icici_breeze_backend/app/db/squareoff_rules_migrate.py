"""SQLite migration for Strategy Group (SG) profit/loss square-off rules.

One row == one Strategy Group instance, keyed by (user_id, stock_code, expiry_display).
The group key is *reusable over time*; a row is a specific instance with a lifecycle.
Exactly one row per key may be non-terminal at a time -- enforced by a partial unique
index rather than by convention, because the convention silently failing is precisely
what caused the original bug (arm_rule UPSERTing a second strategy onto the same row).

Statuses: armed | triggered | fired | completed | reset | disarmed.
  * `triggered` is an internal sub-second transient (guards double-fire), shown as Armed.
  * `completed`/`reset` are terminal. `reset` absorbs the old `fire_failed`.
  * `disarmed` = explicit user disarm; the SG simply returns to (derived) Active.
`Active` is derived, not stored -- an SG with open legs and no row.
"""
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
        if "legs_snapshot" not in cols:
            # JSON {scrip_key: quantity} captured at arm time. Powers composition-drift
            # detection (spec section 9) and is quantity-sensitive: the thresholds were set
            # against a specific exposure, so ANY qty change invalidates them.
            conn.execute("ALTER TABLE portfolio_squareoff_rules ADD COLUMN legs_snapshot TEXT")
        if "reset_reason" not in cols:
            # User-facing text. Spec sections 8/9/10 all require explaining why monitoring
            # stopped -- a Reset with no reason is the failure mode we're designing against.
            conn.execute("ALTER TABLE portfolio_squareoff_rules ADD COLUMN reset_reason TEXT")
        if "resolved_at" not in cols:
            conn.execute("ALTER TABLE portfolio_squareoff_rules ADD COLUMN resolved_at TIMESTAMP")

        # `fire_failed` predates the SG model: it only ever meant "placement failed", which
        # is one flavour of Reset. Fold it in so there is a single terminal-failure state.
        conn.execute(
            "UPDATE portfolio_squareoff_rules "
            "SET status = 'reset', "
            "    resolved_at = COALESCE(resolved_at, fired_at), "
            "    reset_reason = COALESCE(reset_reason, "
            "        'One or more exit orders could not be placed.') "
            "WHERE status = 'fire_failed'"
        )

        # Pre-existing data can legitimately violate the invariant we're about to enforce:
        # under the OLD model a rule could fire (status='fired', row kept for history) and
        # the user could then arm a fresh rule for the same key, leaving two non-terminal
        # rows. CREATE UNIQUE INDEX would fail outright on that. Resolve first: keep the
        # newest row per key (the user's current intent) and retire the rest. Their orders
        # aren't touched -- Reset never retracts orders already placed; they surface in the
        # Order Book and the SG's own re-arm guard covers the duplicate-fire risk.
        superseded_reason = (
            "Superseded by a newer Profit Booking / Stop Loss rule on the same scrip and "
            "expiry. Check the Order Book for any exit orders from this attempt that are "
            "still live."
        )
        conn.execute(
            """
            UPDATE portfolio_squareoff_rules SET
                status = 'reset',
                resolved_at = COALESCE(resolved_at, CURRENT_TIMESTAMP),
                reset_reason = COALESCE(reset_reason, ?)
            WHERE status IN ('armed', 'triggered', 'fired')
              AND id NOT IN (
                SELECT id FROM (
                    SELECT id, ROW_NUMBER() OVER (
                        PARTITION BY user_id, stock_code, expiry_display
                        ORDER BY COALESCE(created_at, '') DESC, id DESC
                    ) AS rn
                    FROM portfolio_squareoff_rules
                    WHERE status IN ('armed', 'triggered', 'fired')
                ) WHERE rn = 1
              )
            """,
            (superseded_reason,),
        )

        # Spec section 1 as a DB invariant, not a convention. This is what structurally
        # forecloses the original bug: two live rules can no longer collide on one key,
        # instead of relying on arm_rule remembering to check. Terminal rows (completed/
        # reset/disarmed) are excluded, so a key is freely reusable by a NEW SG later --
        # which is exactly spec section 11 ("historical SGs are never reactivated").
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_squareoff_one_active_sg "
            "ON portfolio_squareoff_rules(user_id, stock_code, expiry_display) "
            "WHERE status IN ('armed', 'triggered', 'fired')"
        )
        conn.commit()
