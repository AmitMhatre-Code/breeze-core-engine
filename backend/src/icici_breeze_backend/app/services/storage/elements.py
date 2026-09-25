"""What occupies the data volume, element by element, and how each deletable one is deleted.

Sizes are exact for whole files and directories. Inside `backtest.sqlite3` they are estimates:
the SQLite this app ships cannot report per-table sizes (no `dbstat`), so each small table is
sized as row count x sampled row width, indexes included, and the option candles -- nearly all of
the file, and far too big to scan in a request -- as what the file holds beyond them. The pages a
delete freed but compaction has not yet returned are reported on their own line, so the
estimates plus that line always add up to the real file size.

Deletes are by IST calendar date, inclusive at both ends. What each date means:

* bars (futures, cash index, option) and VIX -- the bar's own date;
* bot backtest runs and signal backtest zips -- the day the run was started;
* bot and Strategy Builder audit logs -- the day the file is named for;
* application logs -- the day a rotated file was last written;
* the request & page-view log -- the day each row was stamped (IST).

Guarded on purpose:

* the two most recent sessions of futures bars, which the live navbar signal warms up from
  (`index_signal.warmup.cached_bars`) -- deleting them only costs ICICI calls to fetch back;
* the log files the processes are writing to now, and today's bot audit file;
* a bot backtest run that is still running;
* signal backtest *rows*: only their zips go, because the 30-day bot gate reads the rows;
* `audit_log`'s event rows (orders, square-off rules, GTT exits, bots): only request and
  page-view rows are deletable (`audit_retention`).
"""
from __future__ import annotations

import datetime
import os
import sqlite3
import threading
from dataclasses import dataclass
from typing import Any, Callable, Optional

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.timezone import IST, now_ist

_TS = "%Y-%m-%d %H:%M:%S"

#: Indexes on each backtest-cache table, by the columns each one copies. A composite PRIMARY KEY
#: on a rowid table is a separate index, so it counts as one.
_CACHE_TABLES: dict[str, list[tuple[str, ...]]] = {
    "futures_candles": [("stock_code", "ts"), ("ts",)],
    "spot_candles": [("stock_code", "ts"), ("ts",)],
    "daily_vix": [("date",)],
    "option_candles": [("stock_code", "expiry", "strike", "right", "interval", "ts")],
    "option_fetches": [("stock_code", "expiry", "strike", "right", "interval", "start_ts", "end_ts")],
    "option_needs": [("stock_code", "expiry", "strike", "right", "interval", "start_ts", "end_ts")],
    "meta": [("key",)],
    "backtest_runs": [("id",), ("user_id", "created_at")],
}
_SAMPLE_ROWS = 500
#: Per-row cell header and rowid, per-index-entry overhead: small, but on a million option bars
#: they are most of the difference between a guess and a useful estimate.
_ROW_OVERHEAD = 12
_INDEX_OVERHEAD = 10


@dataclass(frozen=True)
class DateRange:
    start: datetime.date
    end: datetime.date

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError("The end date is before the start date.")

    @property
    def ts_from(self) -> str:
        return f"{self.start.isoformat()} 00:00:00"

    @property
    def ts_before(self) -> str:
        return f"{(self.end + datetime.timedelta(days=1)).isoformat()} 00:00:00"

    def contains(self, day: datetime.date) -> bool:
        return self.start <= day <= self.end


# --------------------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------------------


def _data(*parts: str) -> str:
    return os.path.join(cfg.DATA_PATH, *parts)


def cache_path() -> str:
    from icici_breeze_backend.app.services.bots.scalping import backtest_store as store

    return store.db_path()


def _file_bytes(path: str) -> int:
    total = 0
    for suffix in ("", "-wal", "-journal", "-shm"):
        try:
            total += os.path.getsize(path + suffix)
        except OSError:
            pass
    return total


