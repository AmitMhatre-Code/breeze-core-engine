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
from collections.abc import Iterable
from typing import Any, Optional

from icici_breeze_backend.app.core.timezone import ist_timestamp, now_ist
from icici_breeze_backend.app.db.bots_migrate import (
    BOT_CAS_BINGO,
    BOT_EXPIRY_INDEX_WRITER,
    BOT_HOLDINGS_WRITER,
    BOT_IRON_FLY_SCALPER,
    BOT_MOMENTUM_LONG_SCALPER,
)
from icici_breeze_backend.app.domain.bots import (
    BotCycleRecord,
    BotRecord,
    BotRunRecord,
    CasBingoConfig,
    ExpiryIndexWriterConfig,
    HoldingsWriterConfig,
    IronFlyScalperConfig,
    MomentumLongScalperConfig,
    ProposalLeg,
    ProposalRecord,
    ScalperDayTotals,
    ScripPref,
)

_CONFIG_MODEL = {
    BOT_HOLDINGS_WRITER: HoldingsWriterConfig,
    BOT_EXPIRY_INDEX_WRITER: ExpiryIndexWriterConfig,
    BOT_MOMENTUM_LONG_SCALPER: MomentumLongScalperConfig,
    BOT_IRON_FLY_SCALPER: IronFlyScalperConfig,
    BOT_CAS_BINGO: CasBingoConfig,
}

