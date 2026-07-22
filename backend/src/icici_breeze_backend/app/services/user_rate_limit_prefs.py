"""Per-user pause duration between ICICI API calls (proactive pacing + 429/503 backoff)."""

import logging
import re
import sqlite3

import icici_breeze_backend.app.core.config as cfg

logger = logging.getLogger(__name__)

_DEFAULT_PAUSE = 0.5
_MIN = 0.0
_MAX = 3.0


def ensure_icici_rate_limit_pause_column() -> None:
    with sqlite3.connect(cfg.DATA_PATH + cfg.USERS_DB) as conn:
        try:
            conn.execute(
                "ALTER TABLE user_account ADD COLUMN icici_rate_limit_pause_seconds "
                "REAL NOT NULL DEFAULT 0.5"
            )
            conn.commit()
        except sqlite3.OperationalError:
            pass


def get_icici_rate_limit_pause_seconds(user_id: str) -> float:
    ensure_icici_rate_limit_pause_column()
    with sqlite3.connect(cfg.DATA_PATH + cfg.USERS_DB) as conn:
        row = conn.execute(
            "SELECT icici_rate_limit_pause_seconds FROM user_account WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if not row or row[0] is None:
        return _DEFAULT_PAUSE
    try:
        v = float(row[0])
    except (TypeError, ValueError):
        return _DEFAULT_PAUSE
    return max(_MIN, min(_MAX, v))


def set_icici_rate_limit_pause_seconds(user_id: str, seconds: float) -> float:
    ensure_icici_rate_limit_pause_column()
    v = max(_MIN, min(_MAX, float(seconds)))
    with sqlite3.connect(cfg.DATA_PATH + cfg.USERS_DB) as conn:
        conn.execute(
            "UPDATE user_account SET icici_rate_limit_pause_seconds = ? WHERE user_id = ?",
            (v, user_id),
        )
        conn.commit()
    return v


def migrate_legacy_rate_limit_pause_default() -> None:
    """Reset legacy factory defaults (5s, 1s) to the current default (0.5s)."""
    ensure_icici_rate_limit_pause_column()
    with sqlite3.connect(cfg.DATA_PATH + cfg.USERS_DB) as conn:
        conn.execute(
            "UPDATE user_account SET icici_rate_limit_pause_seconds = ? "
            "WHERE icici_rate_limit_pause_seconds IN (5, 1, 0.5)",
            (_DEFAULT_PAUSE,),
        )
        conn.commit()


def migrate_rate_limit_pause_bounds() -> None:
    """Clamp stored pause values to the supported 0–3s range."""
    ensure_icici_rate_limit_pause_column()
    with sqlite3.connect(cfg.DATA_PATH + cfg.USERS_DB) as conn:
        conn.execute(
            "UPDATE user_account SET icici_rate_limit_pause_seconds = ? "
            "WHERE icici_rate_limit_pause_seconds < ? OR icici_rate_limit_pause_seconds > ?",
            (_DEFAULT_PAUSE, _MIN, _MAX),
        )
        conn.commit()


_LEGACY_COLUMN_DEF_RE = re.compile(
    r"icici_rate_limit_pause_seconds\s+\w+\s+NOT NULL DEFAULT\s+[\d.]+",
    re.IGNORECASE,
)


def rebuild_rate_limit_pause_column_default(db_path: str | None = None) -> bool:
    """One-time structural fix for user_account.icici_rate_limit_pause_seconds.

    The column was added via `ALTER TABLE ... ADD COLUMN ... DEFAULT 5` back when
    5s was the factory default. SQLite bakes ADD COLUMN defaults permanently into
    the column and has no ALTER COLUMN, so every later `ensure_icici_rate_limit_pause_column()`
    call just hits "duplicate column" and no-ops — the stale DEFAULT 5 never gets
    corrected. New user rows (INSERT statements that don't name this column)
    silently inherited that raw 5, which then read back clamped to 3s in Settings
    until the next restart's `migrate_legacy_rate_limit_pause_default()` reset it.

    Rebuilds user_account with the column's default corrected to 0.5s and resets
    every existing row's stored value to 0.5s. Idempotent: no-ops once the column
    already carries DEFAULT 0.5.
    """
    path = db_path or (cfg.DATA_PATH + cfg.USERS_DB)
    with sqlite3.connect(path) as conn:
        try:
            conn.execute(
                "ALTER TABLE user_account ADD COLUMN icici_rate_limit_pause_seconds "
                "REAL NOT NULL DEFAULT 0.5"
            )
            conn.commit()
        except sqlite3.OperationalError:
            pass

        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='user_account'"
        ).fetchone()
        if not row or not row[0]:
            return False
        old_sql = row[0]
        match = _LEGACY_COLUMN_DEF_RE.search(old_sql)
        if not match or "DEFAULT 0.5" in match.group(0):
            return False

        cols = [c[1] for c in conn.execute("PRAGMA table_info(user_account)").fetchall()]
        indexes = [
            r[0]
            for r in conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' "
                "AND tbl_name='user_account' AND sql IS NOT NULL"
            ).fetchall()
        ]

        new_sql = _LEGACY_COLUMN_DEF_RE.sub(
            "icici_rate_limit_pause_seconds REAL NOT NULL DEFAULT 0.5", old_sql, count=1
        )
        new_sql = re.sub(
            r'CREATE TABLE\s+"?user_account"?\s*\(',
            'CREATE TABLE "user_account_new" (',
            new_sql,
            count=1,
            flags=re.IGNORECASE,
        )

        conn.execute("BEGIN IMMEDIATE")
        conn.execute(new_sql)
        col_list = ", ".join(f'"{c}"' for c in cols)
        select_list = ", ".join(
            "0.5" if c == "icici_rate_limit_pause_seconds" else f'"{c}"' for c in cols
        )
        conn.execute(
            f"INSERT INTO user_account_new ({col_list}) "
            f"SELECT {select_list} FROM user_account"
        )
        conn.execute("DROP TABLE user_account")
        conn.execute("ALTER TABLE user_account_new RENAME TO user_account")
        for idx_sql in indexes:
            conn.execute(idx_sql)
        conn.commit()
    logger.info("Rebuilt user_account.icici_rate_limit_pause_seconds default -> 0.5s")
    return True
