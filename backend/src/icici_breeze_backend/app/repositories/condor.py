"""Storage for Dynamic Iron Condor campaigns (`db/condor_migrate.py`).

Settings are validated through `CondorSettings` on every read, as the bots' configs are, so a
row written by an older build picks up fields added since rather than failing deep inside a
check.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from icici_breeze_backend.app.core.timezone import ist_timestamp
from icici_breeze_backend.app.domain.condor import CondorSettings

ACTIVE = "active"
CLOSED = "closed"


def _db_path() -> str:
    from icici_breeze_backend.core import config as cfg

    return cfg.DATA_PATH + cfg.USERS_DB


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@dataclass(frozen=True)
class Cycle:
    id: int
    campaign_id: str
    expiry: str
    opened_at: str
    closed_at: Optional[str]
    close_reason: Optional[str]
    tranches_entered: int


@dataclass(frozen=True)
class Fill:
    id: int
    campaign_id: str
    cycle_id: Optional[int]
    order_id: Optional[str]
    expiry: str
    strike: float
    right: str
    side: str
    quantity: int
    price: float
    charges: float
    kind: str
    filled_at: str
    note: Optional[str]

    @property
    def cash(self) -> float:
        """What this fill did to the ledger: proceeds of a sell, cost of a buy, less charges."""
        gross = self.price * self.quantity
        return (gross if self.side == "Sell" else -gross) - self.charges


@dataclass(frozen=True)
class Campaign:
    id: str
    user_id: str
    underlying: str
    exchange_code: str
    origin: str
    mode: str
    status: str
    settings: CondorSettings
    created_at: str
    closed_at: Optional[str]
    close_reason: Optional[str]
    note: Optional[str]
    cycles: tuple[Cycle, ...] = field(default_factory=tuple)

    @property
    def cycle(self) -> Optional[Cycle]:
        """The open cycle, if any."""
        for c in reversed(self.cycles):
            if c.closed_at is None:
                return c
        return None


def _cycle(row: sqlite3.Row) -> Cycle:
    return Cycle(
        id=int(row["id"]), campaign_id=row["campaign_id"], expiry=row["expiry"],
        opened_at=row["opened_at"], closed_at=row["closed_at"], close_reason=row["close_reason"],
        tranches_entered=int(row["tranches_entered"] or 0),
    )


def _campaign(conn: sqlite3.Connection, row: sqlite3.Row) -> Campaign:
    cycles = tuple(
        _cycle(r) for r in conn.execute(
            "SELECT * FROM condor_cycles WHERE campaign_id = ? ORDER BY id", (row["id"],)
        )
    )
    return Campaign(
        id=row["id"], user_id=row["user_id"], underlying=row["underlying"],
        exchange_code=row["exchange_code"], origin=row["origin"], mode=row["mode"],
        status=row["status"], settings=CondorSettings(**json.loads(row["settings"])),
        created_at=row["created_at"], closed_at=row["closed_at"],
        close_reason=row["close_reason"], note=row["note"], cycles=cycles,
    )


class GroupTaken(ValueError):
    """Another active campaign already owns this underlying + expiry."""


def active_owner(user_id: str, underlying: str, expiry: str) -> Optional[str]:
    """The active campaign whose open cycle is on this expiry, if any.

    Called from the PB/SL arm guard, so a database without the condor tables (a test fixture,
    or the moments before startup migrates) reads as "no campaign", never as a failed arm."""
    try:
        with _connect() as conn:
            # Paper campaigns hold nothing at the broker, so they own no group.
            row = conn.execute(
                "SELECT c.id FROM condor_campaigns c JOIN condor_cycles y ON y.campaign_id = c.id "
                "WHERE c.user_id = ? AND c.underlying = ? AND c.status = ? AND c.mode = 'live' "
                "AND y.closed_at IS NULL AND lower(y.expiry) = lower(?) LIMIT 1",
                (user_id, underlying, ACTIVE, expiry),
            ).fetchone()
    except sqlite3.OperationalError:
        return None
    return row["id"] if row else None


def create_campaign(
    user_id: str,
    settings: CondorSettings,
    *,
    expiry: str,
    origin: str = "manual",
    mode: str = "live",
    underlying: str = "NIFTY",
    exchange_code: str = "NFO",
    note: Optional[str] = None,
) -> Campaign:
    with _connect() as conn:
        taken = None
        if mode == "live":
            taken = conn.execute(
                "SELECT c.id FROM condor_campaigns c JOIN condor_cycles y ON y.campaign_id = c.id "
                "WHERE c.user_id = ? AND c.underlying = ? AND c.status = ? AND c.mode = 'live' "
                "AND y.closed_at IS NULL AND lower(y.expiry) = lower(?)",
                (user_id, underlying, ACTIVE, expiry),
            ).fetchone()
        if taken:
            raise GroupTaken(f"A campaign already manages {underlying} {expiry}.")
        cid = str(uuid.uuid4())
        now = ist_timestamp()
        conn.execute(
            "INSERT INTO condor_campaigns (id, user_id, underlying, exchange_code, origin, mode, "
            "status, settings, created_at, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (cid, user_id, underlying, exchange_code, origin, mode, ACTIVE,
             json.dumps(settings.model_dump(mode="json")), now, note),
        )
        conn.execute(
            "INSERT INTO condor_cycles (campaign_id, expiry, opened_at) VALUES (?, ?, ?)",
            (cid, expiry, now),
        )
        conn.commit()
        return _campaign(conn, conn.execute("SELECT * FROM condor_campaigns WHERE id = ?", (cid,)).fetchone())


def get_campaign(campaign_id: str, user_id: str) -> Optional[Campaign]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM condor_campaigns WHERE id = ? AND user_id = ?", (campaign_id, user_id)
        ).fetchone()
        return _campaign(conn, row) if row else None


def list_campaigns(user_id: str, *, include_closed: bool = False, limit: int = 50) -> list[Campaign]:
    sql = "SELECT * FROM condor_campaigns WHERE user_id = ?"
    args: list[Any] = [user_id]
    if not include_closed:
        sql += " AND status = ?"
        args.append(ACTIVE)
    sql += " ORDER BY created_at DESC LIMIT ?"
    args.append(int(limit))
    with _connect() as conn:
        return [_campaign(conn, r) for r in conn.execute(sql, args).fetchall()]


def list_active_all() -> list[Campaign]:
    """Every user's active campaigns -- what the scheduled checks run over."""
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM condor_campaigns WHERE status = ?", (ACTIVE,)).fetchall()
        return [_campaign(conn, r) for r in rows]


