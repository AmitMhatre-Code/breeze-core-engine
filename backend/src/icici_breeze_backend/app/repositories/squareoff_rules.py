"""CRUD for portfolio_squareoff_rules (Strategy Groups) in users.sqlite3.

One row == one SG instance. At most one row per (user_id, stock_code, expiry_display)
may be non-terminal, enforced by a partial unique index (see squareoff_rules_migrate).

`arm_rule` still upserts an existing *armed* row in place -- that is the "edit my
thresholds" path and is correct. What it must never do again is silently absorb a
DIFFERENT strategy: under the SG model, adding legs to an armed SG changes its
composition, which Resets it with a warning (spec section 9) rather than quietly
rewriting the row. That detection lives in `strategy_group_lifecycle`.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any, Optional

from icici_breeze_backend.app.core.timezone import ist_timestamp
from icici_breeze_backend.app.domain.squareoff_rule import SquareOffRuleRecord

# Non-terminal: the SG is live and occupies its (user, stock, expiry) key.
ACTIVE_STATUSES = ("armed", "triggered", "fired")


def _db_path() -> str:
    from icici_breeze_backend.core import config as cfg

    return cfg.DATA_PATH + cfg.USERS_DB


def _json_or_none(raw: Any) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _row_to_record(row: sqlite3.Row) -> SquareOffRuleRecord:
    d = dict(row)
    snapshot = _json_or_none(d.get("legs_snapshot"))
    return SquareOffRuleRecord(
        id=str(d["id"]),
        stock_code=str(d["stock_code"]),
        expiry_display=str(d["expiry_display"]),
        exchange_code=str(d["exchange_code"] or "NFO"),
        profit_target_pnl=float(d["profit_target_pnl"]),
        loss_limit_pnl=float(d["loss_limit_pnl"]),
        target_premium_pct=int(d["target_premium_pct"]),
        stop_loss_premium_pct=int(d["stop_loss_premium_pct"]),
        target_option_price=(
            float(d["target_option_price"])
            if d.get("target_option_price") is not None
            else None
        ),
        status=d["status"],  # type: ignore[arg-type]
        leg_results=_json_or_none(d.get("leg_results")),
        created_at=str(d["created_at"]) if d.get("created_at") else None,
        fired_at=str(d["fired_at"]) if d.get("fired_at") else None,
        resolved_at=str(d["resolved_at"]) if d.get("resolved_at") else None,
        reset_reason=str(d["reset_reason"]) if d.get("reset_reason") else None,
        legs_snapshot={str(k): int(v) for k, v in snapshot.items()}
        if isinstance(snapshot, dict)
        else None,
    )


_SELECT_COLUMNS = (
    "id, user_id, stock_code, expiry_display, exchange_code, profit_target_pnl, "
    "loss_limit_pnl, target_premium_pct, stop_loss_premium_pct, status, leg_results, "
    "created_at, fired_at, resolved_at, reset_reason, legs_snapshot, target_option_price"
)


def order_ids_for_rule(rule: SquareOffRuleRecord) -> set[str]:
    """Every broker order this SG's fire produced. This set is the discriminator the
    whole lifecycle turns on: an execution whose order_id is IN here is our own exit
    working as intended (-> Completed); one that isn't is manual intervention
    (-> Reset)."""
    return {oid for leg in (rule.leg_results or []) for oid in leg.order_ids if oid}


def list_active_rules(user_id: str) -> list[SquareOffRuleRecord]:
    """SGs worth showing on Portfolio: the live ones (armed/triggered/fired) plus
    `reset`, which needs the user's attention — it may still have live exit orders that
    can fill, and in the worst case open a contra position. `completed` is history and
    `disarmed` is a deliberate dismissal, so neither surfaces here."""
    with sqlite3.connect(_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            f"""
            SELECT {_SELECT_COLUMNS} FROM portfolio_squareoff_rules
            WHERE user_id = ? AND status IN ('armed', 'triggered', 'fired', 'reset')
            ORDER BY created_at DESC
            """,
            (user_id,),
        )
        return [_row_to_record(r) for r in cur.fetchall()]


def list_all_rules_for_exit_board(user_id: str) -> list[SquareOffRuleRecord]:
    """Every non-disarmed SG for the Orders page > Profit Booking / Stop Loss table,
    including `completed` history (spec section 12 requires completed groups and their
    orders stay visible for audit)."""
    with sqlite3.connect(_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            f"""
            SELECT {_SELECT_COLUMNS} FROM portfolio_squareoff_rules
            WHERE user_id = ? AND status != 'disarmed'
            ORDER BY created_at DESC
            """,
            (user_id,),
        )
        return [_row_to_record(r) for r in cur.fetchall()]


def list_fired_rules_for_user(user_id: str) -> list[SquareOffRuleRecord]:
    """Every SG currently parked in `fired` for this user — the REST reconcile backstop's
    work list. Completion is normally driven live off the WS order feed, but a dropped
    notification would strand a fully-exited SG on `fired`; re-checking these against the
    authoritative order book on each book fetch lets that self-heal."""
    with sqlite3.connect(_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            f"""
            SELECT {_SELECT_COLUMNS} FROM portfolio_squareoff_rules
            WHERE user_id = ? AND status = 'fired'
            ORDER BY created_at DESC
            """,
            (user_id,),
        )
        return [_row_to_record(r) for r in cur.fetchall()]


def rules_owning_orders(user_id: str) -> list[SquareOffRuleRecord]:
    """SGs whose orders should be pulled OUT of the main Order Book and shown under
    their SG instead.

    Excludes `reset` deliberately: spec section 12 says a Reset SG's orders revert to
    displaying as independent individual orders. The row still keeps its `order_ids` in
    the DB -- "association removed" is a *display* rule, not a data rule, and that stored
    set is what powers the orphan warning, the bulk-cancel action and the re-arm guard.
    """
    with sqlite3.connect(_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            f"""
            SELECT {_SELECT_COLUMNS} FROM portfolio_squareoff_rules
            WHERE user_id = ? AND status IN ('armed', 'triggered', 'fired', 'completed')
            ORDER BY created_at DESC
            """,
            (user_id,),
        )
        return [_row_to_record(r) for r in cur.fetchall()]


def get_active_rule_for_group(
    user_id: str, stock_code: str, expiry_display: str
) -> Optional[SquareOffRuleRecord]:
    """The one live SG for this key, if any. The DB guarantees at most one."""
    with sqlite3.connect(_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            f"""
            SELECT {_SELECT_COLUMNS} FROM portfolio_squareoff_rules
            WHERE user_id = ? AND stock_code = ? AND expiry_display = ?
              AND status IN {ACTIVE_STATUSES}
            """,
            (user_id, stock_code.strip().upper(), expiry_display.strip()),
        ).fetchone()
        return _row_to_record(row) if row else None


def list_monitoring_rules(user_id: str) -> list[SquareOffRuleRecord]:
    """This user's SGs that still depend on a working broker session — what logout is
    about to take away.

    Same non-terminal set as `list_all_live_rules`, scoped to one user: `armed` needs the
    session to place exit orders, and `fired`/`triggered` still need the order feed to
    reach Completed. `reset` is excluded (its monitoring has already stopped, so logging
    out takes nothing further away from it)."""
    with sqlite3.connect(_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            f"""
            SELECT {_SELECT_COLUMNS} FROM portfolio_squareoff_rules
            WHERE user_id = ? AND status IN {ACTIVE_STATUSES}
            ORDER BY created_at DESC
            """,
            (user_id,),
        )
        return [_row_to_record(r) for r in cur.fetchall()]


def list_all_live_rules() -> list[dict[str, Any]]:
    """Every live SG across all users — for startup hydration of the in-memory engine
    and for re-pinning WS subscriptions so PB/SL keeps working headless."""
    with sqlite3.connect(_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            f"SELECT user_id, {_SELECT_COLUMNS} FROM portfolio_squareoff_rules "
            f"WHERE status IN {ACTIVE_STATUSES}"
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
    target_premium_pct: int,
    stop_loss_premium_pct: int,
    legs_snapshot: dict[str, int] | None = None,
    target_option_price: float | None = None,
) -> SquareOffRuleRecord:
    """Arm an SG, or edit an already-`armed` one's thresholds in place.

    The in-place update only ever applies to a still-`armed` row, i.e. "change my
    numbers". It cannot absorb a different strategy the way the old code did: a
    composition change Resets the SG first (spec section 9), so by the time this runs the
    old row is terminal and this inserts a NEW SG.

    `legs_snapshot` is re-captured on every arm so the drift baseline always matches the
    thresholds the user just confirmed.
    """
    stock_code = stock_code.strip().upper()
    expiry_display = expiry_display.strip()
    exchange_code = (exchange_code or "NFO").strip() or "NFO"
    snapshot_json = json.dumps(legs_snapshot) if legs_snapshot else None
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
                SET profit_target_pnl = ?, loss_limit_pnl = ?, exchange_code = ?,
                    target_premium_pct = ?, stop_loss_premium_pct = ?,
                    target_option_price = ?,
                    legs_snapshot = COALESCE(?, legs_snapshot)
                WHERE id = ?
                """,
                (
                    profit_target_pnl,
                    loss_limit_pnl,
                    exchange_code,
                    target_premium_pct,
                    stop_loss_premium_pct,
                    # Set unconditionally, not COALESCEd: an edit that omits it means "no
                    # price target", and silently keeping a stale one would leave the SG
                    # booking at a price the caller no longer intends.
                    target_option_price,
                    snapshot_json,
                    rule_id,
                ),
            )
        else:
            rule_id = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO portfolio_squareoff_rules (
                    id, user_id, stock_code, expiry_display, exchange_code,
                    profit_target_pnl, loss_limit_pnl, target_premium_pct,
                    stop_loss_premium_pct, status, legs_snapshot, created_at,
                    target_option_price
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'armed', ?, ?, ?)
                """,
                (
                    rule_id,
                    user_id,
                    stock_code,
                    expiry_display,
                    exchange_code,
                    profit_target_pnl,
                    loss_limit_pnl,
                    target_premium_pct,
                    stop_loss_premium_pct,
                    snapshot_json,
                    # Bound rather than left to the column DEFAULT: databases created
                    # before the IST switch still carry `DEFAULT CURRENT_TIMESTAMP`
                    # (UTC), and SQLite has no ALTER COLUMN to correct it in place.
                    ist_timestamp(),
                    target_option_price,
                ),
            )
        conn.commit()
        row = conn.execute(
            f"SELECT {_SELECT_COLUMNS} FROM portfolio_squareoff_rules WHERE id = ?",
            (rule_id,),
        ).fetchone()
        return _row_to_record(row)


def mark_completed(rule_id: str) -> bool:
    """Fired -> Completed. Only from `fired`, and only once every exit order executed
    and no legs remain — the caller owns that check."""
    with sqlite3.connect(_db_path()) as conn:
        cur = conn.execute(
            "UPDATE portfolio_squareoff_rules "
            "SET status = 'completed', resolved_at = ? "
            "WHERE id = ? AND status = 'fired'",
            (ist_timestamp(), rule_id),
        )
        conn.commit()
        return cur.rowcount > 0


def mark_reset(rule_id: str, reason: str) -> bool:
    """Retire a live SG with a user-facing explanation.

    Does NOT cancel the SG's outstanding exit orders — Reset withdraws future
    automation, it does not retract actions already taken. Auto-cancelling would, in the
    stop-loss case, kill working exits and leave the user unprotected in a moving market
    because some unrelated leg failed. The orphans surface in the Order Book and via the
    explicit bulk-cancel action instead.
    """
    with sqlite3.connect(_db_path()) as conn:
        cur = conn.execute(
            f"UPDATE portfolio_squareoff_rules "
            f"SET status = 'reset', resolved_at = ?, reset_reason = ? "
            f"WHERE id = ? AND status IN {ACTIVE_STATUSES}",
            (ist_timestamp(), reason, rule_id),
        )
        conn.commit()
        return cur.rowcount > 0


def disarm_rule(user_id: str, rule_id: str) -> bool:
    """Allowed from 'armed' (user cancels protection), 'fired' (dismiss once they're done
    watching the square-off settle), or 'reset' (dismiss after handling the fallout).

    Callers MUST NOT allow a `reset` dismissal while that SG still has live orphaned exit
    orders — see `strategy_group_lifecycle.rearm_blocked`. Dismissing would erase the
    hazard from the UI while the orders keep working, and the UI must not be able to lie
    about live risk. The route enforces that; this stays a dumb write.
    """
    with sqlite3.connect(_db_path()) as conn:
        cur = conn.execute(
            """
            UPDATE portfolio_squareoff_rules SET status = 'disarmed'
            WHERE id = ? AND user_id = ? AND status IN ('armed', 'fired', 'reset')
            """,
            (rule_id, user_id),
        )
        conn.commit()
        return cur.rowcount > 0


def mark_triggered(rule_id: str) -> None:
    """Transient marker: this poll cycle detected a breach and is dispatching close
    orders now. Defensive WHERE so a rule that's already moved on (e.g. re-fired by a
    duplicate event) isn't bounced back to 'triggered'."""
    with sqlite3.connect(_db_path()) as conn:
        conn.execute(
            "UPDATE portfolio_squareoff_rules SET status = 'triggered' WHERE id = ? AND status = 'armed'",
            (rule_id,),
        )
        conn.commit()


