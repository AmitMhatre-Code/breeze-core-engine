"""Bhavcopy index storage and chain assembly."""
from __future__ import annotations

import datetime as dt
import logging
import sqlite3
import threading
from typing import Any

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.strike import Strike, parse_strike, strike_key
from icici_breeze_backend.app.db.redis_client import cache_get_json, cache_set_json
from icici_breeze_backend.app.services.reference_data.aliases import underlying_aliases
from icici_breeze_backend.app.services.reference_data.bhavcopy_common import safe_float, safe_int
from icici_breeze_backend.app.services.reference_data.keys import bhav_index_key, bhav_meta_key
from icici_breeze_backend.app.services.reference_data.scrip_index import (
    current_version,
    get_exchange_ticker,
    get_strikes,
)
from icici_breeze_backend.app.services.reference_data.versioning import bump_refdata_version

_logger = logging.getLogger(__name__)
_lock = threading.RLock()

# Process-local cache mirroring Redis index for fast reads
_local: dict[str, Any] = {
    "nfo": {"meta": {}, "by_strike": {}},
    "bfo": {"meta": {}, "by_strike": {}},
}

_BHAVCOPY_DB_COLUMNS = (
    "segment",
    "stock_code",
    "expiry_display",
    "expiry_date",
    "right",
    "strike_price",
    "ltp",
    "best_bid_price",
    "best_offer_price",
    "total_buy_qty",
    "total_sell_qty",
    "open_interest",
    "spot_price",
    "open",
    "high",
    "low",
    "previous_close",
    "source_date",
    "source_url",
    "ingested_at",
)


def _scrip_conn() -> sqlite3.Connection:
    return sqlite3.connect(cfg.DATA_PATH + cfg.SCRIP_DB)


_FO_BHAVCOPY_DDL = """
CREATE TABLE {name} (
    segment TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    expiry_display TEXT NOT NULL,
    expiry_date TEXT,
    right TEXT NOT NULL,
    strike_price REAL NOT NULL,
    ltp TEXT,
    best_bid_price TEXT,
    best_offer_price TEXT,
    total_buy_qty TEXT,
    total_sell_qty TEXT,
    open_interest TEXT,
    spot_price TEXT,
    open TEXT,
    high TEXT,
    low TEXT,
    previous_close TEXT,
    source_date TEXT NOT NULL,
    source_url TEXT,
    ingested_at TEXT NOT NULL,
    PRIMARY KEY (segment, stock_code, expiry_display, right, strike_price)
)
"""


def _strike_column_is_integer(conn: sqlite3.Connection) -> bool:
    rows = conn.execute("PRAGMA table_info(fo_bhavcopy)").fetchall()
    if not rows:
        return False
    for _cid, name, col_type, *_rest in rows:
        if str(name) == "strike_price":
            return str(col_type or "").upper() == "INTEGER"
    return False


def _migrate_fo_bhavcopy_strike_to_real(conn: sqlite3.Connection) -> None:
    if not _strike_column_is_integer(conn):
        return
    _logger.info("Migrating fo_bhavcopy.strike_price from INTEGER to REAL")
    conn.execute("ALTER TABLE fo_bhavcopy RENAME TO fo_bhavcopy_legacy")
    conn.execute(_FO_BHAVCOPY_DDL.format(name="fo_bhavcopy"))
    conn.execute(
        f"""
        INSERT INTO fo_bhavcopy ({", ".join(_BHAVCOPY_DB_COLUMNS)})
        SELECT {", ".join(_BHAVCOPY_DB_COLUMNS)}
        FROM fo_bhavcopy_legacy
        """
    )
    conn.execute("DROP TABLE fo_bhavcopy_legacy")
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_fo_bhavcopy_segment
        ON fo_bhavcopy(segment)
        """
    )
    conn.commit()
    _logger.info("fo_bhavcopy strike_price migration complete")


def ensure_fo_bhavcopy_table() -> None:
    with _scrip_conn() as conn:
        conn.execute(_FO_BHAVCOPY_DDL.format(name="IF NOT EXISTS fo_bhavcopy"))
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_fo_bhavcopy_segment
            ON fo_bhavcopy(segment)
            """
        )
        conn.commit()
        _migrate_fo_bhavcopy_strike_to_real(conn)


def _segment_key(exchange_code: str) -> str:
    return "nfo" if exchange_code == cfg.NFO else "bfo"