def update_settings(campaign_id: str, user_id: str, settings: CondorSettings) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE condor_campaigns SET settings = ? WHERE id = ? AND user_id = ?",
            (json.dumps(settings.model_dump(mode="json")), campaign_id, user_id),
        )
        conn.commit()
        return cur.rowcount > 0


def close_campaign(campaign_id: str, user_id: str, reason: str) -> bool:
    now = ist_timestamp()
    with _connect() as conn:
        conn.execute(
            "UPDATE condor_cycles SET closed_at = ?, close_reason = COALESCE(close_reason, ?) "
            "WHERE campaign_id = ? AND closed_at IS NULL",
            (now, reason, campaign_id),
        )
        cur = conn.execute(
            "UPDATE condor_campaigns SET status = ?, closed_at = ?, close_reason = ? "
            "WHERE id = ? AND user_id = ? AND status = ?",
            (CLOSED, now, reason, campaign_id, user_id, ACTIVE),
        )
        conn.commit()
        return cur.rowcount > 0


def delete_unopened(campaign_id: str, user_id: str) -> bool:
    """Remove a campaign that never got going: no fill, no execution. Used when the ticket that
    was to open it is refused before any order goes out, so the refusal leaves nothing behind."""
    with _connect() as conn:
        busy = conn.execute(
            "SELECT 1 FROM condor_fills WHERE campaign_id = ? UNION ALL "
            "SELECT 1 FROM condor_executions WHERE campaign_id = ? LIMIT 1",
            (campaign_id, campaign_id),
        ).fetchone()
        if busy:
            return False
        conn.execute("DELETE FROM condor_decisions WHERE campaign_id = ?", (campaign_id,))
        conn.execute("DELETE FROM condor_cycles WHERE campaign_id = ?", (campaign_id,))
        cur = conn.execute("DELETE FROM condor_campaigns WHERE id = ? AND user_id = ?", (campaign_id, user_id))
        conn.commit()
        return cur.rowcount > 0