def _dir_files(path: str, *, recursive: bool = True) -> list[tuple[str, int, float]]:
    """(path, bytes, mtime) of every file under `path`."""
    out: list[tuple[str, int, float]] = []
    if not os.path.isdir(path):
        return out
    walker = os.walk(path) if recursive else [(path, [], os.listdir(path))]
    for root, _dirs, names in walker:
        for name in names:
            full = os.path.join(root, name)
            try:
                if os.path.isfile(full):
                    st = os.stat(full)
                    out.append((full, st.st_size, st.st_mtime))
            except OSError:
                continue
    return out


def _mtime_day(mtime: float) -> datetime.date:
    return datetime.datetime.fromtimestamp(mtime, IST).date()


def _day_label(day: datetime.date) -> str:
    return f"{day.day} {day:%b %Y}"


def _span(days: list[datetime.date]) -> dict[str, Any]:
    if not days:
        return {"from": None, "to": None}
    return {"from": min(days).isoformat(), "to": max(days).isoformat()}


# --------------------------------------------------------------------------------------
# The backtest cache: estimated table sizes and coverage
# --------------------------------------------------------------------------------------

_cache_memo: dict[str, Any] = {}
#: One measurement at a time: requests that arrive while one runs wait for it and reuse its
#: answer instead of each starting their own.
_cache_lock = threading.Lock()

#: The table deliberately never scanned. On a deployment it is nearly all of the file (the cache
#: trims itself at 2 GB) with no index on `ts`, so one COUNT or MIN/MAX over it on a t4g.small
#: outlasted nginx's 60-second timeout -- and each abandoned request kept scanning. It is sized as
#: what the file holds beyond everything else, and dated from the fetch log.
_BULK_TABLE = "option_candles"
#: A B-tree page is rarely full, so row bytes understate the pages a small table occupies.
_PAGE_FILL = 1.25


def _table_rows(conn: sqlite3.Connection, table: str, indexes: list[tuple[str, ...]]) -> tuple[int, float]:
    """(rows, estimated bytes per row including its index entries) for one small table."""
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    rows = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] or 0)
    if not rows:
        return 0, 0.0

    def width(names: tuple[str, ...] | list[str]) -> float:
        expr = " + ".join(f'COALESCE(LENGTH("{c}"), 0)' for c in names) or "0"
        value = conn.execute(
            f"SELECT AVG({expr}) FROM (SELECT * FROM {table} LIMIT {_SAMPLE_ROWS})"
        ).fetchone()[0]
        return float(value or 0.0)

    per_row = width(cols) + _ROW_OVERHEAD
    per_row += sum(width(idx) + _INDEX_OVERHEAD for idx in indexes)
    return rows, per_row * _PAGE_FILL


def cache_breakdown(path: Optional[str] = None) -> dict[str, Any]:
    """Estimated bytes per table, the free pages, and each table's coverage.

    Never reads the option candles row by row (see `_BULK_TABLE`): every query here is bounded
    by the small tables. Memoised on the file's size and mtime all the same, so reopening the
    screen costs nothing until the cache changes."""
    path = path or cache_path()
    if not os.path.exists(path):
        return {"file_bytes": 0, "free_bytes": 0, "tables": {}, "coverage": {}}
    with _cache_lock:
        stamp = (path, _file_bytes(path), os.path.getmtime(path))
        if _cache_memo.get("stamp") == stamp:
            return _cache_memo["value"]
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30) as conn:
            page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])
            page_count = int(conn.execute("PRAGMA page_count").fetchone()[0])
            free_pages = int(conn.execute("PRAGMA freelist_count").fetchone()[0])
            present = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            small = {
                t: _table_rows(conn, t, idx) for t, idx in _CACHE_TABLES.items()
                if t in present and t != _BULK_TABLE
            }
            bulk_has_rows = _BULK_TABLE in present and conn.execute(
                f"SELECT 1 FROM {_BULK_TABLE} LIMIT 1"
            ).fetchone() is not None
            coverage = _cache_coverage(conn, present)
        file_bytes = _file_bytes(path)
        free_bytes = free_pages * page_size
        used = max(0, file_bytes - free_bytes)
        estimates = {t: rows * per_row for t, (rows, per_row) in small.items()}
        tables: dict[str, dict[str, int]]
        if bulk_has_rows:
            # The small tables' estimates stand; the option candles are what is left. If the
            # estimates somehow exceed the file, they are scaled down to it rather than going negative.
            scale = min(1.0, used / (sum(estimates.values()) or 1.0))
            tables = {t: {"bytes": int(b * scale)} for t, b in estimates.items()}
            tables[_BULK_TABLE] = {"bytes": used - sum(v["bytes"] for v in tables.values())}
        else:
            total = sum(estimates.values()) or 1.0
            tables = {t: {"bytes": int(used * b / total)} for t, b in estimates.items()}
            if _BULK_TABLE in present:
                tables[_BULK_TABLE] = {"bytes": 0}
        value = {
            "file_bytes": file_bytes,
            "free_bytes": free_bytes,
            "page_bytes": page_size * page_count,
            "tables": tables,
            "coverage": coverage,
        }
        _cache_memo.update(stamp=stamp, value=value)
        return value


