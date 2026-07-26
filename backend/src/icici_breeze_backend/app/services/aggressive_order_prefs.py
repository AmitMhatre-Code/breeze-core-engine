"""Per-user default for the aggressive-order form: which mode and (for tolerance mode) how far.

Seeds the order form only — the user can still override mode/tolerance on any given order. Stored
as two columns on user_account, mirroring user_rate_limit_prefs.
"""
from __future__ import annotations

import sqlite3

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.services.aggressive_limit import clamp_tolerance_pct

VALID_MODES = ("market", "limit_tolerance")
_DEFAULT_MODE = "limit_tolerance"


def _default_tolerance() -> float:
    return float(cfg.AGGRESSIVE_LIMIT_DEFAULT_TOLERANCE_PCT)


def ensure_aggressive_order_columns() -> None:
    with sqlite3.connect(cfg.DATA_PATH + cfg.USERS_DB) as conn:
        for ddl in (
            "ALTER TABLE user_account ADD COLUMN aggressive_order_mode TEXT",
            "ALTER TABLE user_account ADD COLUMN aggressive_order_tolerance_pct REAL",
        ):
            try:
                conn.execute(ddl)
            except sqlite3.OperationalError:
                pass
        conn.commit()


def _normalize_mode(raw: object) -> str:
    m = str(raw or "").strip().lower()
    return m if m in VALID_MODES else _DEFAULT_MODE


def get_aggressive_order_prefs(user_id: str) -> dict:
    ensure_aggressive_order_columns()
    with sqlite3.connect(cfg.DATA_PATH + cfg.USERS_DB) as conn:
        row = conn.execute(
            "SELECT aggressive_order_mode, aggressive_order_tolerance_pct "
            "FROM user_account WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    mode = _normalize_mode(row[0]) if row else _DEFAULT_MODE
    tol = clamp_tolerance_pct(row[1]) if (row and row[1] is not None) else _default_tolerance()
    return {"mode": mode, "tolerance_pct": tol}


def set_aggressive_order_prefs(
    user_id: str,
    *,
    mode: str | None = None,
    tolerance_pct: float | None = None,
) -> dict:
    ensure_aggressive_order_columns()
    current = get_aggressive_order_prefs(user_id)
    new_mode = _normalize_mode(mode) if mode is not None else current["mode"]
    new_tol = clamp_tolerance_pct(tolerance_pct) if tolerance_pct is not None else current["tolerance_pct"]
    with sqlite3.connect(cfg.DATA_PATH + cfg.USERS_DB) as conn:
        conn.execute(
            "UPDATE user_account SET aggressive_order_mode = ?, "
            "aggressive_order_tolerance_pct = ? WHERE user_id = ?",
            (new_mode, new_tol, user_id),
        )
        conn.commit()
    return {"mode": new_mode, "tolerance_pct": new_tol}
