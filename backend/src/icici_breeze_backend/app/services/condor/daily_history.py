"""NIFTY option closing prices from NSE's public archive, for the condor's long-history backtest.

docs/condor-daily-history-plan.md. ICICI's option history starts 5 Jan 2026 -- about eight monthly
cycles, none of them a crash. NSE publishes every session's F&O closing prices, back well before
2020, as plain files:

    to 5 Jul 2024     content/historical/DERIVATIVES/{YYYY}/{MON}/fo{DD}{MON}{YYYY}bhav.csv.zip
    from 5 Jul 2024   content/fo/BhavCopy_NSE_FO_0_0_0_{YYYYMMDD}_F_0000.csv.zip   (UDiFF)
    every session     content/indices/ind_close_all_{DDMMYYYY}.csv                  (index closes)

Only NIFTY index options **that traded** that session are kept. A contract that did not trade still
appears with a close, but a stale one -- seen 2026-09-29: a 21,200 call closing at 3,038.10 on no
volume with the index at 22,716, against an intrinsic value of 1,516. Kept, it would mark a leg at a
price that never existed. Expiries more than `MAX_DTE` days out and strikes beyond `STRIKE_BAND` of
the index are dropped: the condor never trades them, and they would triple the store.

Holidays are recognised by NSE returning 404 for both files, not from a calendar: the app's
exchange calendar does not reach back to 2020. Fetching is resumable -- a session recorded as
fetched (or as no session) is never asked for again -- and errors are not recorded, so they are
retried by the next run.
"""
from __future__ import annotations

import csv
import datetime
import io
import logging
import sqlite3
import time
import zipfile
from typing import Any, Callable, Iterable, Optional

_logger = logging.getLogger(__name__)

#: The first session the long-history backtest offers (the user's choice, 2026-10-09).
HISTORY_START = datetime.date(2020, 1, 1)
#: The first session served only in UDiFF; the old format ends 5 Jul 2024.
UDIFF_FROM = datetime.date(2024, 7, 8)
SYMBOL = "NIFTY"
INDEX_NAME = "NIFTY 50"
#: Expiries further out than this are not kept: no condor setting enters beyond ~95 DTE.
MAX_DTE = 100
#: Strikes further from the index close than this share are not kept (the replay's chain spans 15%).
STRIKE_BAND = 0.18
#: Pause between requests: NSE's archive is a public courtesy, not an API.
REQUEST_PAUSE_SECONDS = 0.6

_ARCHIVE = "https://nsearchives.nseindia.com"
OK = "ok"
NO_FILE = "no_file"
ERROR = "error"

Row = tuple[str, str, float, str, Optional[float], float, int, int]


def _connect(path: Optional[str]) -> sqlite3.Connection:
    from icici_breeze_backend.app.services.bots.scalping import backtest_store as store

    return sqlite3.connect(path or store.db_path())