def _cache_coverage(conn: sqlite3.Connection, present: set[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for table in ("futures_candles", "spot_candles"):
        if table not in present:
            continue
        series = []
        for code, first, last, days in conn.execute(
            f"SELECT stock_code, MIN(ts), MAX(ts), COUNT(DISTINCT substr(ts, 1, 10)) "
            f"FROM {table} GROUP BY stock_code ORDER BY stock_code"
        ):
            series.append({"name": code, "from": str(first)[:10], "to": str(last)[:10], "days": int(days)})
        out[table] = series
    if "daily_vix" in present:
        first, last, days = conn.execute("SELECT MIN(date), MAX(date), COUNT(*) FROM daily_vix").fetchone()
        out["daily_vix"] = {"from": first, "to": last, "days": int(days or 0)}
    if "option_fetches" in present:
        # Dated from the fetch log, not the bars (see `_BULK_TABLE`). Windows that came back empty
        # are left out. A date-range delete removes the fetch records it overlaps, so this can read
        # a little narrower than the bars left behind -- never wider.
        first, last, e_first, e_last = conn.execute(
            "SELECT MIN(start_ts), MAX(end_ts), MIN(expiry), MAX(expiry) FROM option_fetches WHERE rows > 0"
        ).fetchone()
        contracts = conn.execute(
            'SELECT COUNT(*) FROM (SELECT DISTINCT stock_code, expiry, strike, "right" '
            "FROM option_fetches WHERE rows > 0)"
        ).fetchone()[0]
        out["option_candles"] = {
            "from": str(first)[:10] if first else None,
            "to": str(last)[:10] if last else None,
            "days": None,
            "expiry_from": e_first,
            "expiry_to": e_last,
            "contracts": int(contracts or 0),
        }
    if "backtest_runs" in present:
        first, last, runs = conn.execute(
            "SELECT MIN(substr(created_at, 1, 10)), MAX(substr(created_at, 1, 10)), COUNT(*) FROM backtest_runs"
        ).fetchone()
        out["backtest_runs"] = {"from": first, "to": last, "runs": int(runs or 0)}
    return out


def cache_free_bytes(path: Optional[str] = None) -> int:
    """Pages inside the cache a delete freed and compaction has not handed back yet."""
    path = path or cache_path()
    if not os.path.exists(path):
        return 0
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30) as conn:
            page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])
            return page_size * int(conn.execute("PRAGMA freelist_count").fetchone()[0])
    except sqlite3.Error:
        return 0


def invalidate_cache_memo() -> None:
    _cache_memo.clear()


# --------------------------------------------------------------------------------------
# Guards
# --------------------------------------------------------------------------------------


def protected_futures_from(today: Optional[datetime.date] = None) -> datetime.date:
    """The first day of futures bars that deletion may not touch: the older of the two sessions
    the live signal warms up from. Everything from it onward (today included) is kept."""
    from icici_breeze_backend.app.services.index_signal import warmup

    today = today or now_ist().date()
    sessions = warmup.previous_sessions(today)
    return sessions[0] if sessions else today