def mark_fired(rule_id: str, leg_results: list[dict[str, Any]]) -> None:
    with sqlite3.connect(_db_path()) as conn:
        conn.execute(
            """
            UPDATE portfolio_squareoff_rules
            SET status = 'fired', fired_at = ?, leg_results = ?
            WHERE id = ?
            """,
            (ist_timestamp(), json.dumps(leg_results), rule_id),
        )
        conn.commit()


def mark_fire_failed(rule_id: str, leg_results: list[dict[str, Any]], reason: str) -> None:
    """A placement failure is one flavour of Reset — the SG is no longer monitoring and
    the user must re-arm. `leg_results` is still stored: any legs that DID get an order
    placed before the failure are live orphans, and that stored set is what the orphan
    warning and bulk-cancel act on."""
    fired = ist_timestamp()
    with sqlite3.connect(_db_path()) as conn:
        conn.execute(
            """
            UPDATE portfolio_squareoff_rules
            SET status = 'reset', fired_at = ?,
                resolved_at = ?, leg_results = ?, reset_reason = ?
            WHERE id = ?
            """,
            (fired, fired, json.dumps(leg_results), reason, rule_id),
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


def update_leg_order_ids(rule_id: str, scrip_key: str, order_ids: list[str]) -> bool:
    """Patch one leg's `order_ids` within a fired rule's stored leg_results, after a
    leg-modify has cancelled/modified/added orders for it — so the next read (PB/SL
    table, future modify/cancel matching) resolves the currently-live set."""
    rule = get_rule(rule_id)
    if rule is None or not rule.leg_results:
        return False
    updated = False
    new_legs = []
    for leg in rule.leg_results:
        if leg.scrip_key == scrip_key:
            leg = leg.model_copy(update={"order_ids": order_ids, "order_id": order_ids[0] if order_ids else None})
            updated = True
        new_legs.append(leg)
    if not updated:
        return False
    with sqlite3.connect(_db_path()) as conn:
        conn.execute(
            "UPDATE portfolio_squareoff_rules SET leg_results = ? WHERE id = ?",
            (json.dumps([leg.model_dump() for leg in new_legs]), rule_id),
        )
        conn.commit()
    return True
