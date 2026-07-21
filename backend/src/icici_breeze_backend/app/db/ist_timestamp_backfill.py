"""One-shot migration: shift stored UTC wall-clock timestamps to IST.

Every stored timestamp column in users.sqlite3 used to be written by SQLite's
``CURRENT_TIMESTAMP`` / ``datetime('now')``, both of which are **UTC**. Displays and
date comparisons are IST, so a row written at 13:07 IST read back as 07:37 — visibly
wrong in the Orders page's Placed / Resolved column, and quietly wrong anywhere a date
was sliced out of one (``fired_at[:10]``, the Profit Booking / Stop Loss history
filter). Writes now bind ``core.timezone.ist_timestamp()`` instead; this shifts the rows
written before that change so old and new rows mean the same thing.

Two safety properties, both deliberate:

* **Shape-guarded.** Only values matching ``YYYY-MM-DD HH:MM:SS`` are touched. Columns
  holding an explicit-offset ISO instant (``user_broker_session.expires_at``, which is
  ``...T18:30:00+00:00``, or ``reference_data_ingest_history.ingested_at``, already
  ``+05:30``) are already unambiguous and are skipped by the pattern even where they sit
  in a table listed below. A timestamp that already carries its zone is never wrong.
* **Version-guarded.** ``PRAGMA user_version`` gates the whole thing, so a restart —
  or a customer upgrading through several releases — cannot shift the same row twice.
  Double-shifting would put timestamps 11 hours out with no way to detect it after
  the fact.

Deliberately NOT included:

* ``idempotency_results`` — a self-consistent UTC island. Its writer and reader both use
  ``datetime.now(timezone.utc)`` and the reader explicitly re-attaches UTC to the naive
  string. Nothing displays it; it is a TTL comparison, not a wall clock. Shifting it
  would need both sides changed together and would expire in-flight keys 5½ hours early.
* ``user_messages.created_at`` — written but never read. Rows are flushed and deleted on
  retrieval, ordered by ``id``.
* ``_legacy_user_exchange_calendar_backup`` — a frozen pre-migration snapshot. Rewriting
  a backup defeats its purpose.
"""
from __future__ import annotations

import logging
import sqlite3

_logger = logging.getLogger(__name__)

#: Bumped to 1 by this migration. `user_version` was untouched (0) before it, so there is
#: no earlier numbering to reconcile with — but anything added later must use 2+.
_IST_BACKFILL_VERSION = 1

#: `table -> columns` written by CURRENT_TIMESTAMP / datetime('now') before the switch.
#: Derived by inspecting which columns actually held naive stamps, not from the schema:
#: several nullable columns (`user_credentials.rotated_at`, `exchange_calendar.*`) are
#: empty in some deployments and populated in others, so all of them are listed.
_TARGETS: dict[str, tuple[str, ...]] = {
    "user_account": ("created_at", "updated_at"),
    "user_credentials": ("created_at", "rotated_at"),
    "user_broker_session": ("created_at",),
    "user_telegram": ("created_at", "updated_at"),
    "parked_orders": ("created_at", "updated_at"),
    "portfolio_squareoff_rules": ("created_at", "fired_at", "resolved_at"),
    "exchange_calendar": ("created_at", "updated_at", "local_updated_at"),
    "api_usage_daily": ("updated_at",),
    "api_usage_daily_by_api": ("updated_at",),
    "api_usage_daily_by_route": ("updated_at",),
    "audit_log": ("timestamp",),
}

#: Matches exactly the format CURRENT_TIMESTAMP produced. An ISO instant carrying `T` or
#: an offset fails this and is left alone — see the module docstring.
_NAIVE_STAMP_GLOB = "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9] [0-9][0-9]:[0-9][0-9]:[0-9][0-9]"


def _existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """Column names of `table`, or an empty set when the table does not exist.

    Tables are created lazily by their own migrations and some only ever exist on
    deployments that used the feature, so a missing table is normal, not an error.
    """
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except sqlite3.Error:
        return set()


def backfill_ist_timestamps_if_needed(db_path: str) -> bool:
    """Shift naive UTC stamps to IST once. Returns True if this call did the work."""
    with sqlite3.connect(db_path) as conn:
        current = conn.execute("PRAGMA user_version").fetchone()[0]
        if current >= _IST_BACKFILL_VERSION:
            return False

        shifted = 0
        for table, columns in _TARGETS.items():
            present = _existing_columns(conn, table)
            if not present:
                continue
            for column in columns:
                if column not in present:
                    continue
                cur = conn.execute(
                    f"UPDATE {table} SET {column} = "
                    f"datetime({column}, '+5 hours', '+30 minutes') "
                    f"WHERE {column} GLOB ?",
                    (_NAIVE_STAMP_GLOB + "*",),
                )
                if cur.rowcount > 0:
                    shifted += cur.rowcount
                    _logger.info(
                        "IST backfill: shifted %d rows in %s.%s", cur.rowcount, table, column
                    )

        # Same transaction as the UPDATEs: if the version bump were a separate commit, a
        # crash between the two would re-run the whole shift on the next boot.
        conn.execute(f"PRAGMA user_version = {_IST_BACKFILL_VERSION}")
        conn.commit()
        _logger.info("IST timestamp backfill complete (%d values shifted).", shifted)
        return True