def _row_dict_from_db(
    segment: str,
    stock_code: str,
    expiry_display: str,
    expiry_date: str | None,
    right: str,
    strike_price: float,
    ltp: str | None,
    best_bid: str | None,
    best_offer: str | None,
    total_buy: str | None,
    total_sell: str | None,
    open_interest: str | None,
    spot_price: str | None,
    open_p: str | None,
    high: str | None,
    low: str | None,
    previous_close: str | None,
) -> dict[str, str]:
    return {
        "stock_code": str(stock_code or "").strip().upper(),
        "expiry_display": str(expiry_display or "").strip(),
        "expiry_date": str(expiry_date or "").strip(),
        "right": str(right or "").strip(),
        "strike_price": strike_key(strike_price),
        "ltp": str(ltp or ""),
        "best_bid_price": str(best_bid or ""),
        "best_offer_price": str(best_offer or ""),
        "total_buy_qty": str(total_buy or ""),
        "total_sell_qty": str(total_sell or ""),
        "open_interest": str(open_interest or ""),
        "spot_price": str(spot_price or ""),
        "open": str(open_p or ""),
        "high": str(high or ""),
        "low": str(low or ""),
        "previous_close": str(previous_close or ""),
        "segment": cfg.NFO if segment == "nfo" else cfg.BFO,
    }


def persist_bhavcopy_rows(
    rows: list[dict[str, str]],
    segment: str,
    source_date: dt.date,
    source_url: str,
) -> None:
    """Replace all bhavcopy rows for a segment in SQLite."""
    ensure_fo_bhavcopy_table()
    seg = segment.lower()
    ingested_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    source_date_s = source_date.isoformat()
    source_url_s = str(source_url or "")

    batch: list[tuple] = []
    for row in rows or []:
        stock = str(row.get("stock_code") or "").strip().upper()
        disp = str(row.get("expiry_display") or "").strip()
        right = str(row.get("right") or "").strip()
        strike = parse_strike(row.get("strike_price"))
        if not stock or not disp or not right or strike is None:
            continue
        batch.append(
            (
                seg,
                stock,
                disp,
                str(row.get("expiry_date") or "").strip(),
                right,
                strike,
                str(row.get("ltp") or ""),
                str(row.get("best_bid_price") or ""),
                str(row.get("best_offer_price") or ""),
                str(row.get("total_buy_qty") or ""),
                str(row.get("total_sell_qty") or ""),
                str(row.get("open_interest") or ""),
                str(row.get("spot_price") or ""),
                str(row.get("open") or ""),
                str(row.get("high") or ""),
                str(row.get("low") or ""),
                str(row.get("previous_close") or ""),
                source_date_s,
                source_url_s,
                ingested_at,
            )
        )

    with _scrip_conn() as conn:
        conn.execute("DELETE FROM fo_bhavcopy WHERE segment = ?", (seg,))
        if batch:
            conn.executemany(
                f"""
                INSERT INTO fo_bhavcopy ({", ".join(_BHAVCOPY_DB_COLUMNS)})
                VALUES ({", ".join("?" for _ in _BHAVCOPY_DB_COLUMNS)})
                """,
                batch,
            )
        conn.commit()
    _logger.info("Persisted bhavcopy %s rows=%s date=%s", seg, len(batch), source_date_s)


def bhavcopy_row_count(segment: str) -> int:
    try:
        ensure_fo_bhavcopy_table()
        with _scrip_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM fo_bhavcopy WHERE segment = ?",
                (segment.lower(),),
            ).fetchone()
            return int(row[0]) if row else 0
    except Exception:
        _logger.debug("bhavcopy row count failed", exc_info=True)
        return 0


def load_bhavcopy_rows_from_db(segment: str) -> list[dict[str, str]]:
    ensure_fo_bhavcopy_table()
    seg = segment.lower()
    try:
        with _scrip_conn() as conn:
            rows = conn.execute(
                """
                SELECT stock_code, expiry_display, expiry_date, right, strike_price,
                       ltp, best_bid_price, best_offer_price, total_buy_qty, total_sell_qty,
                       open_interest, spot_price, open, high, low, previous_close
                FROM fo_bhavcopy
                WHERE segment = ?
                """,
                (seg,),
            ).fetchall()
    except sqlite3.Error:
        return []
    out: list[dict[str, str]] = []
    for row in rows:
        out.append(_row_dict_from_db(seg, *row))
    return out


def _load_bhavcopy_meta_from_db(segment: str) -> tuple[dt.date | None, str]:
    ensure_fo_bhavcopy_table()
    seg = segment.lower()
    try:
        with _scrip_conn() as conn:
            row = conn.execute(
                """
                SELECT source_date, source_url
                FROM fo_bhavcopy
                WHERE segment = ?
                LIMIT 1
                """,
                (seg,),
            ).fetchone()
    except sqlite3.Error:
        return None, ""
    if not row:
        return None, ""
    raw_date = str(row[0] or "").strip()
    try:
        return dt.date.fromisoformat(raw_date[:10]), str(row[1] or "")
    except ValueError:
        return None, str(row[1] or "")


