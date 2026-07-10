"""CRUD for portfolio_squareoff_rules in users.sqlite3.

One row per (user_id, stock_code, expiry_display) while `status = 'armed'` —
`arm_rule` upserts in place rather than inserting a duplicate, so re-opening
the Exit Rule modal and resubmitting just updates the existing rule's
target/stop values.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any, Optional

from icici_breeze_backend.app.domain.squareoff_rule import SquareOffRuleRecord


def _db_path() -> str:
    from icici_breeze_backend.core import config as cfg

    return cfg.DATA_PATH + cfg.USERS_DB


def _row_to_record(row: sqlite3.Row) -> SquareOffRuleRecord:
    d = dict(row)
    leg_results = None
    if d.get("leg_results"):
        try:
            leg_results = json.loads(d["leg_results"])
        except (TypeError, ValueError):
            leg_results = None
    return SquareOffRuleRecord(
        id=str(d["id"]),
        stock_code=str(d["stock_code"]),
        expiry_display=str(d["expiry_display"]),
        exchange_code=str(d["exchange_code"] or "NFO"),
        profit_target_pnl=float(d["profit_target_pnl"]),
        loss_limit_pnl=float(d["loss_limit_pnl"]),
        status=d["status"],  # type: ignore[arg-type]
        leg_results=leg_results,
        created_at=str(d["created_at"]) if d.get("created_at") else None,
        fired_at=str(d["fired_at"]) if d.get("fired_at") else None,
    )


_SELECT_COLUMNS = (
    "id, user_id, stock_code, expiry_display, exchange_code, profit_target_pnl, "
    "loss_limit_pnl, status, leg_results, created_at, fired_at"
)


def list_active_rules(user_id: str) -> list[SquareOffRuleRecord]:
    """Rules still worth showing in the UI: armed (protecting a group),
    fired (order placed, but the position typically lingers in the broker's
    portfolio feed for a bit until it actually closes out), or fire_failed
    (needs manual attention). Only `disarmed` rows are excluded — kept in the
    table for audit history but not surfaced here."""
    with sqlite3.connect(_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            f"""
            SELECT {_SELECT_COLUMNS} FROM portfolio_squareoff_rules
            WHERE user_id = ? AND status IN ('armed', 'fired', 'fire_failed')
            ORDER BY created_at DESC
            """,
            (user_id,),
        )
        return [_row_to_record(r) for r in cur.fetchall()]


def list_all_armed_rules() -> list[dict[str, Any]]:
    """Every currently-armed rule across all users — used once at startup to
    hydrate the in-memory P&L engine's group-rule registry."""
    with sqlite3.connect(_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            f"""
            SELECT user_id, {_SELECT_COLUMNS} FROM portfolio_squareoff_rules
            WHERE status = 'armed'
            """
        )
        return [dict(r) for r in cur.fetchall()]


def arm_rule(
    user_id: str,
    *,
    stock_code: str,
    expiry_display: str,
    exchange_code: str,
    profit_target_pnl: float,
    loss_limit_pnl: float,
) -> SquareOffRuleRecord:
    stock_code = stock_code.strip().upper()
    expiry_display = expiry_display.strip()
    exchange_code = (exchange_code or "NFO").strip() or "NFO"
    with sqlite3.connect(_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        existing = conn.execute(
            """
            SELECT id FROM portfolio_squareoff_rules
            WHERE user_id = ? AND stock_code = ? AND expiry_display = ? AND status = 'armed'
            """,
            (user_id, stock_code, expiry_display),
        ).fetchone()
        if existing:
            rule_id = str(existing["id"])
            conn.execute(
                """
                UPDATE portfolio_squareoff_rules
                SET profit_target_pnl = ?, loss_limit_pnl = ?, exchange_code = ?
                WHERE id = ?
                """,
                (profit_target_pnl, loss_limit_pnl, exchange_code, rule_id),
            )
        else:
            rule_id = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO portfolio_squareoff_rules (
                    id, user_id, stock_code, expiry_display, exchange_code,
                    profit_target_pnl, loss_limit_pnl, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'armed')
                """,
                (
                    rule_id,
                    user_id,
                    stock_code,
                    expiry_display,
                    exchange_code,
                    profit_target_pnl,
                    loss_limit_pnl,
                ),
            )
        conn.commit()
        row = conn.execute(
            f"SELECT {_SELECT_COLUMNS} FROM portfolio_squareoff_rules WHERE id = ?",
            (rule_id,),
        ).fetchone()
        return _row_to_record(row)


def disarm_rule(user_id: str, rule_id: str) -> bool:
    """Allowed from 'armed' (user cancels protection), 'fired' (user dismisses
    the badge once they're done watching the square-off settle), or
    'fire_failed' (user dismisses after manually handling the leftover legs)."""
    with sqlite3.connect(_db_path()) as conn:
        cur = conn.execute(
            """
            UPDATE portfolio_squareoff_rules SET status = 'disarmed'
            WHERE id = ? AND user_id = ? AND status IN ('armed', 'fired', 'fire_failed')
            """,
            (rule_id, user_id),
        )
        conn.commit()
        return cur.rowcount > 0


def mark_fired(rule_id: str, leg_results: list[dict[str, Any]]) -> None:
    with sqlite3.connect(_db_path()) as conn:
        conn.execute(
            """
            UPDATE portfolio_squareoff_rules
            SET status = 'fired', fired_at = CURRENT_TIMESTAMP, leg_results = ?
            WHERE id = ?
            """,
            (json.dumps(leg_results), rule_id),
        )
        conn.commit()


def mark_fire_failed(rule_id: str, leg_results: list[dict[str, Any]]) -> None:
    with sqlite3.connect(_db_path()) as conn:
        conn.execute(
            """
            UPDATE portfolio_squareoff_rules
            SET status = 'fire_failed', fired_at = CURRENT_TIMESTAMP, leg_results = ?
            WHERE id = ?
            """,
            (json.dumps(leg_results), rule_id),
        )
        conn.commit()


def get_rule(rule_id: str) -> Optional[SquareOffRuleRecord]:
    with sqlite3.connect(_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            f"SELECT {_SELECT_COLUMNS} FROM portfolio_squareoff_rules WHERE id = ?",
            (rule_id,),
        ).fetchone()
        return _row_to_record(row) if row else None