def _rotated_log(name: str) -> bool:
    """A rotated backup (`backend.jsonl.1`), as opposed to a file a process is writing now."""
    stem, _, suffix = name.rpartition(".")
    return bool(stem) and suffix.isdigit()


def _bot_audit_day(name: str) -> Optional[datetime.date]:
    from icici_breeze_backend.audit import bot_audit

    parsed = bot_audit._parse_name(name)  # noqa: SLF001
    if parsed is None or not name.endswith(".jsonl"):
        return None
    try:
        return datetime.date.fromisoformat(parsed[2])
    except ValueError:
        return None


def _strategy_audit_day(name: str) -> Optional[datetime.date]:
    """`20260708T172849Z_<user>_<index>_<id>.json` -> 2026-07-08 (UTC-stamped; the date part is
    close enough for a delete-by-day, and never more than a day out)."""
    try:
        return datetime.datetime.strptime(name[:8], "%Y%m%d").date()
    except ValueError:
        return None


# --------------------------------------------------------------------------------------
# Inventory
# --------------------------------------------------------------------------------------


def _element(key: str, label: str, group: str, *, bytes_: int, approx: bool = False,
             deletable: bool, description: str, coverage: Optional[dict[str, Any]] = None,
             series: Optional[list[dict[str, Any]]] = None, count: Optional[int] = None,
             count_unit: Optional[str] = None, guard: Optional[str] = None) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "group": group,
        "bytes": int(bytes_),
        "approx": approx,
        "deletable": deletable,
        "description": description,
        "from": (coverage or {}).get("from"),
        "to": (coverage or {}).get("to"),
        "days": (coverage or {}).get("days"),
        "series": series or [],
        "count": count,
        "count_unit": count_unit,
        "guard": guard,
    }


