"""CRUD for the Bots section in users.sqlite3 (docs/bots-mvp-plan.md).

Config blobs are validated through `app/domain/bots.py` on every read as well as every
write. Reading through the model matters as much as writing through it: a config persisted
by an older build is missing whatever fields have since been added, and validating on read
is what makes those inherit the current policy default instead of surfacing as KeyErrors
deep inside a bot that is halfway through placing orders.
"""
from __future__ import annotations

import datetime
import json
import secrets
import sqlite3
import uuid
from typing import Any, Optional

from icici_breeze_backend.app.core.timezone import ist_timestamp, now_ist
from icici_breeze_backend.app.db.bots_migrate import (
    BOT_EXPIRY_INDEX_WRITER,
    BOT_HOLDINGS_WRITER,
)
from icici_breeze_backend.app.domain.bots import (
    BotRecord,
    BotRunRecord,
    ExpiryIndexWriterConfig,
    HoldingsWriterConfig,
    ProposalLeg,
    ProposalRecord,
    ScripPref,
)

_CONFIG_MODEL = {
    BOT_HOLDINGS_WRITER: HoldingsWriterConfig,
    BOT_EXPIRY_INDEX_WRITER: ExpiryIndexWriterConfig,
}


def _db_path() -> str:
    from icici_breeze_backend.core import config as cfg

    return cfg.DATA_PATH + cfg.USERS_DB


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def _json_or(default: Any, raw: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def normalize_config(bot_type: str, raw: Any) -> dict[str, Any]:
    """Coerce a stored/incoming config through its model, filling policy defaults.

    Never raises on a stored blob: a config that fails validation falls back to defaults
    rather than bricking the bot list. Invalid *incoming* config is rejected at the route
    layer, where the user can be told why.
    """
    model = _CONFIG_MODEL.get(bot_type)
    if model is None:
        return {}
    try:
        return model(**(raw if isinstance(raw, dict) else {})).model_dump()
    except Exception:  # noqa: BLE001 -- see docstring
        return model().model_dump()


def _row_to_bot(row: sqlite3.Row) -> BotRecord:
    d = dict(row)
    return BotRecord(
        id=str(d["id"]),
        bot_type=d["bot_type"],
        enabled=bool(d["enabled"]),
        priority=int(d["priority"] or 1) if d.get("priority") is not None else 1,
        config=normalize_config(d["bot_type"], _json_or({}, d.get("config"))),
        created_at=str(d["created_at"]) if d.get("created_at") else None,
        updated_at=str(d["updated_at"]) if d.get("updated_at") else None,
    )


# --------------------------------------------------------------------------------------
# Bot instances
# --------------------------------------------------------------------------------------


def get_or_create_bot(user_id: str, bot_type: str) -> BotRecord:
    """Bots are created on first sight, disabled, with policy-default config.

    Lazy creation keeps the section self-healing: a new bot type added in a later release
    simply appears in the list, disabled, with no backfill migration needed.
    """
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM bots WHERE user_id = ? AND bot_type = ?", (user_id, bot_type)
        ).fetchone()
        if row is not None:
            return _row_to_bot(row)
        bot_id = str(uuid.uuid4())
        # Seed distinct priorities so two freshly-created bots are never tied. Bot 1 leads
        # because it is the one with a hard external constraint -- its calls are capped by
        # stock actually held, so margin it does not take is margin nothing else can use.
        default_priority = 1 if bot_type == BOT_HOLDINGS_WRITER else 2
        conn.execute(
            "INSERT INTO bots (id, user_id, bot_type, enabled, priority, config) "
            "VALUES (?, ?, ?, 0, ?, ?)",
            (
                bot_id,
                user_id,
                bot_type,
                default_priority,
                json.dumps(normalize_config(bot_type, {})),
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM bots WHERE id = ?", (bot_id,)).fetchone()
        return _row_to_bot(row)


def list_bots(user_id: str) -> list[BotRecord]:
    return [get_or_create_bot(user_id, t) for t in (BOT_HOLDINGS_WRITER, BOT_EXPIRY_INDEX_WRITER)]


def list_enabled_bots(bot_type: str) -> list[BotRecord]:
    """Every user whose bot of this type is enabled, for the scheduler to sweep."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM bots WHERE bot_type = ? AND enabled = 1", (bot_type,)
        ).fetchall()
    return [_row_to_bot(r) for r in rows]


def list_enabled_bots_by_user() -> dict[str, list[BotRecord]]:
    """Every enabled bot, grouped by owner and ordered by cross-bot priority.

    The scheduler needs this shape rather than one query per bot type: on a day both bots
    want to trade, the lower-priority number must size and place FIRST and the other must
    then size against what is actually left. That sequencing is per user, so the sweep has
    to iterate users, not bot types.
    """
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM bots WHERE enabled = 1 ORDER BY user_id, priority, bot_type"
        ).fetchall()
    out: dict[str, list[BotRecord]] = {}
    for row in rows:
        out.setdefault(str(row["user_id"]), []).append(_row_to_bot(row))
    return out


def bot_owner(bot_id: str) -> Optional[str]:
    with _connect() as conn:
        row = conn.execute("SELECT user_id FROM bots WHERE id = ?", (bot_id,)).fetchone()
    return str(row["user_id"]) if row else None


def has_terminal_run_today(user_id: str, bot_type: str) -> bool:
    """Has this bot already resolved today, either way?

    The scheduler ticks every half-minute, so without this a skipped day would re-log on
    every tick and a fired day could fire twice. `running` deliberately counts as resolved
    too — a run in flight must not be started again alongside itself.
    """
    today = now_ist().date().isoformat()
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM bot_runs WHERE user_id = ? AND bot_type = ? "
            "AND date(started_at) = ? LIMIT 1",
            (user_id, bot_type, today),
        ).fetchone()
    return row is not None


def has_committed_run_today(user_id: str, bot_type: str) -> bool:
    """Has this bot done something today that it must not do twice?

    Deliberately narrower than `has_terminal_run_today`, which treats *any* run row as
    resolving the day. That is right for the autonomous path, but a semi-autonomous bot
    writes a `proposed` run the moment it asks the user -- and asking is not acting. If
    proposing counted, the first proposal would block the re-proposal loop and the eventual
    placement, and the bot would never trade.

    `proposed` is therefore the one status excluded here. `running` still counts, so a run
    in flight is never started alongside itself.
    """
    today = now_ist().date().isoformat()
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM bot_runs WHERE user_id = ? AND bot_type = ? "
            "AND date(started_at) = ? AND status != 'proposed' LIMIT 1",
            (user_id, bot_type, today),
        ).fetchone()
    return row is not None


def update_bot(
    user_id: str,
    bot_type: str,
    *,
    enabled: Optional[bool] = None,
    priority: Optional[int] = None,
    config: Optional[dict[str, Any]] = None,
) -> BotRecord:
    current = get_or_create_bot(user_id, bot_type)
    new_enabled = current.enabled if enabled is None else bool(enabled)
    new_priority = current.priority if priority is None else max(1, int(priority))
    # Merge rather than replace: the UI edits one panel at a time, and a partial PATCH
    # must not silently reset the fields it did not send.
    merged = dict(current.config)
    if config:
        merged.update(config)
    new_config = normalize_config(bot_type, merged)
    with _connect() as conn:
        conn.execute(
            "UPDATE bots SET enabled = ?, priority = ?, config = ?, updated_at = ? "
            "WHERE user_id = ? AND bot_type = ?",
            (
                1 if new_enabled else 0,
                new_priority,
                json.dumps(new_config),
                ist_timestamp(),
                user_id,
                bot_type,
            ),
        )
        conn.commit()
    return get_or_create_bot(user_id, bot_type)


# --------------------------------------------------------------------------------------
# Bot 1 per-scrip preferences
# --------------------------------------------------------------------------------------


def list_scrip_prefs(user_id: str) -> list[ScripPref]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM bot_scrip_prefs WHERE user_id = ? ORDER BY priority, stock_code",
            (user_id,),
        ).fetchall()
    return [
        ScripPref(
            stock_code=str(r["stock_code"]),
            ce_enabled=bool(r["ce_enabled"]),
            pe_enabled=bool(r["pe_enabled"]),
            ce_lots=r["ce_lots"],
            pe_lots=r["pe_lots"],
            safety_pct_ce=r["safety_pct_ce"],
            safety_pct_pe=r["safety_pct_pe"],
            priority=int(r["priority"] or 1),
        )
        for r in rows
    ]


def upsert_scrip_prefs(user_id: str, prefs: list[ScripPref]) -> list[ScripPref]:
    with _connect() as conn:
        for p in prefs:
            conn.execute(
                """
                INSERT INTO bot_scrip_prefs
                    (user_id, stock_code, ce_enabled, pe_enabled, ce_lots, pe_lots,
                     safety_pct_ce, safety_pct_pe, priority, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, stock_code) DO UPDATE SET
                    ce_enabled = excluded.ce_enabled,
                    pe_enabled = excluded.pe_enabled,
                    ce_lots = excluded.ce_lots,
                    pe_lots = excluded.pe_lots,
                    safety_pct_ce = excluded.safety_pct_ce,
                    safety_pct_pe = excluded.safety_pct_pe,
                    priority = excluded.priority,
                    updated_at = excluded.updated_at
                """,
                (
                    user_id,
                    p.stock_code.strip().upper(),
                    1 if p.ce_enabled else 0,
                    1 if p.pe_enabled else 0,
                    p.ce_lots,
                    p.pe_lots,
                    p.safety_pct_ce,
                    p.safety_pct_pe,
                    int(p.priority or 1),
                    ist_timestamp(),
                ),
            )
        conn.commit()
    return list_scrip_prefs(user_id)


# --------------------------------------------------------------------------------------
# Run log
# --------------------------------------------------------------------------------------


def start_run(user_id: str, bot_type: str, trigger: str) -> str:
    run_id = str(uuid.uuid4())
    with _connect() as conn:
        conn.execute(
            "INSERT INTO bot_runs (id, user_id, bot_type, trigger, status) "
            "VALUES (?, ?, ?, ?, 'running')",
            (run_id, user_id, bot_type, trigger),
        )
        conn.commit()
    return run_id


def finish_run(
    run_id: str,
    *,
    status: str,
    reason_code: str,
    reason_text: str,
    detail: Optional[dict[str, Any]] = None,
) -> None:
    """Close a run. `reason_code` and `reason_text` are both required by signature, not by
    convention -- an unexplained terminal run is the exact failure this log exists to
    prevent."""
    with _connect() as conn:
        conn.execute(
            "UPDATE bot_runs SET status = ?, reason_code = ?, reason_text = ?, "
            "detail = ?, finished_at = ? WHERE id = ?",
            (
                status,
                reason_code,
                reason_text,
                json.dumps(detail) if detail else None,
                ist_timestamp(),
                run_id,
            ),
        )
        conn.commit()


def reap_stale_runs(*, older_than_minutes: int | None = None) -> int:
    """Close out runs left `running`, and say so honestly.

    A run goes `running` at start and is closed by `finish_run`. If the process dies in
    between -- a crash, an EC2 power-cycle, or the portal recreating the container for an
    upgrade -- the row stays `running` for ever. Harmless for a bot the user drives by hand,
    but Bot 2 trades unattended and its log is the only place anyone can see what it did; a
    permanently-`running` row there reads as "still working" long after the process is gone.

    Called with no age bound at startup, where the single-instance model makes it exact: one
    backend process owns this SQLite file, so any `running` row at startup is definitionally
    stale -- no other process could still be working on it. Called with an age bound from the
    scheduler, to also catch a run that hangs without the process dying (a blocked broker call
    with no timeout).

    The reason deliberately says *unknown*, not *nothing happened*: an interrupted run may
    already have placed orders before it died, so it points at the order book instead of
    implying the day was a no-op.
    """
    sql = "UPDATE bot_runs SET status = 'failed', reason_code = ?, reason_text = ?, finished_at = ? WHERE status = 'running'"
    args: list[Any] = [
        "interrupted",
        "Interrupted before it finished — the app stopped or the run stalled. Any orders it "
        "had already placed are in the Order Book.",
        ist_timestamp(),
    ]
    if older_than_minutes is not None:
        cutoff = (now_ist() - datetime.timedelta(minutes=int(older_than_minutes))).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        sql += " AND started_at < ?"
        args.append(cutoff)
    with _connect() as conn:
        cur = conn.execute(sql, args)
        conn.commit()
        return cur.rowcount or 0


def list_runs(
    user_id: str, *, bot_type: Optional[str] = None, limit: int = 50
) -> list[BotRunRecord]:
    sql = "SELECT * FROM bot_runs WHERE user_id = ?"
    args: list[Any] = [user_id]
    if bot_type:
        sql += " AND bot_type = ?"
        args.append(bot_type)
    sql += " ORDER BY started_at DESC, rowid DESC LIMIT ?"
    args.append(max(1, min(500, int(limit))))
    with _connect() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [
        BotRunRecord(
            id=str(r["id"]),
            bot_type=r["bot_type"],
            trigger=r["trigger"],
            status=r["status"],
            reason_code=r["reason_code"],
            reason_text=r["reason_text"],
            detail=_json_or(None, r["detail"]),
            started_at=str(r["started_at"]) if r["started_at"] else None,
            finished_at=str(r["finished_at"]) if r["finished_at"] else None,
        )
        for r in rows
    ]


# --------------------------------------------------------------------------------------
# Proposals
# --------------------------------------------------------------------------------------


def _row_to_proposal(row: sqlite3.Row) -> ProposalRecord:
    d = dict(row)
    legs_raw = _json_or([], d.get("legs"))
    legs: list[ProposalLeg] = []
    for item in legs_raw if isinstance(legs_raw, list) else []:
        try:
            legs.append(ProposalLeg(**item))
        except Exception:  # noqa: BLE001 -- a malformed leg must not hide the whole proposal
            continue
    return ProposalRecord(
        id=str(d["id"]),
        run_id=str(d["run_id"]),
        bot_type=d["bot_type"],
        status=d["status"],
        legs=legs,
        totals=_json_or(None, d.get("totals")),
        created_at=str(d["created_at"]) if d.get("created_at") else None,
        expires_at=str(d["expires_at"]) if d.get("expires_at") else None,
        resolved_at=str(d["resolved_at"]) if d.get("resolved_at") else None,
        resolution_note=str(d["resolution_note"]) if d.get("resolution_note") else None,
    )


def create_proposal(
    *,
    run_id: str,
    user_id: str,
    bot_type: str,
    legs: list[ProposalLeg],
    totals: Optional[dict[str, Any]] = None,
    ttl_minutes: int = 15,
) -> ProposalRecord:
    """Create a pending proposal, superseding any existing one for this bot.

    Supersede rather than reject-the-new: a fresh scan reflects fresher prices, and leaving
    the user to choose between two sets of stale numbers is worse than losing the old one.
    """
    expires = (now_ist() + datetime.timedelta(minutes=max(1, int(ttl_minutes)))).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    proposal_id = str(uuid.uuid4())
    with _connect() as conn:
        conn.execute(
            "UPDATE bot_proposals SET status = 'superseded', resolved_at = ?, "
            "resolution_note = 'Replaced by a newer scan.' "
            "WHERE user_id = ? AND bot_type = ? AND status = 'pending'",
            (ist_timestamp(), user_id, bot_type),
        )
        conn.execute(
            "INSERT INTO bot_proposals (id, run_id, user_id, bot_type, status, legs, totals, expires_at) "
            "VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)",
            (
                proposal_id,
                run_id,
                user_id,
                bot_type,
                json.dumps([leg.model_dump() for leg in legs]),
                json.dumps(totals) if totals else None,
                expires,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM bot_proposals WHERE id = ?", (proposal_id,)).fetchone()
    return _row_to_proposal(row)


def expire_stale_proposals(user_id: str) -> int:
    """Mark pending proposals past their TTL as expired. Called before any read, so the UI
    can never present stale prices as actionable."""
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE bot_proposals SET status = 'expired', resolved_at = ?, "
            "resolution_note = 'Prices went stale before approval.' "
            "WHERE user_id = ? AND status = 'pending' AND expires_at IS NOT NULL "
            "AND expires_at < ?",
            (ist_timestamp(), user_id, now_ist().strftime("%Y-%m-%d %H:%M:%S")),
        )
        conn.commit()
        return cur.rowcount or 0


def get_pending_proposal(user_id: str, bot_type: str) -> Optional[ProposalRecord]:
    expire_stale_proposals(user_id)
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM bot_proposals WHERE user_id = ? AND bot_type = ? AND status = 'pending'",
            (user_id, bot_type),
        ).fetchone()
    return _row_to_proposal(row) if row is not None else None


def get_proposal(user_id: str, proposal_id: str) -> Optional[ProposalRecord]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM bot_proposals WHERE user_id = ? AND id = ?", (user_id, proposal_id)
        ).fetchone()
    return _row_to_proposal(row) if row is not None else None


def supersede_other_pending(user_id: str, bot_type: str, keep_id: str) -> int:
    """Retire any pending proposal for this bot other than `keep_id`.

    Exists because approving is not a read-only act: `_approve_holdings` re-prices by
    running a full scan, and a scan legitimately *creates* a proposal. On the drift path
    that new proposal is the point -- it is the fresh one the user is told to look at. On
    the success path it is debris: the trade is placed, and a proposal left pending behind
    it invites the user to place it a second time.
    """
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE bot_proposals SET status = 'superseded', resolved_at = ?, "
            "resolution_note = 'Replaced by the trade you approved.' "
            "WHERE user_id = ? AND bot_type = ? AND status = 'pending' AND id != ?",
            (ist_timestamp(), user_id, bot_type, keep_id),
        )
        conn.commit()
        return cur.rowcount or 0


def resolve_proposal(
    user_id: str, proposal_id: str, *, status: str, note: Optional[str] = None
) -> Optional[ProposalRecord]:
    with _connect() as conn:
        conn.execute(
            "UPDATE bot_proposals SET status = ?, resolved_at = ?, resolution_note = ? "
            "WHERE user_id = ? AND id = ?",
            (status, ist_timestamp(), note, user_id, proposal_id),
        )
        conn.commit()
    return get_proposal(user_id, proposal_id)


def last_proposal_at(user_id: str, bot_type: str) -> Optional[datetime.datetime]:
    """When this bot last asked, in any state. Drives the re-proposal cadence.

    Reads across every status, not just `pending`: the whole point is to know how long ago
    the user was last bothered, and a proposal they let expire bothered them just as much
    as one still outstanding.
    """
    with _connect() as conn:
        row = conn.execute(
            "SELECT created_at FROM bot_proposals WHERE user_id = ? AND bot_type = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (user_id, bot_type),
        ).fetchone()
    if row is None or not row["created_at"]:
        return None
    try:
        return datetime.datetime.strptime(str(row["created_at"])[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def issue_approval_token(
    *,
    user_id: str,
    bot_type: str,
    proposal_id: str,
    chat_id: str,
    ttl_minutes: int,
) -> str:
    """Mint the single-use token behind a Telegram Approve/Reject tap.

    Superseding proposals supersede their tokens: a token for a proposal that is no longer
    pending must not survive to authorise a trade at prices the user never saw. Rather than
    leave that to a later expiry check, every earlier live token for this bot is burned
    here, so at most one tap can ever be live per bot.
    """
    token = secrets.token_urlsafe(24)
    expires = (
        now_ist() + datetime.timedelta(minutes=max(1, int(ttl_minutes)))
    ).strftime("%Y-%m-%d %H:%M:%S")
    with _connect() as conn:
        conn.execute(
            "UPDATE bot_approval_tokens SET consumed_at = ? "
            "WHERE user_id = ? AND bot_type = ? AND consumed_at IS NULL",
            (ist_timestamp(), user_id, bot_type),
        )
        conn.execute(
            "INSERT INTO bot_approval_tokens "
            "(token, user_id, bot_type, proposal_id, chat_id, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (token, user_id, bot_type, proposal_id, str(chat_id), expires),
        )
        conn.commit()
    return token


def consume_approval_token(token: str) -> Optional[dict[str, Any]]:
    """Burn `token` and return what it authorises, or None if it is no longer good.

    Single-use is enforced by the UPDATE's own WHERE clause rather than by a read followed
    by a write: two callbacks arriving together must not both find the token unconsumed.
    The row is returned only when this call is the one that burned it.
    """
    if not token:
        return None
    now = now_ist().strftime("%Y-%m-%d %H:%M:%S")
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE bot_approval_tokens SET consumed_at = ? "
            "WHERE token = ? AND consumed_at IS NULL AND expires_at >= ?",
            (ist_timestamp(), token, now),
        )
        if not cur.rowcount:
            conn.commit()
            return None
        row = conn.execute(
            "SELECT user_id, bot_type, proposal_id, chat_id FROM bot_approval_tokens "
            "WHERE token = ?",
            (token,),
        ).fetchone()
        conn.commit()
    return dict(row) if row is not None else None


def has_outstanding_approval_token() -> bool:
    """Any live token, for any user. Mirrors `has_outstanding_link_token` -- the claim loop
    uses it to decide whether there is anything a claim could usefully return."""
    now = now_ist().strftime("%Y-%m-%d %H:%M:%S")
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM bot_approval_tokens WHERE consumed_at IS NULL AND expires_at >= ? "
            "LIMIT 1",
            (now,),
        ).fetchone()
    return row is not None
