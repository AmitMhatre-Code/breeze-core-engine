"""Index constituent weights for the W-OBI signal -- fetched from the exchanges, never hand-kept.

Sources, in order (docs/design-decisions.md #30):

  NIFTY   1. NSE `equity-stock-indices?index=NIFTY 50`: every constituent's free-float market cap
             (`ffmc`); weight = ffmc_i / sum(ffmc). Exact, live, and needs no cookie warm-up --
             the older `equity-stockIndices` path is the one that 404s.
          2. niftyindices' monthly factsheet PDF ("Top constituents by weightage"), mapped from
             company name to symbol through the published constituents CSV. A month stale, but
             only the tracked names' weights *relative to each other* feed the signal.
  SENSEX  1. BSE: `HeatMapData` lists the 30 constituents with scrip codes, `StockTrading` gives
             each one's free-float market cap (`MktCapFF`), and `MarketCap?code=16` gives the
             index's own free-float total -- a checksum the per-stock sum must match within 1%.
             No third-party library: the maintained BSE one is GPLv3, and this image ships.
  Either  3. `SEED_WEIGHTS` below, as of `SEED_AS_OF`. The last resort, so the signal still
             has a basket on an instance that has never reached an exchange (cloud IPs are
             routinely blocked). Always labelled as such in the published payload.

Identity: an exchange symbol becomes the feed's ICICI ShortName through `symbol_registry`, never a
local map (#28). The ShortName is identical on NSE and BSE for these names (HDFBAN is NSE 1333
and BSE 500180), so one registry lookup serves both exchanges' depth rooms.
"""
from __future__ import annotations

import csv
import io
import logging
import random
import re
import sqlite3
import threading
import time
import zlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

import requests

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.timezone import IST
from icici_breeze_backend.app.services.index_signal.engine import Constituent
from icici_breeze_backend.app.services.reference_data.bhavcopy_common import safe_float

_logger = logging.getLogger(__name__)

LABELS: tuple[str, ...] = ("nifty", "sensex")

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
)
NSE_API_HEADERS = {
    "User-Agent": _UA,
    "Accept": "*/*",
    "Accept-Language": "en-GB,en;q=0.9",
    "Referer": "https://www.nseindia.com/get-quotes/equity?symbol=SBIN",
}
# BSE's API answers a request without a bseindia.com Origin/Referer with a 302 to its error page.
BSE_API_HEADERS = {
    "User-Agent": _UA,
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.bseindia.com/",
    "Referer": "https://www.bseindia.com/",
}
NIFTY_INDICES_HEADERS = {"User-Agent": _UA, "Accept": "*/*"}

NSE_INDEX_API_URL = "https://www.nseindia.com/api/equity-stock-indices"
NSE_INDEX_NAME = "NIFTY 50"
NIFTY_FACTSHEET_URL = "https://www.niftyindices.com/Factsheet/ind_nifty50.pdf"
NIFTY_CONSTITUENTS_CSV_URL = "https://www.niftyindices.com/IndexConstituent/ind_nifty50list.csv"
BSE_API_BASE = "https://api.bseindia.com/BseIndiaAPI/api"
SENSEX_BSE_INDEX_CODE = 16

_TIMEOUT = (5.0, 20.0)
NSE_MIN_CONSTITUENTS = 45
BSE_MIN_CONSTITUENTS = 27
BSE_CHECKSUM_TOLERANCE = 0.01
FACTSHEET_MIN_ROWS = 8
# A failed fetch is retried this long after, not on every check.
_RETRY_SECONDS = 1800.0