def inventory() -> dict[str, Any]:
    """Every element on the data volume, biggest groups first, adding up to the volume's use."""
    from icici_breeze_backend.app.services.index_signal import backtest as signal_backtest
    from icici_breeze_backend.app.services.nsccl_baseline import span_archive_dir
    from icici_breeze_backend.app.core import log_sink
    from icici_breeze_backend.audit import bot_audit, strategy_builder_audit
    from icici_breeze_backend.app.services.storage import audit_retention, usage

    elements: list[dict[str, Any]] = []
    accounted: set[str] = set()

    # --- backtest cache
    cpath = cache_path()
    cache = cache_breakdown(cpath)
    tables, cov = cache["tables"], cache["coverage"]
    for suffix in ("", "-wal", "-journal", "-shm"):
        accounted.add(os.path.abspath(cpath + suffix))

    def tbytes(*names: str) -> int:
        return sum(tables.get(n, {}).get("bytes", 0) for n in names)

    def series_span(rows: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "from": min((r["from"] for r in rows), default=None),
            "to": max((r["to"] for r in rows), default=None),
            "days": max((r["days"] for r in rows), default=0),
        }

    fut = cov.get("futures_candles", [])
    elements.append(_element(
        "futures_bars", "Futures history", "Backtest history", bytes_=tbytes("futures_candles"),
        approx=True, deletable=True, coverage=series_span(fut), series=fut,
        description="One-minute NIFTY / SENSEX futures bars. Every bot backtest, every signal "
                    "backtest and the live navbar signal's warm-up read these.",
        guard=f"Bars from {_day_label(protected_futures_from())} on are kept: the live signal "
              "warms up from the last two sessions.",
    ))
    spot = cov.get("spot_candles", [])
    elements.append(_element(
        "spot_bars", "Cash-index history", "Backtest history", bytes_=tbytes("spot_candles"),
        approx=True, deletable=True, coverage=series_span(spot), series=spot,
        description="One-minute NIFTY / SENSEX cash-index bars, used by bot backtests.",
    ))
    elements.append(_element(
        "daily_vix", "Daily VIX", "Backtest history", bytes_=tbytes("daily_vix"), approx=True,
        deletable=True, coverage=cov.get("daily_vix"),
        description="One India VIX close per day, used by bot backtests' VIX filters.",
    ))
    opt = cov.get("option_candles") or {}
    elements.append(_element(
        "option_bars", "Option candles", "Backtest history", bytes_=tbytes("option_candles"),
        approx=True, deletable=True, coverage=opt, count=opt.get("contracts"), count_unit="contracts",
        description="Option contract bars fetched for bot backtests; the dates are those of the "
                    "windows fetched. Deleting a date range also forgets which windows were fetched "
                    "there, so a later backtest fetches them again. The cache also trims itself, "
                    "oldest expiry first, past 2 GB.",
    ))

    # --- bot backtest runs: rows in the cache + their audit trails
    bt_files = _dir_files(bot_audit.backtest_dir())
    for p, _s, _m in bt_files:
        accounted.add(os.path.abspath(p))
    runs_cov = cov.get("backtest_runs") or {}
    elements.append(_element(
        "bot_backtest_runs", "Bot backtest runs", "Backtest results",
        bytes_=tbytes("backtest_runs") + sum(s for _p, s, _m in bt_files), approx=True, deletable=True,
        coverage=runs_cov, count=runs_cov.get("runs"), count_unit="runs",
        description="Each bot backtest's settings, summary and trades, plus its downloadable audit "
                    "trail. Deleting by date removes runs started on those days; a run still "
                    "running is never deleted.",
    ))

    # --- signal backtest zips
    zips = []
    for run in signal_backtest.list_runs(limit=100000):
        path = run.get("zip_path")
        if path and os.path.exists(str(path)):
            zips.append((str(path), os.path.getsize(str(path)), str(run.get("triggered_at") or "")[:10]))
    sig_dir_files = _dir_files(signal_backtest.runs_dir())
    for p, _s, _m in sig_dir_files:
        accounted.add(os.path.abspath(p))
    zip_days = [datetime.date.fromisoformat(d) for _p, _s, d in zips if d]
    elements.append(_element(
        "signal_backtest_zips", "Signal backtest downloads", "Backtest results",
        bytes_=sum(s for _p, s, _m in sig_dir_files), deletable=True, coverage=_span(zip_days),
        count=len(zips), count_unit="runs",
        description="The per-run zip (readings, calls, bars) behind each signal backtest. The newest "
                    "30 are kept automatically. The run's summary row stays, because the 30-day bot "
                    "gate reads it.",
    ))

    # --- logs
    log_files = _dir_files(log_sink.logs_dir())
    for p, _s, _m in log_files:
        accounted.add(os.path.abspath(p))
    log_days = [_mtime_day(m) for _p, _s, m in log_files]
    elements.append(_element(
        "app_logs", "Application logs", "Logs", bytes_=sum(s for _p, s, _m in log_files),
        deletable=True, coverage=_span(log_days), count=len(log_files), count_unit="files",
        description="Backend and chain-builder logs (Settings → Application Logs). Capped at "
                    "about 40 MB and 7 days automatically.",
        guard="The files being written now are kept; only rotated files are deleted.",
    ))
    audit_root = bot_audit.audit_dir()
    bot_logs = [(p, s, m) for p, s, m in _dir_files(audit_root, recursive=False)]
    for p, _s, _m in bot_logs:
        accounted.add(os.path.abspath(p))
    bot_days = [d for d in (_bot_audit_day(os.path.basename(p)) for p, _s, _m in bot_logs) if d]
    elements.append(_element(
        "bot_audit_logs", "Bot audit logs", "Logs", bytes_=sum(s for _p, s, _m in bot_logs),
        deletable=True, coverage=_span(bot_days), count=len(bot_logs), count_unit="files",
        description="Each bot's per-day decision trail (Settings → Bot Audit Logs). Kept 7 days "
                    "automatically.",
        guard="Today's files are kept; the bots are writing to them.",
    ))
    sb_files = _dir_files(strategy_builder_audit.audit_log_dir())
    for p, _s, _m in sb_files:
        accounted.add(os.path.abspath(p))
    sb_days = [d for d in (_strategy_audit_day(os.path.basename(p)) for p, _s, _m in sb_files) if d]
    elements.append(_element(
        "strategy_audit_logs", "Strategy Builder audit logs", "Logs",
        bytes_=sum(s for _p, s, _m in sb_files), deletable=True, coverage=_span(sb_days),
        count=len(sb_files), count_unit="files",
        description="One file per Strategy Builder session (Settings → Audit Logs). The newest "
                    "10 per user are kept automatically.",
    ))

    # --- the request log inside users.sqlite3 (its file is accounted with the database below)
    users_path = _data(cfg.USERS_DB)
    users_free = audit_retention.free_bytes(users_path)
    noise = audit_retention.noise_breakdown(users_path)
    # An estimate, so capped at what the file holds live: the figures must still add up.
    noise_bytes = min(noise["bytes"], max(0, _file_bytes(users_path) - users_free))
    elements.append(_element(
        "audit_requests", "Request & page-view log", "Logs", bytes_=noise_bytes, approx=True,
        deletable=True, coverage={"from": noise["from"], "to": noise["to"]}, count=noise["rows"],
        count_unit="rows",
        description="One row per API call and page load, inside the accounts database. Nothing in "
                    "the app reads it; it is the request history for support beyond the application "
                    f"logs' 7 days. Kept {audit_retention.NOISE_KEEP_DAYS} days automatically.",
        guard="Only request and page-view rows are deleted. The trail of orders, square-off rules, "
              "GTT exits and bot actions stays.",
    ))

    # --- read-only
    elements.append(_element(
        "cache_bookkeeping", "Backtest cache bookkeeping", "Kept by the app",
        bytes_=tbytes("option_fetches", "option_needs", "meta"), approx=True, deletable=False,
        description="Which option windows were fetched or are still wanted, and what the app "
                    "learned about ICICI's history API. Trimmed along with option candles.",
    ))
    if cache["free_bytes"]:
        elements.append(_element(
            "cache_free_pages", "Deleted, not yet compacted", "Kept by the app",
            bytes_=cache["free_bytes"], deletable=False,
            description="Space inside the backtest cache that a delete freed but compaction has not "
                        "yet handed back to the volume. The next delete retries compaction.",
        ))
    span_files = _dir_files(span_archive_dir())
    for p, _s, _m in span_files:
        accounted.add(os.path.abspath(p))
    span_days = []
    for p, _s, _m in span_files:
        try:
            span_days.append(datetime.datetime.strptime(os.path.basename(os.path.dirname(p)), "%Y%m%d").date())
        except ValueError:
            continue
    elements.append(_element(
        "span_archives", "SPAN archives", "Kept by the app", bytes_=sum(s for _p, s, _m in span_files),
        deletable=False, coverage=_span(span_days), count=len(span_files), count_unit="files",
        description="The raw exchange SPAN files the margin comparison re-reads. The app keeps only "
                    "the latest revision per exchange for the two most recent dates.",
    ))
    for key, label, name, text, less in (
        ("users_db", "Accounts & settings database", cfg.USERS_DB,
         "Accounts, broker sessions, orders, bots, every setting, and the trail of orders, "
         "square-off rules, GTT exits and bot actions (kept "
         f"{audit_retention.EVENT_KEEP_DAYS} days). Never deleted here.",
         noise_bytes + users_free),
        ("scrips_db", "Scrip master database", "scrips.sqlite3",
         "ICICI's security master, rebuilt by the daily reference-data refresh.", 0),
    ):
        path = _data(name)
        for suffix in ("", "-wal", "-journal", "-shm"):
            accounted.add(os.path.abspath(path + suffix))
        elements.append(_element(key, label, "Kept by the app", bytes_=max(0, _file_bytes(path) - less),
                                 approx=bool(less), deletable=False, description=text))
    if users_free:
        elements.append(_element(
            "users_free_pages", "Accounts database: deleted, not yet compacted", "Kept by the app",
            bytes_=users_free, deletable=False,
            description="Space inside the accounts database that a delete or the daily trim freed but "
                        "compaction has not yet handed back. New rows reuse it; the next request-log "
                        "delete retries compaction.",
        ))

    other = sum(s for p, s, _m in _dir_files(_data()) if os.path.abspath(p) not in accounted)
    elements.append(_element(
        "other_files", "Other app files", "Kept by the app", bytes_=other, deletable=False,
        description="Freeze limits, the holiday calendar, empty database templates and anything else "
                    "in the data folder.",
    ))
    vol = usage.volume()
    if vol is not None:
        in_folder = sum(e["bytes"] for e in elements)
        elements.append(_element(
            "filesystem_other", "Filesystem & outside the app", "Kept by the app",
            bytes_=max(0, vol["used_bytes"] - in_folder), deletable=False,
            description="What the volume holds outside the app's data folder, plus the filesystem's "
                        "own bookkeeping.",
        ))
    return {"status": usage.status(), "elements": elements, "generated_at": now_ist().isoformat(timespec="seconds")}


