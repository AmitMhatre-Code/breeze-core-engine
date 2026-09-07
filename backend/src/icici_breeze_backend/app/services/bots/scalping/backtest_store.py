"""Cached historical data for the backtest (docs/bots-scalping-plan.md section 8).

A standalone SQLite file, deliberately **not** `users.sqlite3`: this is bulk, regenerable,
disposable data, and it must never be able to corrupt or bloat the file that holds accounts,
credentials and the run log. Deleting it costs nothing but a re-fetch.

Fetching needs a live broker session, which only works from the production static IP, so
`fetch` runs on the EC2 instance. Replay is pure CPU and needs no broker at all, so it runs
on the same box over SSH -- no transfer, nothing to keep in sync.
"""
from __future__ import annotations

import datetime
import logging
import sqlite3
from dataclasses import dataclass
from typing import Any, Iterable, Optional

import icici_breeze_backend.app.core.config as cfg

_logger = logging.getLogger(__name__)

BACKTEST_DB = "backtest.sqlite3"


@dataclass(frozen=True)
class HistCandle:
    """One 1-minute futures bar, as stored."""

    ts: datetime.datetime
    open: float
    high: float
    low: float
    close: float
    volume: int

    @property
    def date(self) -> datetime.date:
        return self.ts.date()


def db_path() -> str:
    return cfg.DATA_PATH + BACKTEST_DB


def ensure_tables(path: Optional[str] = None) -> None:
    with sqlite3.connect(path or db_path()) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS futures_candles (
                stock_code TEXT NOT NULL,
                ts TEXT NOT NULL,
                open REAL, high REAL, low REAL, close REAL,
                volume INTEGER,
                PRIMARY KEY (stock_code, ts)
            )
            """
        )
        # The primary key makes re-fetching an overlapping range idempotent, which matters:
        # a fetch that dies halfway is resumed by simply running it again.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_futures_candles_ts ON futures_candles(ts)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_vix (
                date TEXT PRIMARY KEY NOT NULL,
                value REAL NOT NULL
            )
            """
        )
        conn.commit()


def store_candles(rows: Iterable[dict[str, Any]], *, stock_code: str = "NIFTY", path: Optional[str] = None) -> int:
    payload = []
    for row in rows:
        ts = _parse_ts(row.get("datetime") or row.get("date"))
        if ts is None:
            continue
        try:
            payload.append(
                (
                    stock_code,
                    ts.strftime("%Y-%m-%d %H:%M:%S"),
                    float(row.get("open") or 0),
                    float(row.get("high") or 0),
                    float(row.get("low") or 0),
                    float(row.get("close") or 0),
                    int(float(row.get("volume") or 0)),
                )
            )
        except (TypeError, ValueError):
            continue
    if not payload:
        return 0
    with sqlite3.connect(path or db_path()) as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO futures_candles "
            "(stock_code, ts, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, ?)",
            payload,
        )
        conn.commit()
    return len(payload)


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
    with sqlite3.connect(path or db_path()) as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO daily_vix (date, value) VALUES (?, ?)", payload
        )
        conn.commit()
    return len(payload)


def load_candles(
    *,
    stock_code: str = "NIFTY",
    from_date: Optional[datetime.date] = None,
    to_date: Optional[datetime.date] = None,
    path: Optional[str] = None,
) -> list[HistCandle]:
    sql = "SELECT ts, open, high, low, close, volume FROM futures_candles WHERE stock_code = ?"
    args: list[Any] = [stock_code]
    if from_date:
        sql += " AND ts >= ?"
        args.append(f"{from_date.isoformat()} 00:00:00")
    if to_date:
        sql += " AND ts <= ?"
        args.append(f"{to_date.isoformat()} 23:59:59")
    sql += " ORDER BY ts ASC"
    with sqlite3.connect(path or db_path()) as conn:
        rows = conn.execute(sql, args).fetchall()
    out: list[HistCandle] = []
    for ts, o, h, l, c, v in rows:
        parsed = _parse_ts(ts)
        if parsed is None:
            continue
        out.append(HistCandle(parsed, float(o), float(h), float(l), float(c), int(v or 0)))
    return out


def load_vix(*, path: Optional[str] = None) -> dict[datetime.date, float]:
    with sqlite3.connect(path or db_path()) as conn:
        rows = conn.execute("SELECT date, value FROM daily_vix").fetchall()
    out: dict[datetime.date, float] = {}
    for date, value in rows:
        try:
            out[datetime.date.fromisoformat(str(date)[:10])] = float(value)
        except (TypeError, ValueError):
            continue
    return out


def coverage(*, stock_code: str = "NIFTY", path: Optional[str] = None) -> dict[str, Any]:
    """What is actually cached -- printed before a replay so a short run is never a surprise."""
    with sqlite3.connect(path or db_path()) as conn:
        row = conn.execute(
            "SELECT COUNT(*), MIN(ts), MAX(ts) FROM futures_candles WHERE stock_code = ?",
            (stock_code,),
        ).fetchone()
        vix = conn.execute("SELECT COUNT(*), MIN(date), MAX(date) FROM daily_vix").fetchone()
    return {
        "candles": int(row[0] or 0),
        "candles_from": row[1],
        "candles_to": row[2],
        "vix_days": int(vix[0] or 0),
        "vix_from": vix[1],
        "vix_to": vix[2],
    }


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