SEED_AS_OF = "2026-09-10"
# Last-resort seeds (see module docstring): free-float weights measured from the sources above on
# SEED_AS_OF. Exchange symbols only -- ShortNames still come from the registry at runtime.
SEED_WEIGHTS: dict[str, tuple[tuple[str, float], ...]] = {
    "nifty": (
        ("HDFCBANK", 9.88),
        ("ICICIBANK", 9.22),
        ("RELIANCE", 8.00),
        ("BHARTIARTL", 5.32),
        ("LT", 4.30),
        ("SBIN", 3.88),
        ("INFY", 3.38),
        ("AXISBANK", 3.33),
        ("KOTAKBANK", 2.85),
        ("BAJFINANCE", 2.60),
    ),
    "sensex": (
        ("HDFCBANK", 11.94),
        ("ICICIBANK", 11.14),
        ("RELIANCE", 9.57),
        ("BHARTIARTL", 6.43),
        ("LT", 5.20),
        ("SBIN", 4.66),
        ("INFY", 4.09),
        ("AXISBANK", 4.07),
        ("KOTAKBANK", 3.45),
        ("BAJFINANCE", 3.19),
    ),
}


class WeightsFetchError(RuntimeError):
    """A source answered, but not with something we are willing to weight an index by."""


@dataclass(frozen=True)
class WeightSet:
    label: str
    rows: tuple[tuple[str, float], ...]  # (exchange symbol, weight %), heaviest first
    source: str
    as_of: str  # IST date the weights were taken
    fetched_at: float | None  # None for the seed set


# ------------------------------------------------------------------------------ parsing (pure)


def _normalise(caps: list[tuple[str, float]]) -> list[tuple[str, float]]:
    total = sum(v for _s, v in caps)
    return sorted(((s, 100.0 * v / total) for s, v in caps), key=lambda r: -r[1])


def parse_nse_index_payload(payload: Any) -> list[tuple[str, float]]:
    """Weights from NSE's `equity-stock-indices` JSON. The index's own row carries priority 1."""
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise WeightsFetchError("NSE index payload has no data array")
    caps: list[tuple[str, float]] = []
    for row in rows:
        if not isinstance(row, dict) or int(safe_float(row.get("priority"))) != 0:
            continue
        symbol = str(row.get("symbol") or "").strip().upper()
        ffmc = safe_float(row.get("ffmc"))
        if symbol and ffmc > 0:
            caps.append((symbol, ffmc))
    if len(caps) < NSE_MIN_CONSTITUENTS:
        raise WeightsFetchError(
            f"NSE index payload has {len(caps)} constituents with a free-float cap; "
            f"need at least {NSE_MIN_CONSTITUENTS}"
        )
    return _normalise(caps)


def parse_bse_heatmap(body: Any) -> list[tuple[str, str]]:
    """(symbol, scrip code) per constituent from BSE `HeatMapData`.

    The body is a JSON *string*: `bseindia$#$` then `|`-separated rows of comma fields, where
    field 0 is the symbol and field 8 the scrip code."""
    if not isinstance(body, str):
        return []
    out: list[tuple[str, str]] = []
    for row in body.split("$#$", 1)[-1].split("|"):
        fields = row.split(",")
        if len(fields) > 8 and fields[0].strip() and fields[8].strip().isdigit():
            out.append((fields[0].strip().upper(), fields[8].strip()))
    return out


def parse_bse_index_free_float(payload: Any) -> float:
    row = payload[0] if isinstance(payload, list) and payload else payload
    ff = safe_float(row.get("FreeFloat")) if isinstance(row, dict) else 0.0
    if ff <= 0:
        raise WeightsFetchError("BSE MarketCap payload has no index free-float total")
    return ff


def compute_sensex_weights(
    caps: list[tuple[str, float]], index_free_float: float
) -> list[tuple[str, float]]:
    """Weights from per-stock free-float caps, checked against the index's own total.

    The checksum is what makes a partial fetch (a throttled `StockTrading` call, a constituent
    missing from the heat map) fail loudly instead of inflating every other name's weight."""
    if len(caps) < BSE_MIN_CONSTITUENTS:
        raise WeightsFetchError(
            f"BSE returned free-float caps for {len(caps)} SENSEX constituents; "
            f"need at least {BSE_MIN_CONSTITUENTS}"
        )
    ratio = sum(v for _s, v in caps) / index_free_float
    if abs(ratio - 1.0) > BSE_CHECKSUM_TOLERANCE:
        raise WeightsFetchError(
            f"SENSEX constituent free-float sum is {ratio:.4f}x the index total "
            f"(tolerance {BSE_CHECKSUM_TOLERANCE:.0%})"
        )
    return _normalise(caps)