# --------------------------------------------------------------------------------------
# Deletes
# --------------------------------------------------------------------------------------

#: Elements whose rows live in the backtest cache: deleting them needs the job slot free (a
#: running backtest is writing there) and is followed by compaction.
CACHE_ELEMENTS = frozenset({"futures_bars", "spot_bars", "daily_vix", "option_bars", "bot_backtest_runs"})
#: Elements whose rows live in `users.sqlite3`: a delete is followed by compacting that file.
USERS_DB_ELEMENTS = frozenset({"audit_requests"})


def _delete_bars(table: str, rng: DateRange, path: str) -> tuple[int, Optional[str]]:
    with sqlite3.connect(path, timeout=30) as conn:
        note = None
        end_before = rng.ts_before
        if table == "futures_candles":
            keep_from = protected_futures_from()
            if rng.start >= keep_from:
                return 0, (f"Nothing deleted: futures bars from {_day_label(keep_from)} on are kept for the "
                           "live signal's warm-up.")
            if rng.end >= keep_from:
                end_before = f"{keep_from.isoformat()} 00:00:00"
                note = f"Bars from {_day_label(keep_from)} on were kept for the live signal's warm-up."
        cur = conn.execute(f"DELETE FROM {table} WHERE ts >= ? AND ts < ?", (rng.ts_from, end_before))
        conn.commit()
        return cur.rowcount, note


