"""Cached historical data for the backtests (docs/bots-scalping-plan.md section 8).

A standalone SQLite file, deliberately **not** `users.sqlite3`: this is bulk, regenerable,
disposable data, and it must never be able to corrupt or bloat the file that holds accounts,
credentials and the run log. Deleting it costs nothing but a re-fetch.

Fetching needs a live broker session, which only works from the production static IP, so
`fetch` runs on the EC2 instance. Replay is pure CPU and needs no broker at all, so it runs
on the same box over SSH -- no transfer, nothing to keep in sync.

Option candles are fetched **on demand** (section 8.7). A replay records the contracts it
needed and did not have as `option_needs`; `fetch-options` downloads exactly those and logs
each window it asked for in `option_fetches` -- including windows that came back empty, so
"ICICI has nothing for this contract" is an answer the replay can act on rather than a need
that is re-requested forever.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import sqlite3
from dataclasses import dataclass
from typing import Any, Iterable, Optional

import icici_breeze_backend.app.core.config as cfg

_logger = logging.getLogger(__name__)

BACKTEST_DB = "backtest.sqlite3"

INTERVAL_MINUTE = "1minute"
INTERVAL_SECOND = "1second"

# A complete NSE session is 09:15-15:29, 375 one-minute bars. A day short of this is either a
# half day or a truncated fetch, and `coverage` lists it so the reader can tell which. Futures
# days also carry pre-open (09:00-09:08) and post-close (to 15:39) bars, up to ~395 in all
# (verified 2026-09-15); the check is a floor, so the extra bars never mark a day incomplete.
SESSION_BARS = 375
COMPLETE_DAY_BARS = 370

_UNDERLYING_TABLES = ("futures_candles", "spot_candles")
_TS = "%Y-%m-%d %H:%M:%S"


@dataclass(frozen=True)
class HistCandle:
    """One bar, as stored. `ts` is the bar's START, naive IST."""

    ts: datetime.datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    #: Open interest as the bar closed, for the expansion mechanism's OI quadrant (#34).
    #: None where the series has none: ICICI serves OI 0 on pre-open bars and on *every* BSE
    #: bar, and a zero used as a window anchor reads as the largest OI rise ever recorded.
    oi: Optional[int] = None

    @property
    def date(self) -> datetime.date:
        return self.ts.date()


@dataclass(frozen=True)
class OptionKey:
    stock_code: str
    expiry: datetime.date
    strike: float
    right: str  # "call" | "put"

    def label(self) -> str:
        return (
            f"{self.stock_code} {self.expiry:%d-%b-%Y} {int(self.strike)} "
            f"{'CE' if self.right == 'call' else 'PE'}"
        )


@dataclass(frozen=True)
class Need:
    """One window of one contract that a replay wanted and the cache did not have."""

    key: OptionKey
    interval: str
    start: datetime.datetime
    end: datetime.datetime


def db_path() -> str:
    return cfg.DATA_PATH + BACKTEST_DB