def _lookup_bhav_row_from_db(
    segment: str,
    aliases: set[str],
    expiry_display: str,
    right: str,
    strike: Strike,
) -> dict[str, str] | None:
    ensure_fo_bhavcopy_table()
    seg = segment.lower()
    names = sorted({str(a).strip().upper() for a in aliases if a})
    if not names:
        return None
    placeholders = ",".join("?" for _ in names)
    try:
        with _scrip_conn() as conn:
            row = conn.execute(
                f"""
                SELECT stock_code, expiry_display, expiry_date, right, strike_price,
                       ltp, best_bid_price, best_offer_price, total_buy_qty, total_sell_qty,
                       open_interest, spot_price, open, high, low, previous_close
                FROM fo_bhavcopy
                WHERE segment = ? AND expiry_display = ? AND right = ? AND strike_price = ?
                  AND stock_code IN ({placeholders})
                LIMIT 1
                """,
                (seg, expiry_display, right, parse_strike(strike), *names),
            ).fetchone()
    except sqlite3.Error:
        return None
    if not row:
        return None
    return _row_dict_from_db(seg, *row)


def _rebuild_indexes(rows: list[dict[str, str]], segment: str) -> dict[str, Any]:
    by_strike: dict[str, dict[str, str]] = {}
    for row in rows or []:
        stock = str(row.get("stock_code") or "").strip().upper()
        disp = str(row.get("expiry_display") or "").strip()
        right = str(row.get("right") or "").strip()
        strike = parse_strike(row.get("strike_price"))
        if not stock or not disp or not right or strike is None:
            continue
        key = f"{stock}|{disp}|{right}|{strike_key(strike)}"
        by_strike[key] = row
    return {"by_strike": by_strike}


def _publish_bhavcopy_to_redis(
    rows: list[dict[str, str]],
    *,
    segment: str,
    source_date: dt.date,
    source_url: str,
    version: int,
) -> int:
    seg = segment.lower()
    index = _rebuild_indexes(rows, seg)
    meta = {
        "source_date": source_date.isoformat(),
        "source_url": source_url,
        "row_count": len(rows),
        "segment": seg,
    }
    cache_set_json(bhav_meta_key(version, seg), meta)
    cache_set_json(bhav_index_key(version, seg), index)
    with _lock:
        _local[seg] = {"meta": meta, "by_strike": index["by_strike"]}
    bump_refdata_version(version)
    _logger.info(
        "Published bhavcopy %s version %s rows=%s date=%s",
        seg,
        version,
        len(rows),
        source_date,
    )
    return version


def publish_bhavcopy_rows(
    rows: list[dict[str, str]],
    *,
    segment: str,
    source_date: dt.date,
    source_url: str,
    version: int | None = None,
) -> int:
    seg = segment.lower()
    persist_bhavcopy_rows(rows, seg, source_date, source_url)
    ver = version if version is not None else _next_version_for_bhav()
    return _publish_bhavcopy_to_redis(
        rows,
        segment=seg,
        source_date=source_date,
        source_url=source_url,
        version=ver,
    )


def publish_bhavcopy_from_db(segment: str, version: int | None = None) -> int:
    seg = segment.lower()
    rows = load_bhavcopy_rows_from_db(seg)
    if not rows:
        return current_version()
    source_date, source_url = _load_bhavcopy_meta_from_db(seg)
    if source_date is None:
        source_date = dt.date.today()
    ver = version if version is not None else _next_version_for_bhav()
    return _publish_bhavcopy_to_redis(
        rows,
        segment=seg,
        source_date=source_date,
        source_url=source_url,
        version=ver,
    )


def _next_version_for_bhav() -> int:
    from icici_breeze_backend.app.services.reference_data.scrip_index import _next_version

    return _next_version()


def load_local_from_redis() -> None:
    ver = current_version()
    if ver <= 0:
        return
    for seg in ("nfo", "bfo"):
        meta = cache_get_json(bhav_meta_key(ver, seg)) or {}
        index = cache_get_json(bhav_index_key(ver, seg)) or {}
        with _lock:
            _local[seg] = {
                "meta": meta if isinstance(meta, dict) else {},
                "by_strike": (index.get("by_strike") if isinstance(index, dict) else {}) or {},
            }


def get_bhavcopy_source_date(exchange_code: str) -> dt.date | None:
    seg = _segment_key(exchange_code)
    with _lock:
        meta = _local.get(seg, {}).get("meta") or {}
    if not meta:
        load_local_from_redis()
        with _lock:
            meta = _local.get(seg, {}).get("meta") or {}
    raw = str(meta.get("source_date") or "").strip()
    if not raw:
        db_date, _ = _load_bhavcopy_meta_from_db(seg)
        return db_date
    try:
        return dt.date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _lookup_bhav_row(
    stock_code: str,
    expiry_display: str,
    right: str,
    strike: Strike,
    exchange_code: str,
) -> dict[str, str] | None:
    seg = _segment_key(exchange_code)
    ticker = get_exchange_ticker(stock_code)
    aliases = set(underlying_aliases(stock_code)) | {ticker}
    with _lock:
        by_strike = _local.get(seg, {}).get("by_strike") or {}
    if not by_strike:
        load_local_from_redis()
        with _lock:
            by_strike = _local.get(seg, {}).get("by_strike") or {}
    for alias in aliases:
        key = f"{alias.upper()}|{expiry_display}|{right}|{strike_key(strike)}"
        row = by_strike.get(key)
        if row:
            return row
    return _lookup_bhav_row_from_db(seg, aliases, expiry_display, right, strike)