def _delete_options(rng: DateRange, path: str) -> tuple[int, Optional[str]]:
    with sqlite3.connect(path, timeout=30) as conn:
        cur = conn.execute("DELETE FROM option_candles WHERE ts >= ? AND ts < ?", (rng.ts_from, rng.ts_before))
        # A fetch record that overlaps the range now describes bars that are gone; left in place
        # it would tell the next replay the window is cached and the gap would never be filled.
        conn.execute(
            "DELETE FROM option_fetches WHERE start_ts < ? AND end_ts >= ?", (rng.ts_before, rng.ts_from)
        )
        conn.commit()
        return cur.rowcount, None


def _delete_vix(rng: DateRange, path: str) -> tuple[int, Optional[str]]:
    with sqlite3.connect(path, timeout=30) as conn:
        cur = conn.execute(
            "DELETE FROM daily_vix WHERE date >= ? AND date <= ?", (rng.start.isoformat(), rng.end.isoformat())
        )
        conn.commit()
        return cur.rowcount, None


def _delete_bot_runs(rng: DateRange, path: str) -> tuple[int, Optional[str]]:
    from icici_breeze_backend.audit import bot_audit

    with sqlite3.connect(path, timeout=30) as conn:
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM backtest_runs WHERE substr(created_at, 1, 10) BETWEEN ? AND ? "
            "AND status != 'running'",
            (rng.start.isoformat(), rng.end.isoformat()),
        )]
        conn.executemany("DELETE FROM backtest_runs WHERE id = ?", [(i,) for i in ids])
        conn.commit()
    tokens = {bot_audit._safe_token(i, 40) for i in ids}  # noqa: SLF001
    for full, _s, _m in _dir_files(bot_audit.backtest_dir()):
        parsed = bot_audit._parse_name(os.path.basename(full))  # noqa: SLF001
        if parsed and parsed[2] in tokens:
            _remove(full)
    return len(ids), None


