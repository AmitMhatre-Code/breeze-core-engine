"""Underlying identity: ICICI ShortName <-> exchange symbol <-> company name, and index-or-stock.

One underlying is spelled at least three ways in this app. ICICI's Breeze API and every
contract identity here use the Security Master's `ShortName` (`BSESEN`, `CNXBAN`, `RELIND`);
the same file's `ExchangeCode` carries the exchange's own symbol (`SENSEX`, `NIFTY BANK`,
`RELIANCE`); `CompanyName` is what `breeze_connect` stamps into a tick's `stock_name`
(`BSE SENSEX`, `NIFTY FINANCIAL SERVICES INDEX`). The Security Master also states, per contract,
whether the underlying is an index -- `InstrumentName` is `OPTIDX`/`FUTIDX` on NSE, `OPTIND`/
`FUTIND` on BSE, against `OPTSTK`/`FUTSTK` for single stocks.

This module is the one place that mapping lives. It exists because the alternative -- a hand-kept
set of names in a config file -- silently charged the 5% single-stock ELM tier on SENSEX index
shorts (2.5x over) for every underlying whose ShortName differs from its exchange name, which is
all of them but NIFTY and BANKEX.

Source of truth is the `symbol_master` table, rebuilt from `raw_scrip_data` on every scrip-master
load (`populate_symbol_master_from_raw`, called from `processor.load_scrip_master`) and published
into the versioned reference-data cache alongside the scrip index. Reads go to an in-process
mirror, then Redis, then SQLite -- the same layering `scrip_index` uses.

`is_index()` returns None, never False, for an underlying it cannot resolve: callers must decide
what to do about not knowing rather than inherit a guess.
"""
from __future__ import annotations

import logging
import re
import sqlite3
import threading
from dataclasses import asdict, dataclass
from typing import Any

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.db.redis_client import cache_get_json, cache_set_json
from icici_breeze_backend.app.services.reference_data.keys import symbols_key

_logger = logging.getLogger(__name__)

KIND_INDEX = "index"
KIND_STOCK = "stock"

# Spellings no ICICI file contains, so they cannot be derived: NSE's derivative bhavcopy names
# its index contracts differently from ICICI's ExchangeCode ("BANKNIFTY" vs "NIFTY BANK"), and
# `fo_bhavcopy` rows are stored under the bhavcopy's own ticker. Stocks need no entry -- NSE and
# ICICI agree there (RELIANCE, MARUTI) -- and neither do BSE's index tickers (SENSEX, BANKEX,
# FOCIT), which already match ICICI's ExchangeCode and so resolve from the Security Master.
# SPAN pfCode bridges (BSXOPT -> BSESEN) live in `nsccl_baseline`, next to that file's parser.
_FEED_ALIASES: dict[str, str] = {
    "BANKNIFTY": "CNXBAN",
    "FINNIFTY": "NIFFIN",
    "MIDCPNIFTY": "NIFSEL",
    "NIFTYNXT50": "NIFNEX",
    "NIFTYFPI": "NIF150",
}


@dataclass(frozen=True)
class SymbolInfo:
    short_name: str  # ICICI code; the app's canonical identity and the Breeze `stock_code`
    exchange_symbol: str  # exchange symbol / index name
    company_name: str  # display name; what breeze_connect puts in a tick's `stock_name`
    kind: str | None  # KIND_INDEX | KIND_STOCK, or None when only the names are known
    segment: str  # NFO | BFO


_lock = threading.RLock()
_local: dict[str, Any] = {"version": 0, "symbols": {}, "aliases": {}}


def _scrip_master_connection() -> sqlite3.Connection:
    return sqlite3.connect(cfg.DATA_PATH + cfg.SCRIP_DB)


def _upper(value: Any) -> str:
    return str(value or "").strip().upper()


def _squash(value: str) -> str:
    """Punctuation/space-insensitive form, so "NIFTY 50" and "NIFTY50" are one key."""
    return re.sub(r"[^A-Z0-9]", "", value)


def kind_from_instrument_name(instrument_name: Any) -> str:
    """NSE: OPTIDX/FUTIDX vs OPTSTK/FUTSTK. BSE: OPTIND/FUTIND (BSE lists no stock F&O here)."""
    name = _upper(instrument_name)
    return KIND_INDEX if ("IDX" in name or "IND" in name) else KIND_STOCK


