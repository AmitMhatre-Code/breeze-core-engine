"""Append-only per-day audit trail for the scalping bots.

**Why this exists.** A scalper's run row holds one verdict -- the last one published. Every
decision before it is overwritten, and the log line that carried the detail lives in a
container whose logs rotate (and vanish entirely when the instance is recreated overnight).
The result was a paper day that produced no trades and no way, the following morning, to say
which quiet day it had been. This file is the durable record that answers that question.

**Cadence.** Full fidelity inside a bot's configured session windows -- one record per driver
pass, roughly every two seconds -- because that is where trading decisions actually happen
and where "it evaluated the signal 1,800 times and it never fired" is the answer. Outside the
windows it falls back to recording only changes, since a bot standing down at 03:00 has
nothing new to say on each pass.

**Shape.** One JSON object per line (JSONL), one file per user per bot per IST trading day.
Append-only: a line is written and never revisited, so a crash costs at most the line being
written, and the file can be read while the bot is still writing it.

Modelled on `strategy_builder_audit`, which established the pattern these routes reuse:
files under `DATA_PATH`, an index for the UI, per-file and ZIP download.
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import threading
import zipfile
from datetime import date, datetime, timedelta
from typing import Any, Optional

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.timezone import IST, now_ist

_logger = logging.getLogger(__name__)

_AUDIT_SUBDIR = "bots-audit"

#: Trading days of history kept on disk. The volume is small at this cadence (a few MB a
#: day for both bots), but the data volume is shared with the databases and is only 2 GiB on
#: the oldest stacks, so this prunes rather than grows without bound.
RETENTION_DAYS = 7

#: Appends are serialised: the driver ticks bots from one thread today, but the file is
#: opened per write and a second writer would interleave partial lines.
_write_lock = threading.Lock()

#: (user, bot) -> the last signature written, so the out-of-window path can skip repeats.
_last_signature: dict[tuple[str, str], str] = {}

#: Guards retention so it runs once per process per day rather than on every append.
_last_pruned: dict[str, date] = {}


def audit_dir() -> str:
    path = os.path.join(cfg.DATA_PATH, _AUDIT_SUBDIR)
    os.makedirs(path, exist_ok=True)
    return path


def _safe_token(value: str, max_len: int = 32) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", (value or "unknown").strip())
    return cleaned[:max_len] or "unknown"


def file_name(user_id: str, bot_type: str, day: date) -> str:
    return f"{_safe_token(user_id, 16)}__{_safe_token(bot_type)}__{day.isoformat()}.jsonl"


def _parse_name(name: str) -> Optional[tuple[str, str, str]]:
    """(user_token, bot_type, iso_date) from a filename, or None if it is not ours."""
    if not name.endswith(".jsonl"):
        return None
    parts = name[: -len(".jsonl")].split("__")
    if len(parts) != 3:
        return None
    return parts[0], parts[1], parts[2]


# --------------------------------------------------------------------------- writing


def _signature(record: dict[str, Any]) -> str:
    """What makes an out-of-window record worth writing again.

    Deliberately excludes counters that move on every tick (`ticks_seen`) and the timestamp:
    outside a trading window the point is to record transitions, not to prove the loop is
    still running.
    """
    feed = record.get("feed") or {}
    return "|".join(
        str(x)
        for x in (
            record.get("action"),
            record.get("reason_code"),
            feed.get("warm"),
            feed.get("stale"),
            feed.get("subscribed"),
            feed.get("candles"),
            feed.get("counter_resets"),
            feed.get("stale_ticks"),
        )
    )


def record_pass(
    user_id: str,
    bot_type: str,
    run_id: str,
    *,
    detail: dict[str, Any],
    reason_code: Optional[str],
    reason_text: Optional[str],
    in_window: bool,
    now: Optional[datetime] = None,
) -> None:
    """Append one driver pass. Never raises -- an audit write must not stop a bot.

    `detail` is `runtime._audit_detail`'s output, so the file carries exactly the feed and
    gate state the run row shows, plus everything the run row has no room for.
    """
    try:
        stamp = now or now_ist()
        record = {
            "ts": stamp.isoformat(timespec="milliseconds"),
            "run_id": run_id,
            "bot_type": bot_type,
            "in_window": bool(in_window),
            "action": detail.get("action"),
            "reason_code": reason_code,
            "reason_text": reason_text,
            "feed": detail.get("feed"),
            "gates": detail.get("gates"),
            "decision": detail.get("decision"),
        }

        key = (user_id, bot_type)
        if not in_window:
            signature = _signature(record)
            if _last_signature.get(key) == signature:
                return
            _last_signature[key] = signature
        else:
            _last_signature[key] = _signature(record)

        _append(user_id, bot_type, stamp.date(), record)
    except Exception:  # noqa: BLE001 -- the trail is diagnostic; the bot outranks it
        _logger.exception("bot audit: could not record a pass for %s", bot_type)


def record_event(
    user_id: str, bot_type: str, run_id: str, event: str, payload: dict[str, Any]
) -> None:
    """Append a one-off event (a cycle opening, an entry refused by the broker layer).

    Never throttled: an event is by definition something that happened once.
    """
    try:
        stamp = now_ist()
        _append(
            user_id,
            bot_type,
            stamp.date(),
            {
                "ts": stamp.isoformat(timespec="milliseconds"),
                "run_id": run_id,
                "bot_type": bot_type,
                "event": event,
                "payload": payload,
            },
        )
    except Exception:  # noqa: BLE001
        _logger.exception("bot audit: could not record event %s for %s", event, bot_type)


def _append(user_id: str, bot_type: str, day: date, record: dict[str, Any]) -> None:
    path = os.path.join(audit_dir(), file_name(user_id, bot_type, day))
    line = json.dumps(record, default=str, separators=(",", ":"))
    with _write_lock:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    _maybe_prune(day)


# --------------------------------------------------------------------------- retention


def _maybe_prune(day: date) -> None:
    """Run retention at most once per process per day."""
    if _last_pruned.get("day") == day:
        return
    _last_pruned["day"] = day
    enforce_retention()


def enforce_retention(*, days: int = RETENTION_DAYS, today: Optional[date] = None) -> int:
    """Delete audit files older than `days` trading days. Returns the number removed.

    Dated from the filename rather than mtime: a file is named for the day it describes, and
    an append made after midnight must not make yesterday's file look fresh.
    """
    cutoff = (today or now_ist().date()) - timedelta(days=max(0, days))
    removed = 0
    try:
        root = audit_dir()
    except OSError:
        return 0
    for name in os.listdir(root):
        parsed = _parse_name(name)
        if parsed is None:
            continue
        try:
            file_day = date.fromisoformat(parsed[2])
        except ValueError:
            continue
        if file_day >= cutoff:
            continue
        try:
            os.remove(os.path.join(root, name))
            removed += 1
        except OSError as exc:
            _logger.warning("bot audit: could not remove %s: %s", name, exc)
    if removed:
        _logger.info("bot audit: pruned %d file(s) older than %s", removed, cutoff)
    return removed


# --------------------------------------------------------------------------- reading


def _files_for_user(user_id: str) -> list[str]:
    token = _safe_token(user_id, 16)
    root = audit_dir()
    out = []
    for name in os.listdir(root):
        parsed = _parse_name(name)
        if parsed is not None and parsed[0] == token:
            out.append(name)
    return sorted(out, reverse=True)


def _count_lines(path: str) -> int:
    try:
        with open(path, "rb") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


def list_index_for_user(user_id: str) -> list[dict[str, Any]]:
    """One row per bot per trading day, newest first, for the Settings screen."""
    rows: list[dict[str, Any]] = []
    for name in _files_for_user(user_id):
        parsed = _parse_name(name)
        if parsed is None:
            continue
        path = os.path.join(audit_dir(), name)
        try:
            size = os.path.getsize(path)
        except OSError:
            continue
        rows.append(
            {
                "name": name,
                "bot_type": parsed[1],
                "trading_date": parsed[2],
                "size_bytes": size,
                "records": _count_lines(path),
            }
        )
    return rows


def resolve_file_for_user(name: str, user_id: str) -> Optional[str]:
    """Path for `name`, but only when it belongs to `user_id`.

    The name is re-derived from its parts rather than trusted: a caller-supplied string
    reaches the filesystem here, and `..` or an absolute path must not survive it.
    """
    parsed = _parse_name(os.path.basename(name or ""))
    if parsed is None:
        return None
    token, bot_type, day = parsed
    if token != _safe_token(user_id, 16):
        return None
    rebuilt = f"{_safe_token(token, 16)}__{_safe_token(bot_type)}__{_safe_token(day)}.jsonl"
    if rebuilt != os.path.basename(name):
        return None
    path = os.path.join(audit_dir(), rebuilt)
    return path if os.path.isfile(path) else None


def find_for_run(user_id: str, bot_type: str, started_at: Optional[str]) -> Optional[str]:
    """The audit filename covering a run row, from its `started_at` stamp.

    One link per bot per day: a day fragmented across a dozen interrupted session rows still
    resolves to a single continuous file, which is the point.
    """
    if not started_at or len(str(started_at)) < 10:
        return None
    day = str(started_at)[:10]
    try:
        date.fromisoformat(day)
    except ValueError:
        return None
    name = f"{_safe_token(user_id, 16)}__{_safe_token(bot_type)}__{day}.jsonl"
    return name if os.path.isfile(os.path.join(audit_dir(), name)) else None


def build_zip_for_user(user_id: str) -> tuple[bytes, str]:
    """Every retained audit file for one user, zipped."""
    names = _files_for_user(user_id)
    if not names:
        raise FileNotFoundError(f"No bot audit logs for user {user_id}")
    buf = io.BytesIO()
    root = audit_dir()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in names:
            zf.write(os.path.join(root, name), arcname=name)
    return buf.getvalue(), f"bot-audit-{_safe_token(user_id, 16)}.zip"