def has_bhavcopy_quote(
    stock_code: str,
    expiry_display: str,
    right: str,
    strike: Strike,
    exchange_code: str,
) -> bool:
    """True when the published bhavcopy index has a row for this contract."""
    return _lookup_bhav_row(stock_code, expiry_display, right, strike, exchange_code) is not None


def _row_to_chain_cell(
    row: dict[str, str],
    stock_code: str,
    expiry_display: str,
    exchange_code: str,
    right: str,
    lot_val: int,
) -> dict[str, Any]:
    total_buy = safe_int(row.get("total_buy_qty"), 0)
    total_sell = safe_int(row.get("total_sell_qty"), 0)
    if total_sell > 0:
        ratio: float | str = total_buy / total_sell
    else:
        ratio = 0.0 if total_buy == 0 else "NA"
    strike_val = parse_strike(row.get("strike_price"))
    return {
        "stock_code": stock_code,
        "strike_price": strike_val if strike_val is not None else 0.0,
        "right": right,
        "expiry_date": expiry_display,
        "ltp": safe_float(row.get("ltp")),
        "open_interest": safe_int(row.get("open_interest"), 0),
        "total_buy_qty": total_buy,
        "total_sell_qty": total_sell,
        "buy_sell_ratio": ratio,
        "best_bid_price": safe_float(row.get("best_bid_price")),
        "best_offer_price": safe_float(row.get("best_offer_price")),
        "spot_price": safe_float(row.get("spot_price")),
        "lot_size": lot_val,
    }


def build_chain_from_bhavcopy(
    stock_code: str,
    expiry_display: str,
    exchange_code: str,
    *,
    lot_size: int | None = None,
    freeze_quantity: int | None = None,
    strikes: list[Strike] | None = None,
) -> dict[str, Any] | None:
    strike_list = strikes if strikes else get_strikes(stock_code, expiry_display, exchange_code=exchange_code)
    if not strike_list:
        return None
    from icici_breeze_backend.app.services.reference_data.tradable_contracts import (
        is_tradeable_contract,
    )

    lot_val = int(lot_size or 0)
    calls: list[dict[str, Any]] = []
    puts: list[dict[str, Any]] = []
    spot_price: float | None = None
    for strike in strike_list:
        for right in (cfg.CALL, cfg.PUT):
            if not is_tradeable_contract(
                stock_code, expiry_display, strike, right, exchange_code=exchange_code
            ):
                continue
            row = _lookup_bhav_row(stock_code, expiry_display, right, strike, exchange_code)
            if not row:
                continue
            cell = _row_to_chain_cell(row, stock_code, expiry_display, exchange_code, right, lot_val)
            if spot_price is None:
                sp = safe_float(row.get("spot_price"), 0)
                if sp > 0:
                    spot_price = sp
            if right == cfg.CALL:
                calls.append(cell)
            else:
                puts.append(cell)
    if not calls and not puts:
        return None
    call_by = {r["strike_price"]: r for r in calls}
    put_by = {r["strike_price"]: r for r in puts}
    chain_strikes = sorted(set(call_by) | set(put_by))
    chain_rows = [
        {"strike_price": k, "call": call_by.get(k), "put": put_by.get(k)}
        for k in chain_strikes
    ]
    max_call_oi = max((r["open_interest"] for r in calls), default=0)
    max_put_oi = max((r["open_interest"] for r in puts), default=0)
    atm_strike = None
    if spot_price is not None and chain_strikes:
        atm_strike = min(chain_strikes, key=lambda s: abs(s - spot_price))
    bhav_date = get_bhavcopy_source_date(exchange_code)
    return {
        "chain_rows": chain_rows,
        "max_call_oi": max_call_oi,
        "max_put_oi": max_put_oi,
        "expiry_display": expiry_display,
        "stock_code": stock_code,
        "exchange_code": exchange_code,
        "spot_price": spot_price,
        "atm_strike": atm_strike,
        "lot_size": lot_val or None,
        "freeze_quantity": freeze_quantity,
        "quote_source": "bhavcopy",
        "bhavcopy_date": bhav_date.isoformat() if bhav_date else None,
    }