def add_fills(campaign_id: str, cycle_id: Optional[int], fills: Iterable[dict[str, Any]]) -> int:
    """Book fills. A fill whose order id is already booked is skipped, never doubled."""
    added = 0
    now = ist_timestamp()
    with _connect() as conn:
        for f in fills:
            cur = conn.execute(
                "INSERT OR IGNORE INTO condor_fills (campaign_id, cycle_id, order_id, tag, expiry, "
                "strike, right, side, quantity, price, charges, kind, filled_at, note) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    campaign_id, cycle_id, f.get("order_id"), f.get("tag"), f["expiry"],
                    float(f["strike"]), f["right"], f["side"], int(f["quantity"]),
                    float(f["price"]), float(f.get("charges") or 0.0), f["kind"],
                    f.get("filled_at") or now, f.get("note"),
                ),
            )
            added += cur.rowcount or 0
        conn.commit()
    return added


def list_fills(campaign_id: str) -> list[Fill]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM condor_fills WHERE campaign_id = ? ORDER BY filled_at, id", (campaign_id,)
        ).fetchall()
    return [
        Fill(
            id=int(r["id"]), campaign_id=r["campaign_id"], cycle_id=r["cycle_id"],
            order_id=r["order_id"], expiry=r["expiry"], strike=float(r["strike"]),
            right=r["right"], side=r["side"], quantity=int(r["quantity"]), price=float(r["price"]),
            charges=float(r["charges"] or 0), kind=r["kind"], filled_at=r["filled_at"], note=r["note"],
        )
        for r in rows
    ]


def add_decision(
    campaign_id: str,
    *,
    check_kind: str,
    action: str,
    reason: str,
    text: str,
    snapshot: Optional[dict[str, Any]] = None,
    outcome: Optional[str] = None,
    note: Optional[str] = None,
    at: Optional[str] = None,
) -> int:
    """`at` is the check's own time when a scheduler records it, so "already checked today"
    is judged on the clock that ran the check."""
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO condor_decisions (campaign_id, at, check_kind, action, reason, text, "
            "snapshot, outcome, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (campaign_id, at or ist_timestamp(), check_kind, action, reason, text,
             json.dumps(snapshot, default=str) if snapshot is not None else None, outcome, note),
        )
        conn.commit()
        return int(cur.lastrowid)