# Cross-bot ordering seeded so no two bots are ever tied on creation. Bot 1 leads because it
# is the one with a hard external constraint -- its calls are capped by stock actually held,
# so margin it does not take is margin nothing else can use. The scalpers come last: they
# size off what is left, and both are capped by their own rupee budgets anyway. CAS Bingo
# trades last of all in the day (the closing auction), so it sits after them.
_DEFAULT_PRIORITY = {
    BOT_HOLDINGS_WRITER: 1,
    BOT_EXPIRY_INDEX_WRITER: 2,
    BOT_MOMENTUM_LONG_SCALPER: 3,
    BOT_IRON_FLY_SCALPER: 4,
    BOT_CAS_BINGO: 5,
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
        default_priority = _DEFAULT_PRIORITY.get(bot_type, 9)
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


# All four bots are listed as of step 8. `get_or_create_bot` makes each lazily on first
# sight, disabled and in paper mode, so a deployment upgrading into this needs no backfill --
# the scalpers simply appear, switched off.
_LISTED_BOT_TYPES = (
    BOT_HOLDINGS_WRITER,
    BOT_EXPIRY_INDEX_WRITER,
    BOT_MOMENTUM_LONG_SCALPER,
    BOT_IRON_FLY_SCALPER,
    BOT_CAS_BINGO,
)


def list_bots(user_id: str) -> list[BotRecord]:
    return [get_or_create_bot(user_id, t) for t in _LISTED_BOT_TYPES]


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


def has_committed_run_today(
    user_id: str,
    bot_type: str,
    *,
    retryable_reason_codes: Iterable[str] = (),
    retry_after_minutes: float = 0.0,
) -> bool:
    """Has this bot done something today that it must not do twice?

    Deliberately narrower than `has_terminal_run_today`, which treats *any* run row as
    resolving the day. That is right for the autonomous path, but a semi-autonomous bot
    writes a `proposed` run the moment it asks the user -- and asking is not acting. If
    proposing counted, the first proposal would block the re-proposal loop and the eventual
    placement, and the bot would never trade.

    `proposed` is therefore the one status excluded unconditionally. `running` still counts,
    so a run in flight is never started alongside itself.

    `retryable_reason_codes` (with `retry_after_minutes`) extends the same reasoning one
    step further, to runs that *finished* without deciding anything: a chain that had not
    warmed, a quote that had not arrived. Those rows stay in the log -- an unexplained
    no-trade day is the failure the log exists to prevent -- but they stop blocking once the
    retry interval has passed, so the bot gets the rest of its window instead of standing
    down on the first pricing miss. Both arguments default to the old behaviour, so callers
    that have a real answer (Bot 1's autonomous path, every existing test) are unaffected.
    """
    today = now_ist().date().isoformat()
    codes = tuple(retryable_reason_codes)
    with _connect() as conn:
        rows = conn.execute(
            "SELECT status, reason_code, finished_at FROM bot_runs "
            "WHERE user_id = ? AND bot_type = ? AND date(started_at) = ? "
            "AND status != 'proposed'",
            (user_id, bot_type, today),
        ).fetchall()

    for row in rows:
        if not _is_retryable_run(row, codes, retry_after_minutes):
            return True
    return False


def _is_retryable_run(row: Any, codes: tuple[str, ...], retry_after_minutes: float) -> bool:
    """True when this row is a finished, retryable pricing miss whose cooling-off has passed.

    A row still `running` is never retryable however it is coded -- something is working on
    it right now, and starting a second attempt alongside it is the double-fire this gate
    exists to prevent.
    """
    if not codes:
        return False
    if str(row["status"]) == "running":
        return False
    if str(row["reason_code"] or "") not in codes:
        return False
    finished_at = _parse_ist_timestamp(row["finished_at"])
    if finished_at is None:
        return False
    elapsed_minutes = (now_ist().replace(tzinfo=None) - finished_at).total_seconds() / 60.0
    return elapsed_minutes >= float(retry_after_minutes)


def _parse_ist_timestamp(raw: Any) -> Optional[datetime.datetime]:
    if not raw:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return datetime.datetime.strptime(str(raw), fmt)
        except (TypeError, ValueError):
            continue
    return None


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
    # COALESCE(heartbeat_at, started_at): Bots 1 and 2 never beat, so for them this is
    # `started_at` and the semantics are exactly what they were. A scalper session is
    # legitimately `running` for hours, so ageing it from `started_at` would reap a healthy
    # bot mid-trade; ageing it from its heartbeat still catches one that has hung.
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
        sql += " AND COALESCE(heartbeat_at, started_at) < ?"
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
# Scalper sessions and cycles (docs/bots-scalping-plan.md sections 6 and 9)
# --------------------------------------------------------------------------------------


def update_run_reason(
    run_id: str,
    *,
    reason_code: str,
    reason_text: str,
    detail: Optional[dict[str, Any]] = None,
) -> None:
    """Record what a *still-running* session is currently doing, and why.

    Only the scalpers call this. Every other bot resolves in one pass and writes its reason
    once, at the end; a scalper holds one row open all day, so without this the run log's
    Reason column reads "—" for the whole session -- which is exactly the question the run
    log exists to answer (a bot that traded nothing looks identical to one that was never
    running at all).

    Guarded on `status = 'running'` so it can never overwrite the verdict a finished session
    settled on: the loop keeps ticking after `finalise_session` has closed the day, and the
    last word must stay the one that closed it.
    """
    with _connect() as conn:
        conn.execute(
            "UPDATE bot_runs SET reason_code = ?, reason_text = ?, detail = ? "
            "WHERE id = ? AND status = 'running'",
            (reason_code, reason_text, json.dumps(detail or {}), run_id),
        )
        conn.commit()


def touch_run_heartbeat(run_id: str) -> None:
    """Mark a long-lived run as still alive.

    Only the scalpers call this. A row that has never been beaten keeps `heartbeat_at` NULL,
    and `reap_stale_runs` coalesces to `started_at`, so nothing changes for the bots that
    finish in one pass.
    """
    with _connect() as conn:
        conn.execute(
            "UPDATE bot_runs SET heartbeat_at = ? WHERE id = ? AND status = 'running'",
            (ist_timestamp(), run_id),
        )
        conn.commit()


def open_session_run(user_id: str, bot_type: str) -> str:
    """The session run for today, reusing one that is already open.

    Idempotent because the caller is a loop, not a scheduler tick: it asks for its session on
    every wake, and a second row would split one day's cycles across two runs and quietly
    reset the consecutive-loss counter that reads them.
    """
    today = now_ist().strftime("%Y-%m-%d")
    with _connect() as conn:
        # `completed` counts as well as `running`: once the day has been finalised the loop
        # keeps ticking, and matching only `running` would mint a fresh session row on every
        # pass -- each of which then never gets finalised and is reaped as "interrupted".
        # One session row per bot per day is the whole point.
        #
        # `failed` deliberately does NOT count. That is a run the reaper closed because the
        # process died mid-session; the interruption is a real record worth keeping, and what
        # follows it is genuinely a new session rather than a continuation.
        row = conn.execute(
            "SELECT id FROM bot_runs WHERE user_id = ? AND bot_type = ? "
            "AND status IN ('running', 'completed') AND trigger = 'session' "
            "AND DATE(started_at) = ? ORDER BY started_at DESC LIMIT 1",
            (user_id, bot_type, today),
        ).fetchone()
        if row is not None:
            return str(row["id"])
        run_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO bot_runs (id, user_id, bot_type, trigger, status, heartbeat_at) "
            "VALUES (?, ?, ?, 'session', 'running', ?)",
            (run_id, user_id, bot_type, ist_timestamp()),
        )
        conn.commit()
    return run_id


