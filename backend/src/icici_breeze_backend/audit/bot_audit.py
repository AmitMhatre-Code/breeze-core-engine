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
    """(user_token, bot_type, iso_date or run id) from a filename, or None if it is not ours.
    A backtest run's results are a `.zip`; every other trail is `.jsonl`."""
    ext = ".zip" if name.endswith(".zip") else ".jsonl" if name.endswith(".jsonl") else None
    if ext is None:
        return None
    parts = name[: -len(ext)].split("__")
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


# --------------------------------------------------------------------------- backtests (#35)

_BACKTEST_SUBDIR = "backtests"
#: Backtest trails kept per deployment. A trail is the record of one replay, not of a trading
#: day, so it is not dated and does not age out on the daily retention; the newest are kept.
BACKTEST_KEEP = 200


def backtest_dir() -> str:
    path = os.path.join(audit_dir(), _BACKTEST_SUBDIR)
    os.makedirs(path, exist_ok=True)
    return path


def backtest_file_name(user_id: str, bot_type: str, run_id: str) -> str:
    return f"{_safe_token(user_id, 16)}__{_safe_token(bot_type)}__{_safe_token(run_id, 40)}.jsonl"


def write_backtest_audit(
    user_id: str, bot_type: str, run_id: str, records: list[dict[str, Any]]
) -> str:
    """Write one replay's whole trail in a single pass. Returns the file name.

    Run-scoped rather than day-scoped: a month's backtest is one result, and splitting it into
    twenty daily files would rebuild exactly the fragmentation the live trail's per-day file
    exists to avoid (#35)."""
    name = backtest_file_name(user_id, bot_type, run_id)
    path = os.path.join(backtest_dir(), name)
    with _write_lock:
        with open(path, "w", encoding="utf-8") as fh:
            for record in records:
                fh.write(json.dumps(record, default=str, separators=(",", ":")) + "\n")
    _prune_backtests()
    return name


def backtest_zip_name(user_id: str, bot_type: str, run_id: str) -> str:
    return backtest_file_name(user_id, bot_type, run_id)[: -len(".jsonl")] + ".zip"


def _csv_text(rows: list[dict[str, Any]]) -> str:
    """Rows as CSV, columns in first-seen order."""
    import csv

    columns: dict[str, None] = {}
    for row in rows:
        for key in row:
            columns.setdefault(key, None)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(columns), extrasaction="ignore", restval="")
    writer.writeheader()
    for row in rows:
        writer.writerow({
            k: (json.dumps(v, default=str) if isinstance(v, (dict, list)) else
                "" if v is None else v)
            for k, v in row.items()
        })
    return buf.getvalue()


class BacktestZipWriter:
    """One backtest run's zip, written a member at a time.

    **Why not one `members` dict.** A month replayed across Bot 3's twelve signal settings
    produces on the order of a hundred thousand decision rows -- one per minute each setting was
    flat. Handing them over as a single dict meant every row of every setting had to be live at
    the moment the zip was written, which is the peak the process did not survive: no exception,
    no failed run, just a `SIGKILL` and a row the next startup reads as interrupted
    (docs/design-decisions.md #41). The caller adds a setting's files as that setting finishes
    and drops the rows, so only one setting's worth is ever held.

    The file is assembled under a `.partial` name and moved into place by `close()`, so a run
    that dies part-way leaves nothing that looks like a complete result. `_prune_backtests`
    sweeps partials a dead process left behind, since `abandon()` cannot run after a kill.
    """

    def __init__(self, user_id: str, bot_type: str, run_id: str) -> None:
        self.name = backtest_zip_name(user_id, bot_type, run_id)
        self._path = os.path.join(backtest_dir(), self.name)
        self._partial = self._path + ".partial"
        self._zip = zipfile.ZipFile(self._partial, "w", compression=zipfile.ZIP_DEFLATED)
        self._closed = False

    def add(self, member: str, content: Any) -> None:
        """Add one member: text as it stands, a list of dict rows as CSV."""
        self._zip.writestr(member, content if isinstance(content, str) else _csv_text(content))

    def add_all(self, members: dict[str, Any]) -> None:
        for member, content in members.items():
            self.add(member, content)

    def close(self) -> str:
        """Finish the zip and move it into place. Returns the file name."""
        self._zip.close()
        self._closed = True
        # The lock guards the shared directory, not the zip: every run writes its own file, but
        # the rename and the prune race the live trail's own pruning.
        with _write_lock:
            os.replace(self._partial, self._path)
        _prune_backtests()
        return self.name

    def abandon(self) -> None:
        """Drop a half-built zip. Never raises: it runs on the failure path."""
        try:
            if not self._closed:
                self._zip.close()
                self._closed = True
        except Exception:  # noqa: BLE001 -- see docstring
            pass
        try:
            os.remove(self._partial)
        except OSError:
            pass