def _connect(path: Optional[str]) -> sqlite3.Connection:
    return sqlite3.connect(path or db_path())


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def ensure_tables(path: Optional[str] = None) -> None:
    with _connect(path) as conn:
        for table in _UNDERLYING_TABLES:
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {table} (
                    stock_code TEXT NOT NULL,
                    ts TEXT NOT NULL,
                    open REAL, high REAL, low REAL, close REAL,
                    volume INTEGER,
                    PRIMARY KEY (stock_code, ts)
                )
                """
            )
            # The primary key makes re-fetching an overlapping range idempotent, which
            # matters: a fetch that dies halfway is resumed by simply running it again.
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_ts ON {table}(ts)")
        # Which monthly contract a futures bar came from. Added after the first release, so
        # it goes on as an ALTER for a cache that already exists.
        if "expiry" not in _columns(conn, "futures_candles"):
            conn.execute("ALTER TABLE futures_candles ADD COLUMN expiry TEXT")
        # Open interest, for the expansion mechanism (#34). Added after the futures cache
        # already existed, so it goes on as an ALTER like `expiry` did.
        for table in _UNDERLYING_TABLES:
            if "oi" not in _columns(conn, table):
                conn.execute(f"ALTER TABLE {table} ADD COLUMN oi INTEGER")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_vix (
                date TEXT PRIMARY KEY NOT NULL,
                value REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS option_candles (
                stock_code TEXT NOT NULL,
                expiry TEXT NOT NULL,
                strike REAL NOT NULL,
                right TEXT NOT NULL,
                interval TEXT NOT NULL,
                ts TEXT NOT NULL,
                open REAL, high REAL, low REAL, close REAL,
                volume INTEGER,
                oi INTEGER,
                PRIMARY KEY (stock_code, expiry, strike, right, interval, ts)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS option_fetches (
                stock_code TEXT NOT NULL,
                expiry TEXT NOT NULL,
                strike REAL NOT NULL,
                right TEXT NOT NULL,
                interval TEXT NOT NULL,
                start_ts TEXT NOT NULL,
                end_ts TEXT NOT NULL,
                rows INTEGER NOT NULL,
                fetched_at TEXT NOT NULL,
                PRIMARY KEY (stock_code, expiry, strike, right, interval, start_ts, end_ts)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS option_needs (
                stock_code TEXT NOT NULL,
                expiry TEXT NOT NULL,
                strike REAL NOT NULL,
                right TEXT NOT NULL,
                interval TEXT NOT NULL,
                start_ts TEXT NOT NULL,
                end_ts TEXT NOT NULL,
                PRIMARY KEY (stock_code, expiry, strike, right, interval, start_ts, end_ts)
            )
            """
        )
        # What the probe learned about ICICI's API (per-call cap, request clock). Read by the
        # fetcher so a fact measured once is not re-guessed on every run.
        conn.execute(
            "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY NOT NULL, value TEXT)"
        )
        # Replays run from Bots -> Backtest, kept so variations can be compared later. Each row
        # carries the settings it ran on, because the bot's settings move on after it.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS backtest_runs (
                id TEXT PRIMARY KEY NOT NULL,
                user_id TEXT NOT NULL,
                bot TEXT NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL,
                params TEXT NOT NULL,
                summary TEXT,
                trades TEXT,
                error TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_backtest_runs_user ON backtest_runs(user_id, created_at)"
        )
        conn.commit()


# --------------------------------------------------------------------------------------
# Underlying bars: futures (the Bot 3 signal) and the cash index (spot)
# --------------------------------------------------------------------------------------


def _check_table(table: str) -> None:
    if table not in _UNDERLYING_TABLES:
        raise ValueError(f"unknown underlying table {table!r}")


def _bar_oi(row: dict[str, Any]) -> Optional[int]:
    """Open interest from an ICICI bar, or None when it is absent.

    ICICI serves `open_interest: 0` on pre-open bars and on every BSE contract (measured
    2026-09-16 with a working NSE control -- see #34), so a non-positive value is stored as
    NULL rather than as a reading of zero."""
    try:
        value = int(float(row.get("open_interest") or 0))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _bar_tuple(row: dict[str, Any]) -> Optional[tuple]:
    ts = _parse_ts(row.get("datetime") or row.get("date"))
    if ts is None:
        return None
    try:
        return (
            ts.strftime(_TS),
            float(row.get("open") or 0),
            float(row.get("high") or 0),
            float(row.get("low") or 0),
            float(row.get("close") or 0),
            int(float(row.get("volume") or 0)),
            _bar_oi(row),
        )
    except (TypeError, ValueError):
        return None


def store_candles(
    rows: Iterable[dict[str, Any]],
    *,
    stock_code: str = "NIFTY",
    path: Optional[str] = None,
    table: str = "futures_candles",
    expiry: Optional[datetime.date] = None,
) -> int:
    _check_table(table)
    payload = [(stock_code,) + t for t in (_bar_tuple(r) for r in rows) if t is not None]
    if not payload:
        return 0
    with _connect(path) as conn:
        if table == "futures_candles":
            conn.executemany(
                "INSERT OR REPLACE INTO futures_candles "
                "(stock_code, ts, open, high, low, close, volume, oi, expiry) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [p + (expiry.isoformat() if expiry else None,) for p in payload],
            )
        else:
            conn.executemany(
                f"INSERT OR REPLACE INTO {table} "
                "(stock_code, ts, open, high, low, close, volume, oi) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                payload,
            )
        conn.commit()
    return len(payload)