def stamp_session_config(run_id: str, config_hash: str, mode: str) -> None:
    """Record which settings a session ran on -- and void the record if they changed.

    The paper-evidence gate (`scalping/evidence.py`) counts *completed paper trading days on
    the current settings*. A session stamped at 09:15 and then edited at 11:00 ran half a day
    on each, and is honest evidence for neither: crediting it to the settings it started with
    would let a user paper-prove one configuration, switch to another mid-morning, and arm
    that one on the first configuration's record.

    So the stamp is written once, and any later disagreement -- a different hash, or paper
    turning into live -- clears `config_hash` to NULL and marks the mode `mixed`. NULL matches
    no hash, so the day simply stops counting. It is never restored: once a day is mixed it
    stays mixed, even if the user puts the old settings back.
    """
    with _connect() as conn:
        row = conn.execute(
            "SELECT config_hash, mode FROM bot_runs WHERE id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return
        stored_hash, stored_mode = row["config_hash"], row["mode"]
        if stored_hash is None and stored_mode is None:
            new_hash, new_mode = config_hash, mode  # first stamp of the day
        elif stored_hash == config_hash and stored_mode == mode:
            return  # unchanged; the common case, and it writes nothing
        else:
            new_hash, new_mode = None, "mixed"
        conn.execute(
            "UPDATE bot_runs SET config_hash = ?, mode = ? WHERE id = ?",
            (new_hash, new_mode, run_id),
        )
        conn.commit()


def completed_paper_sessions(
    user_id: str, bot_type: str, config_hash: str
) -> list["EvidenceSession"]:
    """Completed paper trading days carrying exactly these settings, newest first.

    `status = 'completed'` is the whole liveness test: `reap_stale_runs` marks an interrupted
    session `failed`, so anything still `completed` reached a real end of day -- whether that
    was the last window closing or the daily stop firing. Both are a day's evidence, and the
    reason code travels with the row so the confirmation dialog can say which it was.
    """
    from icici_breeze_backend.app.services.bots.scalping.evidence import EvidenceSession

    with _connect() as conn:
        runs = conn.execute(
            "SELECT id, started_at, reason_code, reason_text FROM bot_runs "
            "WHERE user_id = ? AND bot_type = ? AND config_hash = ? AND mode = 'paper' "
            "AND status = 'completed' AND trigger = 'session' "
            "ORDER BY started_at DESC",
            (user_id, bot_type, config_hash),
        ).fetchall()
        out: list[EvidenceSession] = []
        for run in runs:
            cycles = [
                _row_to_cycle(r)
                for r in conn.execute(
                    "SELECT * FROM bot_cycles WHERE run_id = ? ORDER BY cycle_no ASC",
                    (run["id"],),
                ).fetchall()
            ]
            closed = [c for c in cycles if c.closed_at is not None]
            started = str(run["started_at"] or "")
            out.append(
                EvidenceSession(
                    run_id=str(run["id"]),
                    trading_day=started[:10],
                    reason_code=run["reason_code"],
                    reason_text=run["reason_text"],
                    cycles=len(cycles),
                    closed_cycles=len(closed),
                    wins=sum(1 for c in closed if (c.net_pnl or 0.0) > 0),
                    losses=sum(1 for c in closed if c.is_loss),
                    net_pnl=round(sum(c.net_pnl or 0.0 for c in closed), 2),
                    friction=round(sum(c.friction or 0.0 for c in cycles), 2),
                )
            )
    return out


def bots_with_open_live_cycles() -> list[tuple[str, str]]:
    """`(user_id, bot_type)` for every bot holding an OPEN LIVE position, armed or not.

    The driver ticks enabled bots. A live position outlives that: a user who sets a bot to
    Off or Paper while it holds one would, without this, strand a real position at the
    exchange with nothing evaluating its stop. This is what lets the loop keep managing such
    a position to its exit -- entries stopped, exits still running (plan section 5.5's rule,
    extended past the arming switch itself).

    Paper cycles are excluded deliberately: an abandoned simulation costs nothing and has no
    exchange side to reconcile.
    """
    with _connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT user_id, bot_type FROM bot_cycles "
            "WHERE closed_at IS NULL AND paper = 0"
        ).fetchall()
    return [(str(r["user_id"]), str(r["bot_type"])) for r in rows]