def write_backtest_zip(
    user_id: str, bot_type: str, run_id: str, members: dict[str, Any]
) -> str:
    """Write one backtest run's results as a zip (docs/signals-streamline-plan.md section 8).

    `members` maps a path inside the zip to either text or a list of dict rows (written as CSV,
    columns in first-seen order). Returns the file name, which the Activity row downloads. A run
    big enough for that dict to matter should use `BacktestZipWriter` directly instead."""
    writer = BacktestZipWriter(user_id, bot_type, run_id)
    try:
        writer.add_all(members)
    except BaseException:
        writer.abandon()
        raise
    return writer.close()


#: A `.partial` zip older than this belonged to a process that was killed mid-run -- one
#: backtest runs at a time and none takes hours, so nothing legitimate is this old.
_PARTIAL_STALE_SECONDS = 6 * 3600


def _sweep_partials(root: str, now: float) -> None:
    """Remove zips a killed process left half-written. Best effort; never raises."""
    try:
        names = [n for n in os.listdir(root) if n.endswith(".partial")]
    except OSError:
        return
    for name in names:
        path = os.path.join(root, name)
        try:
            if now - os.path.getmtime(path) > _PARTIAL_STALE_SECONDS:
                os.remove(path)
        except OSError:
            continue


def _prune_backtests(keep: int = BACKTEST_KEEP) -> int:
    import time

    try:
        root = backtest_dir()
        names = [n for n in os.listdir(root) if n.endswith((".jsonl", ".zip"))]
    except OSError:
        return 0
    _sweep_partials(root, time.time())
    if len(names) <= keep:
        return 0
    names.sort(key=lambda n: os.path.getmtime(os.path.join(root, n)), reverse=True)
    removed = 0
    for name in names[keep:]:
        try:
            os.remove(os.path.join(root, name))
            removed += 1
        except OSError:
            continue
    return removed


def resolve_backtest_file_for_user(name: str, user_id: str) -> Optional[str]:
    """Path for a backtest trail, only when it belongs to `user_id`; name re-derived, as for the
    daily trails, so a traversal attempt resolves to nothing."""
    base = os.path.basename(name or "")
    parsed = _parse_name(base)
    if parsed is None:
        return None
    token, bot_type, run_id = parsed
    if token != _safe_token(user_id, 16):
        return None
    ext = ".zip" if base.endswith(".zip") else ".jsonl"
    rebuilt = f"{_safe_token(token, 16)}__{_safe_token(bot_type)}__{_safe_token(run_id, 40)}{ext}"
    if rebuilt != base:
        return None
    path = os.path.join(backtest_dir(), rebuilt)
    return path if os.path.isfile(path) else None


def find_for_backtest_run(user_id: str, bot_type: str, run_id: str) -> Optional[str]:
    """The run's download: its results zip, or the trail of a run made before zips existed."""
    name = backtest_file_name(user_id, bot_type, run_id)
    zipped = name[: -len(".jsonl")] + ".zip"
    for candidate in (zipped, name):
        if os.path.isfile(os.path.join(backtest_dir(), candidate)):
            return candidate
    return None