def list_decisions(campaign_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM condor_decisions WHERE campaign_id = ? ORDER BY at DESC, id DESC LIMIT ?",
            (campaign_id, int(limit)),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["snapshot"] = json.loads(d["snapshot"]) if d.get("snapshot") else None
        out.append(d)
    return out


def last_scheduled_decision(campaign_id: str) -> Optional[dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM condor_decisions WHERE campaign_id = ? AND check_kind IN ('sod', 'eod') "
            "ORDER BY at DESC, id DESC LIMIT 1",
            (campaign_id,),
        ).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["snapshot"] = json.loads(d["snapshot"]) if d.get("snapshot") else None
    return d


def leave_out(campaign_id: str, expiry: str, strike: float, right: str, units: int) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO condor_left_out (campaign_id, expiry, strike, right, units, at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (campaign_id, expiry, float(strike), right, int(units), ist_timestamp()),
        )
        conn.commit()


def left_out(campaign_id: str, expiry: str) -> dict[tuple[float, str], int]:
    """(strike, right) -> the units left outside the ledger, for one expiry."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT strike, right, units FROM condor_left_out WHERE campaign_id = ? "
            "AND lower(expiry) = lower(?)",
            (campaign_id, expiry),
        ).fetchall()
    return {(float(r["strike"]), r["right"]): int(r["units"]) for r in rows}


def clear_left_out(campaign_id: str, expiry: str, strike: float, right: str) -> None:
    with _connect() as conn:
        conn.execute(
            "DELETE FROM condor_left_out WHERE campaign_id = ? AND lower(expiry) = lower(?) "
            "AND strike = ? AND right = ?",
            (campaign_id, expiry, float(strike), right),
        )
        conn.commit()


def open_cycle(campaign_id: str, expiry: str) -> int:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO condor_cycles (campaign_id, expiry, opened_at) VALUES (?, ?, ?)",
            (campaign_id, expiry, ist_timestamp()),
        )
        conn.commit()
        return int(cur.lastrowid)


def close_cycle(cycle_id: int, reason: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE condor_cycles SET closed_at = ?, close_reason = ? WHERE id = ? AND closed_at IS NULL",
            (ist_timestamp(), reason, cycle_id),
        )
        conn.commit()


def bump_tranches(cycle_id: int) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE condor_cycles SET tranches_entered = tranches_entered + 1 WHERE id = ?", (cycle_id,)
        )
        conn.commit()


# --------------------------------------------------------------------------------------
# Executions
# --------------------------------------------------------------------------------------


def create_execution(campaign_id: str, kind: str, steps: list[dict[str, Any]], *, note: Optional[str] = None,
                     warnings: Optional[list[str]] = None) -> str:
    eid = str(uuid.uuid4())
    with _connect() as conn:
        conn.execute(
            "INSERT INTO condor_executions (id, campaign_id, kind, status, created_at, steps, note, warnings) "
            "VALUES (?, ?, ?, 'running', ?, ?, ?, ?)",
            (eid, campaign_id, kind, ist_timestamp(), json.dumps(steps, default=str), note,
             json.dumps(warnings or [])),
        )
        conn.commit()
    return eid


def update_execution(eid: str, *, steps: Optional[list[dict[str, Any]]] = None, status: Optional[str] = None,
                     message: Optional[str] = None) -> None:
    sets, args = [], []
    if steps is not None:
        sets.append("steps = ?")
        args.append(json.dumps(steps, default=str))
    if status is not None:
        sets.append("status = ?")
        args.append(status)
        if status != "running":
            sets.append("finished_at = ?")
            args.append(ist_timestamp())
    if message is not None:
        sets.append("message = ?")
        args.append(message)
    if not sets:
        return
    with _connect() as conn:
        conn.execute(f"UPDATE condor_executions SET {', '.join(sets)} WHERE id = ?", (*args, eid))
        conn.commit()


def _execution(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["steps"] = json.loads(d.get("steps") or "[]")
    d["warnings"] = json.loads(d.get("warnings") or "[]")
    return d


def get_execution(eid: str, campaign_id: str) -> Optional[dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM condor_executions WHERE id = ? AND campaign_id = ?", (eid, campaign_id)
        ).fetchone()
    return _execution(row) if row else None


def list_executions(campaign_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM condor_executions WHERE campaign_id = ? ORDER BY created_at DESC LIMIT ?",
            (campaign_id, int(limit)),
        ).fetchall()
    return [_execution(r) for r in rows]


def running_execution(campaign_id: str) -> Optional[dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM condor_executions WHERE campaign_id = ? AND status = 'running' LIMIT 1",
            (campaign_id,),
        ).fetchone()
    return _execution(row) if row else None


def fail_interrupted_executions() -> int:
    """At startup: a row still `running` was cut off by a restart. Marked, never resumed --
    what it reached is in its steps, and the ledger/broker comparison shows the rest."""
    try:
        with _connect() as conn:
            cur = conn.execute(
                "UPDATE condor_executions SET status = 'interrupted', finished_at = ?, "
                "message = 'The app restarted while this was running. Check the card for any difference to assign.' "
                "WHERE status = 'running'",
                (ist_timestamp(),),
            )
            conn.commit()
            return cur.rowcount or 0
    except sqlite3.OperationalError:
        return 0


# --------------------------------------------------------------------------------------
# The bot's campaign and its approvals
# --------------------------------------------------------------------------------------


def bot_campaign(user_id: str) -> Optional[Campaign]:
    """The bot's active campaign, paper or live. The bot runs at most one."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM condor_campaigns WHERE user_id = ? AND origin = 'bot' AND status = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (user_id, ACTIVE),
        ).fetchone()
        return _campaign(conn, row) if row else None