def load_candles(
    *,
    stock_code: str = "NIFTY",
    from_date: Optional[datetime.date] = None,
    to_date: Optional[datetime.date] = None,
    path: Optional[str] = None,
    table: str = "futures_candles",
) -> list[HistCandle]:
    _check_table(table)
    sql = f"SELECT ts, open, high, low, close, volume, oi FROM {table} WHERE stock_code = ?"
    args: list[Any] = [stock_code]
    if from_date:
        sql += " AND ts >= ?"
        args.append(f"{from_date.isoformat()} 00:00:00")
    if to_date:
        sql += " AND ts <= ?"
        args.append(f"{to_date.isoformat()} 23:59:59")
    sql += " ORDER BY ts ASC"
    with _connect(path) as conn:
        rows = conn.execute(sql, args).fetchall()
    return _to_candles(rows)


def day_bar_counts(
    *, stock_code: str = "NIFTY", table: str = "futures_candles", path: Optional[str] = None
) -> dict[datetime.date, int]:
    _check_table(table)
    with _connect(path) as conn:
        rows = conn.execute(
            f"SELECT substr(ts, 1, 10), COUNT(*) FROM {table} WHERE stock_code = ? GROUP BY 1",
            (stock_code,),
        ).fetchall()
    out: dict[datetime.date, int] = {}
    for day, count in rows:
        try:
            out[datetime.date.fromisoformat(day)] = int(count)
        except (TypeError, ValueError):
            continue
    return out


def _to_candles(rows: Iterable[tuple]) -> list[HistCandle]:
    out: list[HistCandle] = []
    for ts, o, h, l, c, v, oi in rows:
        parsed = _parse_ts(ts)
        if parsed is None:
            continue
        # A stored 0 or NULL is absent, never a reading of zero (#34).
        oi_value = int(oi) if oi is not None and int(oi) > 0 else None
        out.append(
            HistCandle(parsed, float(o), float(h), float(l), float(c), int(v or 0), oi_value)
        )
    return out


def store_vix(rows: Iterable[dict[str, Any]], *, path: Optional[str] = None) -> int:
    payload = []
    for row in rows:
        date = str(row.get("date") or "")[:10]
        try:
            value = float(row.get("value"))
        except (TypeError, ValueError):
            continue
        if date and value > 0:
            payload.append((date, value))
    if not payload:
        return 0
    with _connect(path) as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO daily_vix (date, value) VALUES (?, ?)", payload
        )
        conn.commit()
    return len(payload)


def load_vix(*, path: Optional[str] = None) -> dict[datetime.date, float]:
    with _connect(path) as conn:
        rows = conn.execute("SELECT date, value FROM daily_vix").fetchall()
    out: dict[datetime.date, float] = {}
    for date, value in rows:
        try:
            out[datetime.date.fromisoformat(str(date)[:10])] = float(value)
        except (TypeError, ValueError):
            continue
    return out


# --------------------------------------------------------------------------------------
# Option bars, and the fetch/need bookkeeping behind them
# --------------------------------------------------------------------------------------


def _key_args(key: OptionKey, interval: str) -> tuple:
    return (key.stock_code, key.expiry.isoformat(), float(key.strike), key.right, interval)


def store_option_candles(
    rows: Iterable[dict[str, Any]], key: OptionKey, interval: str, *, path: Optional[str] = None
) -> int:
    payload = []
    for row in rows:
        bar = _bar_tuple(row)
        if bar is None:
            continue
        # `_bar_tuple` already carries OI in its last slot, normalised so a non-positive
        # value is NULL rather than a reading of zero (#34) -- the option row shape puts it
        # in the same place, so the tuple goes in whole.
        payload.append(_key_args(key, interval) + bar)
    if not payload:
        return 0
    with _connect(path) as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO option_candles "
            "(stock_code, expiry, strike, right, interval, ts, open, high, low, close, volume, oi) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            payload,
        )
        conn.commit()
    return len(payload)