def extract_pdf_text(data: bytes) -> str:
    """Text-show operands from a PDF's Flate streams -- enough for niftyindices' factsheets.

    Deliberately not a PDF library: the factsheet's text is plain Tj/TJ literals, and pulling in
    a parser for one table is not worth a new dependency. Anything this misses fails the
    factsheet source's own row-count check and falls through to the seeds."""
    chunks: list[bytes] = []
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", data, re.S):
        try:
            chunks.append(zlib.decompress(m.group(1)))
        except zlib.error:
            continue
    parts: list[bytes] = []
    for literal, array in re.findall(rb"\((.*?)\)\s*Tj|\[(.*?)\]\s*TJ", b"\n".join(chunks), re.S):
        if literal:
            parts.append(literal)
        else:
            parts.append(b"".join(re.findall(rb"\((.*?)\)", array)))
    text = " ".join(p.decode("latin-1") for p in parts)
    return text.replace("\\(", "(").replace("\\)", ")")


_FACTSHEET_MARKER = "Top constituents by weightage"
_FACTSHEET_ROW_RE = re.compile(r"([A-Z][A-Za-z0-9&.,'()\- ]*?)\s+(\d{1,2}\.\d{2})(?!\d)")


def parse_factsheet_top_constituents(text: str) -> list[tuple[str, float]]:
    """(company name, weight %) rows following the factsheet's top-constituents heading."""
    idx = text.find(_FACTSHEET_MARKER)
    if idx < 0:
        raise WeightsFetchError("factsheet has no 'Top constituents by weightage' table")
    segment = text[idx + len(_FACTSHEET_MARKER):]
    for stop in ("#", "Disclaimer"):
        cut = segment.find(stop)
        if cut >= 0:
            segment = segment[:cut]
    rows = [(name.strip(), float(w)) for name, w in _FACTSHEET_ROW_RE.findall(segment)]
    return rows[:10]