def _symbol_key(segment: str, short_name: str) -> str:
    return f"{_upper(segment)}|{_upper(short_name)}"


# --- build (write side) -------------------------------------------------------------------


def populate_symbol_master_from_raw(cursor: sqlite3.Cursor, exchange_code: str) -> None:
    """Rebuild symbol_master rows for one segment from raw_scrip_data (before it is dropped).

    Built from raw_scrip_data rather than scrip_master because scrip_master only keeps rows that
    join to the freeze-limits file -- an underlying missing from that file would vanish here.
    """
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS symbol_master (
            SegmentCode TEXT NOT NULL,
            ShortName TEXT NOT NULL,
            ExchangeSymbol TEXT,
            CompanyName TEXT,
            InstrumentKind TEXT,
            PRIMARY KEY (SegmentCode, ShortName)
        )
        """
    )
    cursor.execute("DELETE FROM symbol_master WHERE SegmentCode = ?", (exchange_code,))
    cursor.execute(
        """
        SELECT ShortName, ExchangeCode, CompanyName, InstrumentName, COUNT(*) AS n
        FROM raw_scrip_data
        WHERE ShortName IS NOT NULL AND TRIM(ShortName) != ''
        GROUP BY ShortName, ExchangeCode, CompanyName, InstrumentName
        """
    )
    # One underlying has several InstrumentName values (option + future rows); they agree on
    # index-vs-stock, so the most populous row wins and settles any stray disagreement.
    best: dict[str, tuple[int, tuple[str, str, str, str, str]]] = {}
    for short, exchange_symbol, company, instrument, count in cursor.fetchall():
        short_s = _upper(short)
        if not short_s:
            continue
        row = (
            exchange_code,
            short_s,
            _upper(exchange_symbol),
            str(company or "").strip(),
            kind_from_instrument_name(instrument),
        )
        n = int(count or 0)
        if short_s not in best or n > best[short_s][0]:
            best[short_s] = (n, row)
    if best:
        cursor.executemany(
            """
            INSERT INTO symbol_master
                (SegmentCode, ShortName, ExchangeSymbol, CompanyName, InstrumentKind)
            VALUES (?, ?, ?, ?, ?)
            """,
            [row for _n, row in best.values()],
        )
    clear_cache()


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def _query(conn: sqlite3.Connection, sql: str) -> list[tuple[Any, ...]]:
    try:
        return conn.execute(sql).fetchall()
    except sqlite3.Error:
        _logger.debug("symbol registry query failed: %s", sql.split()[3:5], exc_info=True)
        return []


def _rows_from_db() -> list[SymbolInfo]:
    """symbol_master first, then scrip_master for anything it doesn't cover.

    scrip_master carries the same three names but not InstrumentName, so its rows resolve
    spellings with `kind=None` -- an app upgraded onto a database written before symbol_master
    existed keeps every name lookup working, and only says "I don't know" where it genuinely
    doesn't: whether the underlying is an index.
    """
    out: list[SymbolInfo] = []
    seen: set[str] = set()
    try:
        conn = _scrip_master_connection()
    except sqlite3.Error:
        _logger.debug("scrip db connect failed", exc_info=True)
        return out
    try:
        classified = _query(
            conn,
            """
            SELECT SegmentCode, ShortName, ExchangeSymbol, CompanyName, InstrumentKind
            FROM symbol_master
            """,
        )
        for segment, short, exchange_symbol, company, kind in classified:
            short_s = _upper(short)
            if not short_s:
                continue
            key = _symbol_key(segment, short_s)
            seen.add(key)
            out.append(
                SymbolInfo(
                    short_name=short_s,
                    exchange_symbol=_upper(exchange_symbol),
                    company_name=str(company or "").strip(),
                    kind=KIND_INDEX if _upper(kind) == KIND_INDEX.upper() else KIND_STOCK,
                    segment=_upper(segment),
                )
            )
        # scrip_master has grown columns over time (SegmentCode and MarginPercentage are ALTERed
        # in by the loader), so select defensively the way scrip_index does rather than assuming
        # the current shape -- an older database must degrade to fewer names, not to none.
        cols = _table_columns(conn, "scrip_master")
        if "ShortName" in cols:
            segment_expr = "SegmentCode" if "SegmentCode" in cols else "NULL"
            company_expr = "CompanyName" if "CompanyName" in cols else "''"
            exchange_expr = "ExchangeCode" if "ExchangeCode" in cols else "''"
            unclassified = _query(
                conn,
                f"""
                SELECT DISTINCT {segment_expr}, ShortName, {exchange_expr}, {company_expr}
                FROM scrip_master
                """,
            )
        else:
            unclassified = []
        for segment, short, exchange_symbol, company in unclassified:
            short_s = _upper(short)
            if not short_s:
                continue
            key = _symbol_key(segment, short_s)
            if key in seen:
                continue
            seen.add(key)
            out.append(
                SymbolInfo(
                    short_name=short_s,
                    exchange_symbol=_upper(exchange_symbol),
                    company_name=str(company or "").strip(),
                    kind=None,
                    segment=_upper(segment),
                )
            )
    finally:
        conn.close()
    return out


def publish_symbols(version: int) -> int:
    """Publish the registry into the versioned cache. Called by publish_scrip_index_from_db so
    the symbol table flips with the rest of the reference-data version, never half-built."""
    symbols = _rows_from_db()
    cache_set_json(symbols_key(version), [asdict(s) for s in symbols])
    _apply_local_mirror(version, symbols)
    unresolved = unresolved_bhavcopy_underlyings()
    if unresolved:
        _logger.warning(
            "Bhavcopy names %d underlying(s) the symbol registry cannot resolve: %s. "
            "Previous-close and SPAN lookups for these will miss until a spelling is bridged "
            "in symbol_registry._FEED_ALIASES.",
            len(unresolved),
            ", ".join(unresolved),
        )
    return len(symbols)


# --- read side ----------------------------------------------------------------------------


def _apply_local_mirror(version: int, symbols: list[SymbolInfo]) -> None:
    by_key: dict[str, SymbolInfo] = {}
    aliases: dict[str, list[str]] = {}

    def _index(alias: str, key: str) -> None:
        for form in {_upper(alias), _squash(_upper(alias))}:
            if not form:
                continue
            bucket = aliases.setdefault(form, [])
            if key not in bucket:
                bucket.append(key)

    for sym in symbols:
        key = _symbol_key(sym.segment, sym.short_name)
        by_key[key] = sym
        _index(sym.short_name, key)
        _index(sym.exchange_symbol, key)
        _index(sym.company_name, key)
    for feed_alias, short_name in _FEED_ALIASES.items():
        for key, sym in by_key.items():
            if sym.short_name == short_name:
                _index(feed_alias, key)
    with _lock:
        _local["version"] = version
        _local["symbols"] = by_key
        _local["aliases"] = aliases


def load_local_from_redis() -> None:
    from icici_breeze_backend.app.services.reference_data.scrip_index import current_version

    ver = current_version()
    if ver <= 0:
        return
    raw = cache_get_json(symbols_key(ver))
    if not isinstance(raw, list) or not raw:
        return
    symbols = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        try:
            symbols.append(SymbolInfo(**entry))
        except TypeError:
            continue
    if symbols:
        _apply_local_mirror(ver, symbols)


def ensure_symbols_ready() -> bool:
    with _lock:
        if _local.get("symbols"):
            return True
    load_local_from_redis()
    with _lock:
        if _local.get("symbols"):
            return True
    symbols = _rows_from_db()
    if symbols:
        with _lock:
            version = int(_local.get("version") or 0)
        _apply_local_mirror(version, symbols)
    with _lock:
        return bool(_local.get("symbols"))


def clear_cache() -> None:
    """Drop the in-process mirror (after a master reload, or between tests)."""
    with _lock:
        _local["version"] = 0
        _local["symbols"] = {}
        _local["aliases"] = {}


def resolve(code: Any, segment: str | None = None) -> SymbolInfo | None:
    """The underlying behind any spelling of `code`, or None if the registry doesn't know it.

    `segment` (NFO/BFO) disambiguates an underlying listed on both; without it, a single match
    wins and a cross-segment tie resolves by segment name for stability.
    """
    wanted = _upper(code)
    if not wanted:
        return None
    ensure_symbols_ready()
    with _lock:
        aliases: dict[str, list[str]] = _local.get("aliases") or {}
        by_key: dict[str, SymbolInfo] = _local.get("symbols") or {}
        keys = aliases.get(wanted) or aliases.get(_squash(wanted)) or []
        matches = [by_key[k] for k in keys if k in by_key]
    if not matches:
        return None
    if segment:
        seg = _upper(segment)
        for sym in matches:
            if sym.segment == seg:
                return sym
        return None
    if len(matches) == 1:
        return matches[0]
    return sorted(matches, key=lambda s: (s.segment, s.short_name))[0]


def short_name_for(code: Any, segment: str | None = None) -> str:
    """ICICI ShortName for any spelling. Falls back to the input (upper-cased) when unknown, so
    callers that only need a cache key keep working before the first scrip-master load."""
    sym = resolve(code, segment)
    if sym is not None:
        return sym.short_name
    wanted = _upper(code)
    return _FEED_ALIASES.get(wanted, wanted)


def exchange_symbol_for(code: Any, segment: str | None = None) -> str:
    """Exchange symbol (SENSEX, NIFTY BANK, ADANIENSOL) -- what SPAN files and bhavcopies name."""
    sym = resolve(code, segment)
    if sym is not None and sym.exchange_symbol:
        return sym.exchange_symbol
    return short_name_for(code, segment)


def aliases_for(code: Any, segment: str | None = None) -> tuple[str, ...]:
    """Every spelling one underlying answers to, for lookups against data keyed by another feed's
    namespace. Company names are deliberately excluded -- they are for inbound resolution, not
    for matching against symbol columns."""
    wanted = _upper(code)
    sym = resolve(code, segment)
    if sym is None:
        names = {wanted, _FEED_ALIASES.get(wanted, "")}
        names.update(feed for feed, short in _FEED_ALIASES.items() if short == wanted)
    else:
        names = {wanted, sym.short_name, sym.exchange_symbol}
        names.update(
            feed for feed, short in _FEED_ALIASES.items() if short == sym.short_name
        )
    names.discard("")
    return tuple(sorted(names))


def is_index(code: Any, segment: str | None = None) -> bool | None:
    """True/False when the Security Master says so, None when it is not known.

    Not known means either an underlying the registry has never seen, or one carried only by a
    pre-symbol_master database, where the names resolve but the classification does not exist.

    Callers must handle None explicitly: ELM tiering, margin and GTT placement all read
    differently for an index, and a guess there is a money error, not a cosmetic one.
    """
    sym = resolve(code, segment)
    if sym is None or sym.kind is None:
        return None
    return sym.kind == KIND_INDEX


def underlyings(segment: str | None = None, kind: str | None = None) -> tuple[SymbolInfo, ...]:
    ensure_symbols_ready()
    seg = _upper(segment) if segment else None
    want_kind = _upper(kind).lower() if kind else None
    with _lock:
        rows = list((_local.get("symbols") or {}).values())
    out = [
        s
        for s in rows
        if (seg is None or s.segment == seg) and (want_kind is None or s.kind == want_kind)
    ]
    return tuple(sorted(out, key=lambda s: (s.segment, s.short_name)))


def unresolved_bhavcopy_underlyings() -> tuple[str, ...]:
    """Underlyings the bhavcopy names but the registry cannot place.

    The bhavcopy is the one feed with a namespace of its own (`BANKNIFTY` for `CNXBAN`), so a
    newly listed index arrives here as a spelling nothing resolves -- silently, since a missed
    join just reads as "no previous close". Comparing what the feed actually stored against what
    the registry can resolve is the check that would have caught NIFTYFPI/NIF150.
    """
    try:
        conn = _scrip_master_connection()
    except sqlite3.Error:
        return ()
    try:
        rows = _query(conn, "SELECT DISTINCT segment, stock_code FROM fo_bhavcopy")
    finally:
        conn.close()
    unresolved = {
        _upper(stock_code)
        for segment, stock_code in rows
        if _upper(stock_code) and resolve(stock_code, segment=segment) is None
    }
    return tuple(sorted(unresolved))