def bot_campaigns(user_id: str, *, mode: Optional[str] = None) -> list[Campaign]:
    sql = "SELECT * FROM condor_campaigns WHERE user_id = ? AND origin = 'bot'"
    args: list[Any] = [user_id]
    if mode:
        sql += " AND mode = ?"
        args.append(mode)
    with _connect() as conn:
        return [_campaign(conn, r) for r in conn.execute(sql + " ORDER BY created_at", args).fetchall()]


def hand_to_bot(campaign_id: str, user_id: str, settings: CondorSettings, note: str) -> bool:
    """A manual live campaign becomes the bot's: the bot's settings, its fingerprint as the note
    (which is what its evidence is matched on, #66), origin `bot`. One write, so the scheduler
    never sees a bot campaign on the old settings."""
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE condor_campaigns SET origin = 'bot', settings = ?, note = ? "
            "WHERE id = ? AND user_id = ? AND origin = 'manual' AND mode = 'live' AND status = ?",
            (json.dumps(settings.model_dump(mode="json")), note, campaign_id, user_id, ACTIVE),
        )
        conn.commit()
        return cur.rowcount > 0


def set_origin(campaign_id: str, origin: str) -> None:
    with _connect() as conn:
        conn.execute("UPDATE condor_campaigns SET origin = ? WHERE id = ?", (origin, campaign_id))
        conn.commit()


def get_decision(decision_id: int) -> Optional[dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM condor_decisions WHERE id = ?", (int(decision_id),)).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["snapshot"] = json.loads(d["snapshot"]) if d.get("snapshot") else None
    return d


def update_decision(decision_id: int, *, outcome: Optional[str] = None, snapshot: Optional[dict[str, Any]] = None) -> None:
    sets, args = [], []
    if outcome is not None:
        sets.append("outcome = ?")
        args.append(outcome)
    if snapshot is not None:
        sets.append("snapshot = ?")
        args.append(json.dumps(snapshot, default=str))
    if not sets:
        return
    with _connect() as conn:
        conn.execute(f"UPDATE condor_decisions SET {', '.join(sets)} WHERE id = ?", (*args, int(decision_id)))
        conn.commit()


def count_executions(campaign_ids: list[str], *, note: str, status: str = "completed") -> int:
    if not campaign_ids:
        return 0
    marks = ",".join("?" for _ in campaign_ids)
    with _connect() as conn:
        row = conn.execute(
            f"SELECT COUNT(*) FROM condor_executions WHERE campaign_id IN ({marks}) AND note = ? AND status = ?",
            (*campaign_ids, note, status),
        ).fetchone()
    return int(row[0] or 0)


def campaigns_with_note(user_id: str, note: str) -> list[Campaign]:
    """Every campaign, any status or origin, carrying this note -- the bot's fingerprint."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM condor_campaigns WHERE user_id = ? AND note = ? ORDER BY created_at", (user_id, note)
        ).fetchall()
        return [_campaign(conn, r) for r in rows]