def users_with_open_cycles(bot_type: str) -> list[str]:
    """Every user holding an open cycle of this bot type, paper or live, armed or not.

    CAS Bingo's loop ticks these for their exits. Unlike the scalpers it keeps managing an
    open *simulated* position after the bot is switched to Manual too: a simulation that
    stopped marking its position mid-way would leave a cycle open for ever, and the exit
    loop costs nothing but cache reads.
    """
    with _connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT user_id FROM bot_cycles WHERE bot_type = ? AND closed_at IS NULL",
            (bot_type,),
        ).fetchall()
    return [str(r["user_id"]) for r in rows]


def _row_to_cycle(row: sqlite3.Row) -> BotCycleRecord:
    d = dict(row)
    return BotCycleRecord(
        id=str(d["id"]),
        run_id=str(d["run_id"]),
        bot_type=d["bot_type"],
        cycle_no=int(d["cycle_no"]),
        structure=d["structure"],
        legs=_json_or([], d.get("legs")),
        lots=d.get("lots"),
        opened_at=str(d["opened_at"]) if d.get("opened_at") else None,
        closed_at=str(d["closed_at"]) if d.get("closed_at") else None,
        entry_value=d.get("entry_value"),
        exit_value=d.get("exit_value"),
        gross_pnl=d.get("gross_pnl"),
        friction=d.get("friction"),
        net_pnl=d.get("net_pnl"),
        exit_reason_code=d.get("exit_reason_code"),
        exit_reason_text=d.get("exit_reason_text"),
        detail=_json_or(None, d.get("detail")),
        paper=bool(d.get("paper", 1)),
    )


def open_cycle(
    user_id: str,
    bot_type: str,
    run_id: str,
    *,
    structure: str,
    legs: list[dict[str, Any]],
    lots: Optional[int] = None,
    entry_value: Optional[float] = None,
    paper: bool = True,
    detail: Optional[dict[str, Any]] = None,
) -> BotCycleRecord:
    """Record a cycle at the moment a position exists.

    `cycle_no` is per session and allocated inside the write, so the run log reads in the
    order things happened even if a caller ever opens cycles off more than one thread.
    """
    with _connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(cycle_no), 0) AS n FROM bot_cycles WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        cycle_no = int(row["n"]) + 1
        cycle_id = str(uuid.uuid4())
        # `config_hash` is copied from the session run inside the INSERT rather than passed
        # in: every caller would otherwise have to thread the same value through, and the one
        # that forgot would produce a cycle silently attributed to no settings at all. The
        # subquery also means a run voided mid-day (see `stamp_session_config`) yields NULL
        # here too, so the cycle inherits the voiding for free.
        conn.execute(
            "INSERT INTO bot_cycles (id, run_id, user_id, bot_type, cycle_no, structure, "
            "legs, lots, entry_value, detail, paper, config_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "(SELECT config_hash FROM bot_runs WHERE id = ?))",
            (
                cycle_id,
                run_id,
                user_id,
                bot_type,
                cycle_no,
                structure,
                json.dumps(legs or []),
                lots,
                entry_value,
                json.dumps(detail) if detail else None,
                1 if paper else 0,
                run_id,
            ),
        )
        conn.commit()
        return _row_to_cycle(
            conn.execute("SELECT * FROM bot_cycles WHERE id = ?", (cycle_id,)).fetchone()
        )