def _delete_signal_zips(rng: DateRange) -> tuple[int, Optional[str]]:
    from icici_breeze_backend.app.services.index_signal import backtest as signal_backtest

    removed = 0
    for run in signal_backtest.list_runs(limit=100000):
        day = str(run.get("triggered_at") or "")[:10]
        path = run.get("zip_path")
        if not path or not day or not rng.contains(datetime.date.fromisoformat(day)):
            continue
        _remove(str(path))
        signal_backtest.update_run(run["id"], zip_path=None)
        removed += 1
    return removed, None


def _delete_files(files: list[str], pick: Callable[[str], Optional[datetime.date]], rng: DateRange,
                  keep: Callable[[str], bool] = lambda _p: False) -> tuple[int, Optional[str]]:
    removed = 0
    for full in files:
        day = pick(full)
        if day is None or not rng.contains(day) or keep(full):
            continue
        if _remove(full):
            removed += 1
    return removed, None


def _remove(path: str) -> bool:
    try:
        os.remove(path)
        return True
    except OSError:
        return False


def delete(key: str, rng: DateRange) -> dict[str, Any]:
    """Delete one element's data within `rng`. Returns {"deleted": n, "unit": ..., "note": ...}.

    Compaction is the caller's (`cleanup`), so several deletes can share one."""
    from icici_breeze_backend.app.core import log_sink
    from icici_breeze_backend.audit import bot_audit, strategy_builder_audit

    path = cache_path()
    if key == "futures_bars":
        n, note = _delete_bars("futures_candles", rng, path)
        unit = "bars"
    elif key == "spot_bars":
        n, note = _delete_bars("spot_candles", rng, path)
        unit = "bars"
    elif key == "daily_vix":
        n, note = _delete_vix(rng, path)
        unit = "days"
    elif key == "option_bars":
        n, note = _delete_options(rng, path)
        unit = "bars"
    elif key == "bot_backtest_runs":
        n, note = _delete_bot_runs(rng, path)
        unit = "runs"
    elif key == "signal_backtest_zips":
        n, note = _delete_signal_zips(rng)
        unit = "downloads"
    elif key == "app_logs":
        files = [p for p, _s, _m in _dir_files(log_sink.logs_dir())]
        n, note = _delete_files(
            files, lambda p: _mtime_day(os.path.getmtime(p)), rng,
            keep=lambda p: not _rotated_log(os.path.basename(p)),
        )
        unit = "files"
    elif key == "bot_audit_logs":
        today = now_ist().date()
        files = [p for p, _s, _m in _dir_files(bot_audit.audit_dir(), recursive=False)]
        n, note = _delete_files(
            files, lambda p: _bot_audit_day(os.path.basename(p)), rng,
            keep=lambda p: _bot_audit_day(os.path.basename(p)) == today,
        )
        unit = "files"
    elif key == "audit_requests":
        from icici_breeze_backend.app.services.storage import audit_retention

        n, note = audit_retention.delete_noise(rng.start, rng.end, _data(cfg.USERS_DB)), None
        unit = "rows"
    elif key == "strategy_audit_logs":
        files = [p for p, _s, _m in _dir_files(strategy_builder_audit.audit_log_dir())]
        n, note = _delete_files(files, lambda p: _strategy_audit_day(os.path.basename(p)), rng)
        unit = "files"
    else:
        raise ValueError(f"{key!r} cannot be deleted from here.")
    if key in CACHE_ELEMENTS:
        invalidate_cache_memo()
    return {"deleted": n, "unit": unit, "note": note}