def load_option_bars(
    key: OptionKey,
    interval: str,
    start: datetime.datetime,
    end: datetime.datetime,
    *,
    path: Optional[str] = None,
) -> list[HistCandle]:
    with _connect(path) as conn:
        rows = conn.execute(
            "SELECT ts, open, high, low, close, volume, oi FROM option_candles "
            "WHERE stock_code = ? AND expiry = ? AND strike = ? AND right = ? AND interval = ? "
            "AND ts >= ? AND ts <= ? ORDER BY ts ASC",
            _key_args(key, interval) + (start.strftime(_TS), end.strftime(_TS)),
        ).fetchall()
    return _to_candles(rows)


def record_fetch(need: Need, rows: int, *, path: Optional[str] = None) -> None:
    now = datetime.datetime.now().strftime(_TS)
    with _connect(path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO option_fetches "
            "(stock_code, expiry, strike, right, interval, start_ts, end_ts, rows, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            _key_args(need.key, need.interval)
            + (need.start.strftime(_TS), need.end.strftime(_TS), int(rows), now),
        )
        conn.execute(
            "DELETE FROM option_needs WHERE stock_code = ? AND expiry = ? AND strike = ? "
            "AND right = ? AND interval = ? AND start_ts = ? AND end_ts = ?",
            _key_args(need.key, need.interval)
            + (need.start.strftime(_TS), need.end.strftime(_TS)),
        )
        conn.commit()


def fetched(need: Need, *, path: Optional[str] = None) -> bool:
    """True when some earlier fetch already covered this whole window."""
    with _connect(path) as conn:
        row = conn.execute(
            "SELECT 1 FROM option_fetches WHERE stock_code = ? AND expiry = ? AND strike = ? "
            "AND right = ? AND interval = ? AND start_ts <= ? AND end_ts >= ? LIMIT 1",
            _key_args(need.key, need.interval)
            + (need.start.strftime(_TS), need.end.strftime(_TS)),
        ).fetchone()
    return row is not None


def add_needs(needs: Iterable[Need], *, path: Optional[str] = None) -> int:
    """Queue what a replay was missing. Returns how many were new."""
    added = 0
    with _connect(path) as conn:
        for need in needs:
            cur = conn.execute(
                "INSERT OR IGNORE INTO option_needs "
                "(stock_code, expiry, strike, right, interval, start_ts, end_ts) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                _key_args(need.key, need.interval)
                + (need.start.strftime(_TS), need.end.strftime(_TS)),
            )
            added += cur.rowcount or 0
        conn.commit()
    return added


def pending_needs(*, path: Optional[str] = None, limit: Optional[int] = None) -> list[Need]:
    sql = (
        "SELECT stock_code, expiry, strike, right, interval, start_ts, end_ts FROM option_needs "
        "ORDER BY expiry, start_ts, strike, right"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    with _connect(path) as conn:
        rows = conn.execute(sql).fetchall()
    out = []
    for stock, expiry, strike, right, interval, start, end in rows:
        out.append(
            Need(
                OptionKey(stock, datetime.date.fromisoformat(expiry), float(strike), right),
                interval,
                datetime.datetime.strptime(start, _TS),
                datetime.datetime.strptime(end, _TS),
            )
        )
    return out


def set_meta(key: str, value: str, *, path: Optional[str] = None) -> None:
    with _connect(path) as conn:
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, str(value)))
        conn.commit()