def pending_cycles(user_id: str, bot_type: str) -> list[BotCycleRecord]:
    """Cycles written before an order went out that never recorded a fill.

    These exist because a live entry writes its row BEFORE calling the broker: a crash
    between `place_order` returning and the row being updated would otherwise leave a real
    position that nothing knows about. A pending row is the marker that says "an order may
    exist for this" -- it is a question for startup to resolve, not a position to assume.
    """
    return [
        c
        for c in open_cycles(user_id, bot_type)
        if bool((c.detail or {}).get("pending"))
    ]


def replace_cycle_legs(cycle_id: str, legs: list[dict[str, Any]]) -> None:
    """Rewrite an open cycle's legs to what actually filled.

    A partial fill means the position is smaller than the one that was planned. The exit has
    to sell what is held, not what was asked for, so the leg is corrected at the moment the
    broker's answer is known rather than reconciled later.
    """
    with _connect() as conn:
        conn.execute(
            "UPDATE bot_cycles SET legs = ? WHERE id = ? AND closed_at IS NULL",
            (json.dumps(legs), cycle_id),
        )
        conn.commit()


def mark_cycle_placed(
    cycle_id: str, *, order_ids: list[str], detail: dict[str, Any]
) -> None:
    """Resolve an intent row once the broker has answered.

    Clears `pending`, so the row stops being a reconciliation question and becomes an
    ordinary open cycle the exit loop will manage.
    """
    merged = dict(detail or {})
    merged.pop("pending", None)
    merged["order_ids"] = list(order_ids)
    with _connect() as conn:
        conn.execute(
            "UPDATE bot_cycles SET detail = ?, paper = 0 WHERE id = ?",
            (json.dumps(merged), cycle_id),
        )
        conn.commit()


def abandon_cycle(cycle_id: str, *, reason_code: str, reason_text: str) -> None:
    """Close an intent row that never became a position.

    Zeroed rather than left open: nothing was traded, so it must not read as a loss, count
    towards the consecutive-loss cooldown, or leave the bot believing it holds something.
    """
    with _connect() as conn:
        conn.execute(
            "UPDATE bot_cycles SET closed_at = ?, gross_pnl = 0, friction = 0, net_pnl = 0, "
            "exit_reason_code = ?, exit_reason_text = ? WHERE id = ?",
            (ist_timestamp(), reason_code, reason_text, cycle_id),
        )
        conn.commit()


def close_cycle(
    cycle_id: str,
    *,
    exit_reason_code: str,
    exit_reason_text: str,
    exit_value: Optional[float] = None,
    gross_pnl: Optional[float] = None,
    friction: Optional[float] = None,
    detail: Optional[dict[str, Any]] = None,
) -> None:
    """Close a cycle. `net_pnl` is derived here, never passed in.

    Deriving it is the point: net is what the cumulative stop and the consecutive-loss
    counter both read, and letting callers supply it separately from its own components is
    how the two drift apart. Friction is subtracted whether or not the caller tracked it,
    which is what keeps `docs/bots-scalping-plan.md` section 6.4 honest -- friction is the
    binding constraint, so a cycle that omitted it must not read as break-even.
    """
    net = None
    if gross_pnl is not None:
        net = float(gross_pnl) - float(friction or 0.0)
    with _connect() as conn:
        conn.execute(
            "UPDATE bot_cycles SET closed_at = ?, exit_value = ?, gross_pnl = ?, "
            "friction = ?, net_pnl = ?, exit_reason_code = ?, exit_reason_text = ?, "
            "detail = COALESCE(?, detail) WHERE id = ?",
            (
                ist_timestamp(),
                exit_value,
                gross_pnl,
                friction,
                net,
                exit_reason_code,
                exit_reason_text,
                json.dumps(detail) if detail else None,
                cycle_id,
            ),
        )
        conn.commit()


def update_cycle_detail(cycle_id: str, detail: dict[str, Any]) -> None:
    """Replace an open cycle's detail blob -- how the trailing ladder survives a restart.

    Called only when the ladder's STOP moves, not on every new peak: the peak changes
    constantly in a rising market and changes nothing about what the position will do, while
    the stop is what a restart must not lose (docs/bots-scalping-plan.md section 5.5).
    """
    with _connect() as conn:
        conn.execute(
            "UPDATE bot_cycles SET detail = ? WHERE id = ? AND closed_at IS NULL",
            (json.dumps(detail), cycle_id),
        )
        conn.commit()


