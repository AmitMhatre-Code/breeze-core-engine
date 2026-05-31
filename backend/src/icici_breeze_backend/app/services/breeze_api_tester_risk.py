"""Server-side risk acknowledgment for Breeze API Playground (Settings)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import icici_breeze_backend.app.core.config as cfg

# Acceptance expires after 8 hours (browser-session-scale; re-ack on return).
_RISK_ACK_TTL_HOURS = 8


def ensure_breeze_api_tester_risk_column() -> None:
    with sqlite3.connect(cfg.DATA_PATH + cfg.USERS_DB) as conn:
        try:
            conn.execute(
                "ALTER TABLE user_account ADD COLUMN breeze_api_tester_risk_accepted_at TEXT"
            )
            conn.commit()
        except sqlite3.OperationalError:
            pass


def set_breeze_api_tester_risk_accepted(user_id: str) -> str:
    """Persist acceptance timestamp (UTC ISO). Returns the stored value."""
    ensure_breeze_api_tester_risk_column()
    ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    with sqlite3.connect(cfg.DATA_PATH + cfg.USERS_DB) as conn:
        conn.execute(
            "UPDATE user_account SET breeze_api_tester_risk_accepted_at = ? WHERE user_id = ?",
            (ts, user_id),
        )
        conn.commit()
    return ts


def get_breeze_api_tester_risk_accepted_at(user_id: str) -> str | None:
    ensure_breeze_api_tester_risk_column()
    with sqlite3.connect(cfg.DATA_PATH + cfg.USERS_DB) as conn:
        row = conn.execute(
            "SELECT breeze_api_tester_risk_accepted_at FROM user_account WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if not row or not row[0]:
        return None
    return str(row[0])


def is_breeze_api_tester_risk_accepted(user_id: str) -> bool:
    raw = get_breeze_api_tester_risk_accepted_at(user_id)
    if not raw:
        return False
    try:
        accepted = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if accepted.tzinfo is None:
            accepted = accepted.replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return datetime.now(timezone.utc) - accepted <= timedelta(hours=_RISK_ACK_TTL_HOURS)