def ensure_tables(path: Optional[str] = None) -> None:
    with _connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS nse_daily_options (
                session TEXT NOT NULL,
                expiry TEXT NOT NULL,
                strike REAL NOT NULL,
                right TEXT NOT NULL,
                open REAL,
                close REAL NOT NULL,
                volume INTEGER NOT NULL,
                oi INTEGER,
                PRIMARY KEY (expiry, strike, right, session)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_nse_daily_options_session ON nse_daily_options(session)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS nse_daily_index (
                session TEXT PRIMARY KEY NOT NULL,
                open REAL,
                close REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS nse_daily_fetches (
                session TEXT PRIMARY KEY NOT NULL,
                status TEXT NOT NULL,
                fetched_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


# --------------------------------------------------------------------------------------
# URLs and parsing (pure)
# --------------------------------------------------------------------------------------


def old_url(day: datetime.date) -> str:
    mon = day.strftime("%b").upper()
    return f"{_ARCHIVE}/content/historical/DERIVATIVES/{day:%Y}/{mon}/fo{day:%d}{mon}{day:%Y}bhav.csv.zip"


def udiff_url(day: datetime.date) -> str:
    return f"{_ARCHIVE}/content/fo/BhavCopy_NSE_FO_0_0_0_{day:%Y%m%d}_F_0000.csv.zip"


def index_url(day: datetime.date) -> str:
    return f"{_ARCHIVE}/content/indices/ind_close_all_{day:%d%m%Y}.csv"


def _num(value: Any) -> Optional[float]:
    try:
        v = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return v


def _date(value: Any) -> Optional[datetime.date]:
    text = str(value or "").strip()
    for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d-%m-%Y", "%d-%B-%Y"):
        try:
            return datetime.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_index(text: str) -> Optional[tuple[Optional[float], float]]:
    """(open, close) of the Nifty 50 from an `ind_close_all` file, or None."""
    for row in csv.DictReader(io.StringIO(text)):
        name = str(row.get("Index Name") or "").strip().upper()
        if name == INDEX_NAME:
            close = _num(row.get("Closing Index Value"))
            if close and close > 0:
                return _num(row.get("Open Index Value")), close
    return None


def _csv_rows(blob: bytes) -> Iterable[dict[str, str]]:
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        names = zf.namelist()
        if not names:
            return
        with zf.open(names[0]) as fh:
            for row in csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8", errors="replace")):
                yield {str(k or "").strip(): v for k, v in row.items()}


def _keep(session: datetime.date, expiry: Optional[datetime.date], strike: Optional[float],
          index_close: float) -> bool:
    if expiry is None or strike is None or expiry < session:
        return False
    if (expiry - session).days > MAX_DTE:
        return False
    return abs(strike - index_close) <= STRIKE_BAND * index_close


def parse_old(blob: bytes, session: datetime.date, index_close: float) -> list[Row]:
    """NIFTY index-option rows that traded, from the pre-July-2024 format. `CLOSE` is the option's
    close; `SETTLE_PR` is the index on expiry day, never used."""
    out: list[Row] = []
    for r in _csv_rows(blob):
        if (r.get("INSTRUMENT") or "").strip() != "OPTIDX" or (r.get("SYMBOL") or "").strip() != SYMBOL:
            continue
        right = {"CE": "Call", "PE": "Put"}.get((r.get("OPTION_TYP") or "").strip())
        expiry, strike = _date(r.get("EXPIRY_DT")), _num(r.get("STRIKE_PR"))
        close, volume = _num(r.get("CLOSE")), int(_num(r.get("CONTRACTS")) or 0)
        if right is None or not close or close <= 0 or volume <= 0:
            continue
        if not _keep(session, expiry, strike, index_close):
            continue
        out.append((session.isoformat(), expiry.isoformat(), float(strike), right,
                    _num(r.get("OPEN")), close, volume, int(_num(r.get("OPEN_INT")) or 0)))
    return out


def parse_udiff(blob: bytes, session: datetime.date, index_close: Optional[float]) -> tuple[list[Row], Optional[float]]:
    """NIFTY index-option rows that traded, from UDiFF; and the index close the file states
    (`UndrlygPric`), for a session whose index file is missing."""
    out: list[Row] = []
    stated: Optional[float] = None
    rows = list(_csv_rows(blob))
    for r in rows:
        if (r.get("FinInstrmTp") or "").strip() == "IDO" and (r.get("TckrSymb") or "").strip() == SYMBOL:
            stated = stated or _num(r.get("UndrlygPric"))
    level = index_close or stated
    if not level:
        return [], None
    for r in rows:
        if (r.get("FinInstrmTp") or "").strip() != "IDO" or (r.get("TckrSymb") or "").strip() != SYMBOL:
            continue
        right = {"CE": "Call", "PE": "Put"}.get((r.get("OptnTp") or "").strip())
        expiry, strike = _date(r.get("XpryDt")), _num(r.get("StrkPric"))
        close, volume = _num(r.get("ClsPric")), int(_num(r.get("TtlTradgVol")) or 0)
        if right is None or not close or close <= 0 or volume <= 0:
            continue
        if not _keep(session, expiry, strike, level):
            continue
        out.append((session.isoformat(), expiry.isoformat(), float(strike), right,
                    _num(r.get("OpnPric")), close, volume, int(_num(r.get("OpnIntrst")) or 0)))
    return out, stated


# --------------------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------------------


class Stopped(RuntimeError):
    """Asked to stop (a cancel, or the market opening)."""


class DailyFetcher:
    """Downloads missing sessions into the store, one request at a time."""

    def __init__(
        self,
        *,
        path: Optional[str] = None,
        get: Optional[Callable[..., Any]] = None,
        sleep: Callable[[float], None] = time.sleep,
        stop: Optional[Callable[[], Optional[str]]] = None,
        log: Callable[[str], None] = _logger.info,
        progress: Optional[Callable[[int, int], None]] = None,
    ) -> None:
        self.path = path
        self._get = get
        self.sleep = sleep
        self.stop = stop
        self.log = log
        self.progress = progress
        self.requests = 0
        self.stats = {OK: 0, NO_FILE: 0, ERROR: 0}

    def _download(self, url: str) -> tuple[int, bytes]:
        """(HTTP status, body); status 0 for a network failure."""
        if self.stop is not None:
            reason = self.stop()
            if reason:
                raise Stopped(reason)
        if self.requests:
            self.sleep(REQUEST_PAUSE_SECONDS)
        self.requests += 1
        get = self._get
        if get is None:
            import requests

            from icici_breeze_backend.app.services.reference_data.bhavcopy_common import NSE_ARCHIVES_HTTP_HEADERS

            def get(u: str) -> Any:
                return requests.get(u, headers=NSE_ARCHIVES_HTTP_HEADERS, timeout=30)
        try:
            resp = get(url)
        except Exception as exc:  # noqa: BLE001 -- a network failure is an error, retried later
            _logger.debug("nse daily: %s failed: %s", url, exc)
            return 0, b""
        return int(getattr(resp, "status_code", 0)), bytes(getattr(resp, "content", b"") or b"")

    def missing(self, start: datetime.date, end: datetime.date) -> list[datetime.date]:
        ensure_tables(self.path)
        with _connect(self.path) as conn:
            done = {r[0] for r in conn.execute(
                "SELECT session FROM nse_daily_fetches WHERE session BETWEEN ? AND ?",
                (start.isoformat(), end.isoformat()),
            )}
        out, day = [], start
        while day <= end:
            if day.weekday() < 5 and day.isoformat() not in done:
                out.append(day)
            day += datetime.timedelta(days=1)
        return out

    def fetch_range(self, start: datetime.date, end: datetime.date) -> dict[str, int]:
        days = self.missing(start, end)
        for n, day in enumerate(days, start=1):
            if self.progress is not None:
                self.progress(n, len(days))
            status = self.fetch_day(day)
            self.stats[status] += 1
            if n % 50 == 0 or n == len(days):
                self.log(f"NSE daily prices: {n}/{len(days)} sessions checked "
                         f"({self.stats[OK]} stored, {self.stats[NO_FILE]} holidays, {self.stats[ERROR]} errors)")
        return dict(self.stats)

    def fetch_day(self, day: datetime.date) -> str:
        idx_status, idx_body = self._download(index_url(day))
        index = parse_index(idx_body.decode("utf-8", errors="replace")) if idx_status == 200 else None
        rows: list[Row] = []
        fo_status = 404
        if day < UDIFF_FROM:
            fo_status, blob = self._download(old_url(day))
            if fo_status == 200 and index is not None:
                try:
                    rows = parse_old(blob, day, index[1])
                except zipfile.BadZipFile:
                    fo_status = 0
        if day >= UDIFF_FROM or fo_status == 404:
            fo_status, blob = self._download(udiff_url(day))
            if fo_status == 200:
                try:
                    rows, stated = parse_udiff(blob, day, index[1] if index else None)
                except zipfile.BadZipFile:
                    fo_status, stated = 0, None
                if index is None and stated:
                    index = (None, stated)
        if idx_status == 404 and fo_status == 404:
            self._record(day, NO_FILE, None, [])
            return NO_FILE
        if fo_status != 200 or index is None:
            return ERROR
        self._record(day, OK, index, rows)
        return OK

    def _record(self, day: datetime.date, status: str, index: Optional[tuple[Optional[float], float]],
                rows: list[Row]) -> None:
        from icici_breeze_backend.app.core.timezone import now_ist

        with _connect(self.path) as conn:
            if index is not None:
                conn.execute("INSERT OR REPLACE INTO nse_daily_index (session, open, close) VALUES (?, ?, ?)",
                             (day.isoformat(), index[0], index[1]))
            if rows:
                conn.executemany(
                    "INSERT OR REPLACE INTO nse_daily_options "
                    "(session, expiry, strike, right, open, close, volume, oi) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )
            conn.execute("INSERT OR REPLACE INTO nse_daily_fetches (session, status, fetched_at) VALUES (?, ?, ?)",
                         (day.isoformat(), status, now_ist().isoformat(timespec="seconds")))
            conn.commit()


# --------------------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------------------


def sessions(start: datetime.date, end: datetime.date, path: Optional[str] = None) -> list[datetime.date]:
    ensure_tables(path)
    with _connect(path) as conn:
        rows = conn.execute(
            "SELECT session FROM nse_daily_index WHERE session BETWEEN ? AND ? ORDER BY session",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
    return [datetime.date.fromisoformat(r[0]) for r in rows]


def index_closes(start: datetime.date, end: datetime.date, path: Optional[str] = None) -> dict[datetime.date, float]:
    ensure_tables(path)
    with _connect(path) as conn:
        rows = conn.execute(
            "SELECT session, close FROM nse_daily_index WHERE session BETWEEN ? AND ?",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
    return {datetime.date.fromisoformat(s): float(c) for s, c in rows}


def latest_index_close(path: Optional[str] = None) -> Optional[float]:
    ensure_tables(path)
    with _connect(path) as conn:
        row = conn.execute("SELECT close FROM nse_daily_index ORDER BY session DESC LIMIT 1").fetchone()
    return float(row[0]) if row else None


def contract_closes(expiry: datetime.date, strike: float, right: str,
                    path: Optional[str] = None) -> dict[datetime.date, float]:
    with _connect(path) as conn:
        rows = conn.execute(
            "SELECT session, close FROM nse_daily_options WHERE expiry = ? AND strike = ? AND right = ?",
            (expiry.isoformat(), float(strike), right),
        ).fetchall()
    return {datetime.date.fromisoformat(s): float(c) for s, c in rows}


def traded_on(session: datetime.date, expiry: datetime.date,
              path: Optional[str] = None) -> list[tuple[float, str, float]]:
    """(strike, right, close) of every contract of one expiry that traded on one session."""
    with _connect(path) as conn:
        rows = conn.execute(
            "SELECT strike, right, close FROM nse_daily_options WHERE session = ? AND expiry = ?",
            (session.isoformat(), expiry.isoformat()),
        ).fetchall()
    return [(float(k), str(r), float(c)) for k, r, c in rows]


def expiries_by_session(start: datetime.date, end: datetime.date,
                        path: Optional[str] = None) -> dict[datetime.date, list[datetime.date]]:
    """The expiries that traded on each session: what was listed, as far as the replay can know."""
    ensure_tables(path)
    with _connect(path) as conn:
        rows = conn.execute(
            "SELECT DISTINCT session, expiry FROM nse_daily_options WHERE session BETWEEN ? AND ?",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
    out: dict[datetime.date, list[datetime.date]] = {}
    for s, e in rows:
        out.setdefault(datetime.date.fromisoformat(s), []).append(datetime.date.fromisoformat(e))
    return {d: sorted(v) for d, v in out.items()}


def index_bars(start: datetime.date, end: datetime.date,
               path: Optional[str] = None) -> list[tuple[datetime.date, Optional[float], float]]:
    """(session, open, close) of the Nifty 50, oldest first: the premium gate's forecast reads them."""
    ensure_tables(path)
    with _connect(path) as conn:
        rows = conn.execute(
            "SELECT session, open, close FROM nse_daily_index WHERE session BETWEEN ? AND ? ORDER BY session",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
    return [(datetime.date.fromisoformat(s), o, float(c)) for s, o, c in rows]


def holidays_between(start: datetime.date, end: datetime.date, path: Optional[str] = None) -> set[datetime.date]:
    """Weekdays NSE published nothing for (both files 404)."""
    ensure_tables(path)
    with _connect(path) as conn:
        rows = conn.execute(
            "SELECT session FROM nse_daily_fetches WHERE status = ? AND session BETWEEN ? AND ?",
            (NO_FILE, start.isoformat(), end.isoformat()),
        ).fetchall()
    return {datetime.date.fromisoformat(r[0]) for r in rows}


def coverage(path: Optional[str] = None) -> dict[str, Any]:
    ensure_tables(path)
    with _connect(path) as conn:
        first, last, n = conn.execute(
            "SELECT MIN(session), MAX(session), COUNT(*) FROM nse_daily_index").fetchone()
    return {"first": first, "last": last, "sessions": int(n or 0)}