def list_cycles(
    user_id: str, *, run_id: Optional[str] = None, bot_type: Optional[str] = None, limit: int = 200
) -> list[BotCycleRecord]:
    sql = "SELECT * FROM bot_cycles WHERE user_id = ?"
    args: list[Any] = [user_id]
    if run_id:
        sql += " AND run_id = ?"
        args.append(run_id)
    if bot_type:
        sql += " AND bot_type = ?"
        args.append(bot_type)
    sql += " ORDER BY opened_at DESC, cycle_no DESC LIMIT ?"
    args.append(max(1, min(1000, int(limit))))
    with _connect() as conn:
        return [_row_to_cycle(r) for r in conn.execute(sql, args).fetchall()]


def open_cycles(user_id: str, bot_type: str) -> list[BotCycleRecord]:
    """Cycles with a live position. Bot 3 holds at most one; Bot 4 holds one fly.

    Read on every decision, and on startup: a cycle still open after a restart is a position
    the broker is holding that this process has forgotten about, which the runtime has to
    reconcile rather than open a second one alongside.
    """
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM bot_cycles WHERE user_id = ? AND bot_type = ? AND closed_at IS NULL "
            "ORDER BY opened_at ASC",
            (user_id, bot_type),
        ).fetchall()
    return [_row_to_cycle(r) for r in rows]


def scalper_day_totals(user_id: str, bot_type: str) -> ScalperDayTotals:
    """Today's running totals, recomputed from the cycle rows rather than accumulated.

    Recomputed on purpose: an in-memory counter and a restart are how a cumulative stop
    silently resets mid-session and lets a bot that has already lost its limit carry on
    trading. The rows are the truth, and they survive the process.

    `consecutive_losses` counts backwards from the most recent CLOSED cycle and stops at the
    first non-loss, so an aborted entry -- a cycle that never opened a position, and so was
    never a loss -- neither counts toward the cooldown nor clears it.
    """
    today = now_ist().strftime("%Y-%m-%d")
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM bot_cycles WHERE user_id = ? AND bot_type = ? "
            "AND DATE(opened_at) = ? ORDER BY cycle_no ASC",
            (user_id, bot_type, today),
        ).fetchall()
    cycles = [_row_to_cycle(r) for r in rows]
    closed = [c for c in cycles if c.closed_at is not None]
    consecutive = 0
    for cycle in reversed(closed):
        if cycle.is_loss:
            consecutive += 1
        else:
            break
    entry_start, entry_side = _last_entry_signal(cycles)
    return ScalperDayTotals(
        cycles=len(cycles),
        open_cycles=sum(1 for c in cycles if c.is_open),
        realized_net_pnl=round(sum(c.net_pnl or 0.0 for c in closed), 2),
        friction=round(sum(c.friction or 0.0 for c in cycles), 2),
        consecutive_losses=consecutive,
        last_closed_at=closed[-1].closed_at if closed else None,
        last_entry_candle_start=entry_start,
        last_entry_side=entry_side,
    )


_STRUCTURE_SIDE = {"long_ce": "bullish", "long_pe": "bearish"}


def _last_entry_signal(cycles: list[BotCycleRecord]) -> tuple[Optional[int], Optional[str]]:
    """(candle start, side) of the latest cycle that recorded its entry signal.

    Every cycle counts, an aborted entry included: a limit that never filled was still a bid
    on that signal run, and re-bidding it on the next minute is the chase plan section 3.6
    rules out. Cycles without a signal (Bot 4's flies) yield (None, None).
    """
    for cycle in reversed(cycles):
        signal = (cycle.detail or {}).get("signal") or {}
        side = _STRUCTURE_SIDE.get(cycle.structure)
        try:
            start = int(signal.get("candle_start"))
        except (TypeError, ValueError):
            continue
        if side is not None:
            return start, side
    return None, None


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
    # Annotated here rather than at each call site: this is the one choke point every
    # proposal passes through -- the app scan, the Telegram path and a reprice alike -- so a
    # user can never be shown a gross premium on one surface and a net one on another.
    from icici_breeze_backend.app.services.bots.net_premium import annotate_legs

    annotate_legs(legs)

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