def _norm_company(name: str) -> str:
    s = name.lower().replace("&", " and ")
    s = re.sub(r"\b(limited|ltd)\b\.?", " ", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def parse_constituent_csv(text: str) -> dict[str, str]:
    """Normalised company name -> symbol, from niftyindices' constituents CSV."""
    out: dict[str, str] = {}
    for row in csv.DictReader(io.StringIO(text)):
        company = str(row.get("Company Name") or "").strip()
        symbol = str(row.get("Symbol") or "").strip().upper()
        if company and symbol:
            out[_norm_company(company)] = symbol
    return out


# ------------------------------------------------------------------------------ fetchers


def _get_json(session: requests.Session, url: str, *, params: dict, headers: dict) -> Any:
    resp = session.get(url, params=params, headers=headers, timeout=_TIMEOUT)
    if resp.status_code != 200:
        raise WeightsFetchError(f"{url} answered HTTP {resp.status_code}")
    try:
        return resp.json()
    except ValueError as exc:
        # BSE's bot wall redirects to an HTML error page that still answers 200.
        raise WeightsFetchError(f"{url} did not return JSON") from exc


def fetch_nifty_weights_nse(session: requests.Session | None = None) -> list[tuple[str, float]]:
    s = session or requests.Session()
    payload = _get_json(
        s, NSE_INDEX_API_URL, params={"index": NSE_INDEX_NAME}, headers=NSE_API_HEADERS
    )
    return parse_nse_index_payload(payload)


def fetch_nifty_weights_factsheet(
    session: requests.Session | None = None,
) -> list[tuple[str, float]]:
    s = session or requests.Session()
    pdf = s.get(NIFTY_FACTSHEET_URL, headers=NIFTY_INDICES_HEADERS, timeout=_TIMEOUT)
    csv_resp = s.get(NIFTY_CONSTITUENTS_CSV_URL, headers=NIFTY_INDICES_HEADERS, timeout=_TIMEOUT)
    if pdf.status_code != 200 or csv_resp.status_code != 200:
        raise WeightsFetchError(
            f"niftyindices answered HTTP {pdf.status_code} (factsheet) / "
            f"{csv_resp.status_code} (constituents)"
        )
    top = parse_factsheet_top_constituents(extract_pdf_text(pdf.content))
    by_company = parse_constituent_csv(csv_resp.text)
    rows = [
        (by_company[_norm_company(company)], weight)
        for company, weight in top
        if _norm_company(company) in by_company
    ]
    if len(rows) < FACTSHEET_MIN_ROWS:
        raise WeightsFetchError(
            f"factsheet yielded {len(rows)} mappable top constituents; need {FACTSHEET_MIN_ROWS}"
        )
    # Percent of the whole index, top 10 only -- deliberately not renormalised here; the engine
    # only ever uses the tracked names' weights relative to each other.
    return sorted(rows, key=lambda r: -r[1])


def fetch_sensex_weights_bse(session: requests.Session | None = None) -> list[tuple[str, float]]:
    s = session or requests.Session()
    index_ff = parse_bse_index_free_float(
        _get_json(
            s,
            f"{BSE_API_BASE}/MarketCap/w",
            params={"code": SENSEX_BSE_INDEX_CODE},
            headers=BSE_API_HEADERS,
        )
    )
    members: list[tuple[str, str]] = []
    # WEEKHEAT answers at any hour; HEAT (the intraday one) comes back empty outside the session.
    for flag in ("WEEKHEAT", "HEAT"):
        members = parse_bse_heatmap(
            _get_json(
                s,
                f"{BSE_API_BASE}/HeatMapData/w",
                params={
                    "flag": flag,
                    "alpha": "",
                    "indexcode": SENSEX_BSE_INDEX_CODE,
                    "random": f"{random.random():.12f}",
                },
                headers=BSE_API_HEADERS,
            )
        )
        if members:
            break
    if not members:
        raise WeightsFetchError("BSE HeatMapData listed no SENSEX constituents")
    caps: list[tuple[str, float]] = []
    for symbol, scrip_code in members:
        stats = _get_json(
            s,
            f"{BSE_API_BASE}/StockTrading/w",
            params={"flag": "", "quotetype": "EQ", "scripcode": scrip_code},
            headers=BSE_API_HEADERS,
        )
        ff = safe_float(stats.get("MktCapFF")) if isinstance(stats, dict) else 0.0
        if ff > 0:
            caps.append((symbol, ff))
    return compute_sensex_weights(caps, index_ff)


def _sources(label: str) -> tuple[tuple[str, Callable[[], list[tuple[str, float]]]], ...]:
    # Resolved at call time so tests can substitute a fetcher on the module.
    if label == "nifty":
        return (
            ("nse_api", fetch_nifty_weights_nse),
            ("niftyindices_factsheet", fetch_nifty_weights_factsheet),
        )
    if label == "sensex":
        return (("bse_api", fetch_sensex_weights_bse),)
    raise ValueError(f"unknown index label: {label!r}")


# ------------------------------------------------------------------------------ storage

_lock = threading.RLock()
_generation = 0
_refresh_running = False
_last_attempt: dict[str, float] = {}
_status: dict[str, dict[str, Any]] = {}


def _db_path() -> str:
    return cfg.DATA_PATH + cfg.USERS_DB


def ensure_weights_table(db_path: str | None = None) -> None:
    with sqlite3.connect(db_path or _db_path()) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS index_constituent_weights (
                index_label TEXT NOT NULL,
                symbol TEXT NOT NULL,
                weight REAL NOT NULL,
                source TEXT NOT NULL,
                as_of TEXT NOT NULL,
                fetched_at REAL NOT NULL,
                PRIMARY KEY (index_label, symbol)
            )
            """
        )
        conn.commit()


def weights_generation() -> int:
    """Bumped on every successful save, so the publisher re-applies weights only when they moved."""
    with _lock:
        return _generation


def save_weights(
    label: str,
    rows: list[tuple[str, float]],
    *,
    source: str,
    as_of: str,
    fetched_at: float | None = None,
    db_path: str | None = None,
) -> None:
    """Replace one index's weights in a single transaction -- never a mix of two fetches."""
    global _generation
    path = db_path or _db_path()
    ensure_weights_table(path)
    ts = time.time() if fetched_at is None else fetched_at
    with sqlite3.connect(path) as conn:
        conn.execute("DELETE FROM index_constituent_weights WHERE index_label = ?", (label,))
        conn.executemany(
            "INSERT INTO index_constituent_weights "
            "(index_label, symbol, weight, source, as_of, fetched_at) VALUES (?, ?, ?, ?, ?, ?)",
            [(label, symbol, float(weight), source, as_of, ts) for symbol, weight in rows],
        )
        conn.commit()
    with _lock:
        _generation += 1


def load_weights(label: str, db_path: str | None = None) -> WeightSet | None:
    path = db_path or _db_path()
    try:
        ensure_weights_table(path)
        with sqlite3.connect(path) as conn:
            rows = conn.execute(
                "SELECT symbol, weight, source, as_of, fetched_at FROM index_constituent_weights "
                "WHERE index_label = ? ORDER BY weight DESC",
                (label,),
            ).fetchall()
    except sqlite3.Error:
        _logger.warning("index weights: could not read stored weights for %s", label, exc_info=True)
        return None
    if not rows:
        return None
    return WeightSet(
        label=label,
        rows=tuple((str(r[0]), float(r[1])) for r in rows),
        source=str(rows[0][2]),
        as_of=str(rows[0][3]),
        fetched_at=float(rows[0][4]),
    )


def current_weight_set(label: str, db_path: str | None = None) -> WeightSet:
    stored = load_weights(label, db_path)
    if stored is not None:
        return stored
    return WeightSet(
        label=label, rows=SEED_WEIGHTS[label], source="seed", as_of=SEED_AS_OF, fetched_at=None
    )


def tracked_constituents(
    label: str, top_n: int, *, db_path: str | None = None
) -> tuple[tuple[Constituent, ...], dict[str, Any]]:
    """The heaviest `top_n` names that resolve to an ICICI ShortName, plus provenance.

    A name the registry cannot resolve is skipped and reported, not guessed: the next-heaviest
    name takes its slot. An instance whose registry is still cold resolves nothing, which the
    publisher treats as "try again shortly", not as an empty basket to trade on."""
    from icici_breeze_backend.app.services.reference_data import symbol_registry

    ws = current_weight_set(label, db_path)
    out: list[Constituent] = []
    unresolved: list[str] = []
    for symbol, weight in ws.rows:
        if len(out) >= top_n:
            break
        info = symbol_registry.resolve(symbol, cfg.NFO)
        if info is None or not info.short_name:
            unresolved.append(symbol)
            continue
        out.append(Constituent(symbol=symbol, short_name=info.short_name.upper(), weight=weight))
    meta = {
        "source": ws.source,
        "as_of": ws.as_of,
        "fetched_at": ws.fetched_at,
        "unresolved": unresolved,
    }
    return tuple(out), meta


# ------------------------------------------------------------------------------ refresh


def refresh_weights(
    label: str, *, db_path: str | None = None, now: float | None = None
) -> dict[str, Any]:
    """Try each source in order; save the first acceptable answer. Never raises."""
    ts = time.time() if now is None else now
    errors: list[str] = []
    status: dict[str, Any] = {"ok": False, "source": None, "rows": 0, "errors": errors, "at": ts}
    for name, fetch in _sources(label):
        try:
            rows = fetch()
        except Exception as exc:  # noqa: BLE001 -- any source failure falls through to the next
            errors.append(f"{name}: {exc}")
            _logger.warning("index weights: %s source %s failed: %s", label, name, exc)
            continue
        as_of = datetime.fromtimestamp(ts, IST).date().isoformat()
        save_weights(label, rows, source=name, as_of=as_of, fetched_at=ts, db_path=db_path)
        status.update(ok=True, source=name, rows=len(rows))
        _logger.info("index weights: %s refreshed from %s (%s names)", label, name, len(rows))
        break
    with _lock:
        _status[label] = status
        _last_attempt[label] = ts
    return status


def weights_due(label: str, now_dt: datetime, *, db_path: str | None = None) -> bool:
    """Due when nothing is stored, or on a trading day whose weights were fetched on another day."""
    from icici_breeze_backend.app.services.market_calendar import is_trading_day

    with _lock:
        last = _last_attempt.get(label)
    if last is not None and now_dt.timestamp() - last < _RETRY_SECONDS:
        return False
    stored = load_weights(label, db_path)
    if stored is None or stored.fetched_at is None:
        return True
    if not is_trading_day(now_dt):
        return False
    return datetime.fromtimestamp(stored.fetched_at, IST).date() != now_dt.astimezone(IST).date()


def refresh_due_weights_in_background(
    now_dt: datetime | None = None, *, force: bool = False
) -> bool:
    """Start one background refresh for whichever indices are due (all of them with `force`,
    the Settings screen's "Refresh now"). True when one was started.

    Off-thread because the SENSEX source is ~30 sequential BSE calls; neither the publish loop
    nor an HTTP request may wait on an exchange website."""
    global _refresh_running
    now_dt = now_dt or datetime.now(IST)
    with _lock:
        if _refresh_running:
            return False
    due = list(LABELS) if force else [label for label in LABELS if weights_due(label, now_dt)]
    if not due:
        return False
    with _lock:
        if _refresh_running:
            return False
        _refresh_running = True

    def _run() -> None:
        global _refresh_running
        try:
            for label in due:
                refresh_weights(label)
        finally:
            with _lock:
                _refresh_running = False

    threading.Thread(target=_run, name="index-weights-refresh", daemon=True).start()
    return True


def refresh_status() -> dict[str, dict[str, Any]]:
    with _lock:
        return {label: dict(status) for label, status in _status.items()}


def refresh_running() -> bool:
    with _lock:
        return _refresh_running


def weights_overview(label: str, top_n: int, *, db_path: str | None = None) -> dict[str, Any]:
    """What Settings -> Index Signal shows for one index: the tracked basket with each name's
    published weight and its share of the basket (the number that actually weights the signal),
    plus provenance and the last refresh attempt."""
    ws = current_weight_set(label, db_path)
    constituents, meta = tracked_constituents(label, top_n, db_path=db_path)
    basket_total = sum(c.weight for c in constituents)
    with _lock:
        last_refresh = dict(_status.get(label) or {}) or None
    return {
        "label": label,
        "source": meta["source"],
        "as_of": meta["as_of"],
        "fetched_at": meta["fetched_at"],
        "universe_size": len(ws.rows),
        "tracked": [
            {
                "symbol": c.symbol,
                "short_name": c.short_name,
                "weight": round(c.weight, 4),
                "basket_share": round(100.0 * c.weight / basket_total, 2) if basket_total > 0 else None,
            }
            for c in constituents
        ],
        "unresolved": meta["unresolved"],
        "last_refresh": last_refresh,
    }


def reset_state_for_tests() -> None:
    global _generation, _refresh_running
    with _lock:
        _generation = 0
        _refresh_running = False
        _last_attempt.clear()
        _status.clear()