def get_meta(key: str, *, path: Optional[str] = None) -> Optional[str]:
    with _connect(path) as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def coverage(*, stock_code: str = "NIFTY", path: Optional[str] = None) -> dict[str, Any]:
    """What is actually cached -- printed before a replay so a short run is never a surprise."""
    with _connect(path) as conn:
        row = conn.execute(
            "SELECT COUNT(*), MIN(ts), MAX(ts) FROM futures_candles WHERE stock_code = ?",
            (stock_code,),
        ).fetchone()
        spot = conn.execute(
            "SELECT COUNT(*), MIN(ts), MAX(ts) FROM spot_candles WHERE stock_code = ?",
            (stock_code,),
        ).fetchone()
        vix = conn.execute("SELECT COUNT(*), MIN(date), MAX(date) FROM daily_vix").fetchone()
        options = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT stock_code || expiry || strike || right) "
            "FROM option_candles"
        ).fetchone()
        empty = conn.execute("SELECT COUNT(*) FROM option_fetches WHERE rows = 0").fetchone()
        needs = conn.execute("SELECT COUNT(*) FROM option_needs").fetchone()
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
    short_days = {
        d.isoformat(): n
        for d, n in sorted(day_bar_counts(stock_code=stock_code, path=path).items())
        if n < COMPLETE_DAY_BARS
    }
    return {
        "candles": int(row[0] or 0),
        "candles_from": row[1],
        "candles_to": row[2],
        # Either a half day or a truncated fetch; re-running `fetch` refills the latter.
        "short_futures_days": short_days,
        "spot_candles": int(spot[0] or 0),
        "spot_from": spot[1],
        "spot_to": spot[2],
        "vix_days": int(vix[0] or 0),
        "vix_from": vix[1],
        "vix_to": vix[2],
        "option_bars": int(options[0] or 0),
        "option_contracts": int(options[1] or 0),
        "option_windows_empty": int(empty[0] or 0),
        "option_needs_pending": int(needs[0] or 0),
        "probe": {k: v for k, v in meta.items()},
    }


def latest_close(
    stock_code: str, *, table: str = "spot_candles", path: Optional[str] = None
) -> Optional[float]:
    _check_table(table)
    with _connect(path) as conn:
        row = conn.execute(
            f"SELECT close FROM {table} WHERE stock_code = ? AND close > 0 ORDER BY ts DESC LIMIT 1",
            (stock_code,),
        ).fetchone()
    return float(row[0]) if row else None


# --------------------------------------------------------------------------------------
# Saved runs
# --------------------------------------------------------------------------------------


def save_run(run: dict[str, Any], *, path: Optional[str] = None) -> None:
    with _connect(path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO backtest_runs "
            "(id, user_id, bot, created_at, status, params, summary, trades, error) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run["id"],
                run["user_id"],
                run["bot"],
                run["created_at"],
                run["status"],
                json.dumps(run.get("params") or {}, default=str),
                json.dumps(run["summary"], default=str) if run.get("summary") is not None else None,
                json.dumps(run["trades"], default=str) if run.get("trades") is not None else None,
                run.get("error"),
            ),
        )
        conn.commit()


def _run_row(row: sqlite3.Row, *, with_trades: bool) -> dict[str, Any]:
    out = {
        "id": row["id"],
        "bot": row["bot"],
        "created_at": row["created_at"],
        "status": row["status"],
        "params": json.loads(row["params"] or "{}"),
        "summary": json.loads(row["summary"]) if row["summary"] else None,
        "error": row["error"],
    }
    if with_trades:
        out["trades"] = json.loads(row["trades"]) if row["trades"] else []
    return out


def list_runs(user_id: str, *, limit: int = 50, path: Optional[str] = None) -> list[dict[str, Any]]:
    with _connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, bot, created_at, status, params, summary, error FROM backtest_runs "
            "WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, int(limit)),
        ).fetchall()
    return [_run_row(r, with_trades=False) for r in rows]


def get_run(run_id: str, user_id: str, *, path: Optional[str] = None) -> Optional[dict[str, Any]]:
    with _connect(path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM backtest_runs WHERE id = ? AND user_id = ?", (run_id, user_id)
        ).fetchone()
    return _run_row(row, with_trades=True) if row else None


def delete_run(run_id: str, user_id: str, *, path: Optional[str] = None) -> bool:
    with _connect(path) as conn:
        cur = conn.execute(
            "DELETE FROM backtest_runs WHERE id = ? AND user_id = ? AND status != 'running'",
            (run_id, user_id),
        )
        conn.commit()
    return bool(cur.rowcount)


def fail_unfinished_runs(*, path: Optional[str] = None) -> int:
    """Runs left `running` by a restart can never finish; say so rather than spin forever."""
    with _connect(path) as conn:
        cur = conn.execute(
            "UPDATE backtest_runs SET status = 'failed', "
            "error = 'Interrupted: the app restarted while this run was in progress.' "
            "WHERE status = 'running'"
        )
        conn.commit()
    return cur.rowcount or 0


