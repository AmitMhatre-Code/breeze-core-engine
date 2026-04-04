"""Per-user pause duration when ICICI returns HTTP 429 during order flows."""

import sqlite3

import icici_breeze_backend.app.core.config as cfg

_DEFAULT_PAUSE = 20
_MIN = 5
_MAX = 300


def ensure_icici_rate_limit_pause_column() -> None:
    with sqlite3.connect(cfg.DATA_PATH + cfg.USERS_DB) as conn:
        try:
            conn.execute(
                "ALTER TABLE user_account ADD COLUMN icici_rate_limit_pause_seconds "
                "INTEGER NOT NULL DEFAULT 20"
            )
            conn.commit()
        except sqlite3.OperationalError:
            pass


def get_icici_rate_limit_pause_seconds(user_id: str) -> int:
    ensure_icici_rate_limit_pause_column()
    with sqlite3.connect(cfg.DATA_PATH + cfg.USERS_DB) as conn:
        row = conn.execute(
            "SELECT icici_rate_limit_pause_seconds FROM user_account WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if not row or row[0] is None:
        return _DEFAULT_PAUSE
    try:
        v = int(row[0])
    except (TypeError, ValueError):
        return _DEFAULT_PAUSE
    return max(_MIN, min(_MAX, v))


def set_icici_rate_limit_pause_seconds(user_id: str, seconds: int) -> int:
    ensure_icici_rate_limit_pause_column()
    v = max(_MIN, min(_MAX, int(seconds)))
    with sqlite3.connect(cfg.DATA_PATH + cfg.USERS_DB) as conn:
        conn.execute(
            "UPDATE user_account SET icici_rate_limit_pause_seconds = ? WHERE user_id = ?",
            (v, user_id),
        )
        conn.commit()
    return v
