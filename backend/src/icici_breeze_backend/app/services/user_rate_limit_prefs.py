"""Per-user pause duration between ICICI API calls (proactive pacing + 429/503 backoff)."""

import sqlite3

import icici_breeze_backend.app.core.config as cfg

_DEFAULT_PAUSE = 1.0
_MIN = 0.5
_MAX = 3.0


def ensure_icici_rate_limit_pause_column() -> None:
    with sqlite3.connect(cfg.DATA_PATH + cfg.USERS_DB) as conn:
        try:
            conn.execute(
                "ALTER TABLE user_account ADD COLUMN icici_rate_limit_pause_seconds "
                "REAL NOT NULL DEFAULT 1"
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
    """Reset legacy factory default (5s) to the current default (1s)."""
    ensure_icici_rate_limit_pause_column()
    with sqlite3.connect(cfg.DATA_PATH + cfg.USERS_DB) as conn:
        conn.execute(
            "UPDATE user_account SET icici_rate_limit_pause_seconds = 1 "
            "WHERE icici_rate_limit_pause_seconds = 5"
        )
        conn.commit()


def migrate_rate_limit_pause_bounds() -> None:
    """Clamp stored pause values to the supported 0.5–3s range."""
    ensure_icici_rate_limit_pause_column()
    with sqlite3.connect(cfg.DATA_PATH + cfg.USERS_DB) as conn:
        conn.execute(
            "UPDATE user_account SET icici_rate_limit_pause_seconds = ? "
            "WHERE icici_rate_limit_pause_seconds < ? OR icici_rate_limit_pause_seconds > ?",
            (_DEFAULT_PAUSE, _MIN, _MAX),
        )
        conn.commit()