def _parse_ts(raw: Any) -> Optional[datetime.datetime]:
    """ICICI returns several shapes; accept the ones actually observed."""
    if isinstance(raw, datetime.datetime):
        return raw
    text = str(raw or "").strip()
    if not text:
        return None
    text = text.replace("Z", "").replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(text[: len(fmt) + 6], fmt)
        except ValueError:
            continue
    return None


# --------------------------------------------------------------------------------------
# The daily call budget and the size cap (#36)
# --------------------------------------------------------------------------------------

#: What the daily call budget ships as. Measured 2026-09-17: a month of one bot is ~70-90 calls
#: and six months ~460-550, so this admits any single run up to about six months and still stops
#: a day of stacked long runs well short of the per-minute and per-day limits the live bots
#: depend on. There is no bulk pre-cache; data is fetched only when a backtest needs it, and kept.
#:
#: This is now the *default*, not the ceiling: the live value is a setting
#: (`services.backtest_budget`), so a day with the ICICI allowance to spare can be given a bigger
#: one. Read it with `daily_call_budget()`; this constant is only the fallback.
DAILY_CALL_BUDGET = 800

#: The cache's ceiling on disk. Deployments run on an 8 GiB data volume shared with
#: `users.sqlite3`, `scrips.sqlite3` and the audit trails; a replay-driven cache of a dozen bots
#: over several periods measures in the low hundreds of MB, so this is a backstop, not a
#: working limit.
MAX_CACHE_BYTES = 2 * 1024**3


def _calls_key(day: datetime.date) -> str:
    return f"calls:{day.isoformat()}"


def calls_spent(day: datetime.date, *, path: Optional[str] = None) -> int:
    raw = get_meta(_calls_key(day), path=path)
    try:
        return max(0, int(raw)) if raw else 0
    except ValueError:
        return 0


def add_calls(day: datetime.date, calls: int, *, path: Optional[str] = None) -> int:
    total = calls_spent(day, path=path) + max(0, int(calls))
    set_meta(_calls_key(day), str(total), path=path)
    return total


def daily_call_budget() -> int:
    """The configured ceiling for one IST day. Imported lazily so this module -- which the
    replay half uses, and which runs with no app settings DB in tests -- keeps working when
    `users.sqlite3` is absent."""
    try:
        from icici_breeze_backend.app.services.backtest_budget import get_daily_call_budget

        return get_daily_call_budget()
    except Exception:  # noqa: BLE001 - a missing/locked settings DB must not stop a replay
        return DAILY_CALL_BUDGET


def calls_remaining(day: datetime.date, *, path: Optional[str] = None) -> int:
    return max(0, daily_call_budget() - calls_spent(day, path=path))


def cache_bytes(path: Optional[str] = None) -> int:
    target = path or db_path()
    total = 0
    for suffix in ("", "-wal", "-journal"):
        try:
            total += os.path.getsize(target + suffix)
        except OSError:
            pass
    return total


def enforce_cache_cap(
    *, max_bytes: int = MAX_CACHE_BYTES, path: Optional[str] = None
) -> dict[str, Any]:
    """Evict option history, oldest expiry first, until the file is under `max_bytes`.

    Futures, cash-index and VIX bars are never evicted: they are small (~15 MB for six months of
    both indices) and every bot and every signal replay needs them. Option bars are the bulk and
    are per contract, so dropping a whole expired contract series is clean -- its fetch records
    and needs go with it, so a later backtest that wants that expiry simply fetches it again.
    """
    evicted: list[str] = []
    if cache_bytes(path) <= max_bytes:
        return {"evicted_expiries": evicted, "bytes": cache_bytes(path)}
    while cache_bytes(path) > max_bytes:
        with _connect(path) as conn:
            row = conn.execute("SELECT MIN(expiry) FROM option_candles").fetchone()
            oldest = row[0] if row else None
            if oldest is None:
                break
            for table in ("option_candles", "option_fetches", "option_needs"):
                conn.execute(f"DELETE FROM {table} WHERE expiry = ?", (oldest,))
            conn.commit()
        evicted.append(oldest)
        with _connect(path) as conn:
            conn.execute("VACUUM")
    if evicted:
        _logger.info("backtest cache: evicted %d expiry series to stay under the cap", len(evicted))
    return {"evicted_expiries": evicted, "bytes": cache_bytes(path)}
