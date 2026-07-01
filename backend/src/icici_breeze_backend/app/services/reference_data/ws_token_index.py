"""Resolve ICICI WebSocket symbol tokens (e.g. 8.1!820390) to option contracts."""
from __future__ import annotations

import datetime as dt
import logging
import re
import sqlite3
import threading
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.strike import Strike, parse_strike, strike_key
from icici_breeze_backend.app.db.redis_client import cache_get_json, cache_set_json
from icici_breeze_backend.app.services.reference_data.aliases import scrip_short_name, underlying_aliases
from icici_breeze_backend.app.services.reference_data.keys import scrip_token_map_key
from icici_breeze_backend.app.services.reference_data.scrip_index import (
    contract_index_key,
    ensure_scrip_memory_ready,
    is_tradeable_contract_memory,
)
from icici_breeze_backend.app.services.reference_data.scrip_master_sql import (
    normalize_expiry_display,
    scrip_master_expiry_sql_values,
)

_logger = logging.getLogger(__name__)
_lock = threading.RLock()

_WS_SYMBOL_RE = re.compile(r"^([\d.]+)!(\d+)$")

EXCHANGE_TO_WS_PREFIX: dict[str, str] = {
    cfg.NFO: "4.1",
    cfg.BFO: "8.1",
}
_PREFIX_TO_EXCHANGE: dict[str, str] = {v: k for k, v in EXCHANGE_TO_WS_PREFIX.items()}

# contract_index_key -> "4.1!44684"
_token_by_contract: dict[str, str] = {}
# token int -> row meta
_token_rows: dict[int, dict[str, Any]] = {}


@dataclass(frozen=True)
class WsTokenContract:
    exchange_code: str
    stock_code: str
    expiry_display: str
    strike_price: float
    option_type: str  # CE | PE


def _scrip_master_connection() -> sqlite3.Connection:
    return sqlite3.connect(cfg.DATA_PATH + cfg.SCRIP_DB)


def _expiry_to_display(raw: Any) -> str:
    if isinstance(raw, dt.date):
        return raw.strftime("%d-%b-%Y")
    return normalize_expiry_display(str(raw or ""))


def canonical_option_type(option_type: str) -> str:
    """Map scrip master / tick option type to CE or PE."""
    opt = str(option_type or "").strip().upper()
    if opt in {"CE", "C", "CALL"}:
        return "CE"
    if opt in {"PE", "P", "PUT"}:
        return "PE"
    return opt


def format_ws_stock_token(exchange_code: str, token: int) -> str:
    prefix = EXCHANGE_TO_WS_PREFIX.get(exchange_code.upper())
    if not prefix:
        raise ValueError(f"Unknown exchange for WS token: {exchange_code}")
    return f"{prefix}!{int(token)}"


def parse_ws_symbol(symbol: str) -> tuple[str, int] | None:
    """Parse ``{prefix}!{token}`` e.g. ``8.1!820390`` → (``8.1``, 820390)."""
    m = _WS_SYMBOL_RE.match(str(symbol or "").strip())
    if not m:
        return None
    try:
        return m.group(1), int(m.group(2))
    except (TypeError, ValueError):
        return None


def exchange_from_ws_prefix(prefix: str) -> str | None:
    return _PREFIX_TO_EXCHANGE.get(str(prefix or "").strip())


def option_type_to_right(option_type: str) -> str:
    return "call" if canonical_option_type(option_type) == "CE" else "put"


def _apply_token_maps(
    token_by_contract: dict[str, str],
    token_rows: dict[int, dict[str, Any]],
) -> None:
    global _token_by_contract, _token_rows
    with _lock:
        _token_by_contract = dict(token_by_contract)
        _token_rows = dict(token_rows)


def load_token_map_from_redis() -> None:
    from icici_breeze_backend.app.services.reference_data.scrip_index import current_version

    ver = current_version()
    if ver <= 0:
        return
    data = cache_get_json(scrip_token_map_key(ver))
    if not isinstance(data, dict):
        return
    token_by_contract = data.get("by_contract") or {}
    token_rows_raw = data.get("token_rows") or {}
    token_rows: dict[int, dict[str, Any]] = {}
    for k, v in token_rows_raw.items():
        try:
            token_rows[int(k)] = v
        except (TypeError, ValueError):
            continue
    if isinstance(token_by_contract, dict):
        _apply_token_maps(token_by_contract, token_rows)


def publish_ws_token_map_from_db(version: int) -> None:
    """Rebuild in-memory + Redis WS token map from ws_token_index table."""
    token_by_contract: dict[str, str] = {}
    token_rows: dict[int, dict[str, Any]] = {}
    try:
        with _scrip_master_connection() as conn:
            rows = conn.execute(
                """
                SELECT Token, SegmentCode, ShortName, ExpiryDate, StrikePrice, OptionType
                FROM ws_token_index
                """
            ).fetchall()
    except sqlite3.Error:
        _logger.debug("publish_ws_token_map_from_db failed", exc_info=True)
        rows = []

    for token, seg, short, expiry, strike, opt_type in rows:
        try:
            token_i = int(token)
        except (TypeError, ValueError):
            continue
        exchange_code = str(seg or "").strip().upper() or cfg.NFO
        stock = str(short or "").strip().upper()
        disp = _expiry_to_display(expiry)
        strike_parsed = parse_strike(strike)
        opt = canonical_option_type(str(opt_type or ""))
        if not stock or strike_parsed is None or opt not in {"CE", "PE"}:
            continue
        if not is_tradeable_contract_memory(
            stock, disp, strike_parsed, opt, exchange_code=exchange_code
        ):
            continue
        try:
            ws_symbol = format_ws_stock_token(exchange_code, token_i)
        except ValueError:
            continue
        ckey = contract_index_key(exchange_code, stock, disp, strike_parsed, opt)
        token_by_contract[ckey] = ws_symbol
        token_rows[token_i] = {
            "exchange_code": exchange_code,
            "stock_code": stock,
            "expiry_display": disp,
            "strike_price": strike_parsed,
            "option_type": opt,
            "ws_symbol": ws_symbol,
        }

    cache_set_json(
        scrip_token_map_key(version),
        {
            "by_contract": token_by_contract,
            "token_rows": {str(k): v for k, v in token_rows.items()},
        },
    )
    _apply_token_maps(token_by_contract, token_rows)


def ensure_token_map_ready() -> bool:
    with _lock:
        ready = bool(_token_by_contract)
    if ready:
        return True
    ensure_scrip_memory_ready()
    load_token_map_from_redis()
    with _lock:
        if _token_by_contract:
            return True
    from icici_breeze_backend.app.services.reference_data.scrip_index import (
        current_version,
        publish_scrip_index_from_db,
    )

    if current_version() <= 0:
        try:
            publish_scrip_index_from_db()
        except Exception:
            _logger.debug("ensure_token_map_ready publish failed", exc_info=True)
    else:
        publish_ws_token_map_from_db(current_version())
    return bool(_token_by_contract)


def list_ws_stock_tokens_for_liquid_contracts(
    exchange_code: str,
    stock_code: str,
    expiry_display: str,
) -> list[str]:
    """WS stock_token symbols for all liquid CE/PE at stock+expiry."""
    ensure_token_map_ready()
    short = scrip_short_name(stock_code).upper()
    disp = normalize_expiry_display(expiry_display)
    aliases = {short, *(a.upper() for a in underlying_aliases(stock_code))}
    out: list[str] = []
    seen: set[str] = set()
    with _lock:
        by_contract = dict(_token_by_contract)
    for alias in aliases:
        for ckey, ws_symbol in by_contract.items():
            parts = ckey.split("|")
            if len(parts) != 5:
                continue
            ex, stk, exp, _strike, _opt = parts
            if ex == exchange_code.upper() and stk == alias and exp == disp:
                if ws_symbol not in seen:
                    seen.add(ws_symbol)
                    out.append(ws_symbol)
    return sorted(out)


def _lookup_token_row_memory(token: int, segment_code: str | None) -> tuple[Any, ...] | None:
    with _lock:
        row = _token_rows.get(int(token))
    if not row:
        return None
    if segment_code and str(row.get("exchange_code") or "").upper() != segment_code.upper():
        return None
    return (
        row.get("exchange_code"),
        row.get("stock_code"),
        row.get("expiry_display"),
        row.get("strike_price"),
        row.get("option_type"),
    )


def _lookup_token_row(token: int, segment_code: str | None) -> tuple[Any, ...] | None:
    if ensure_token_map_ready():
        row = _lookup_token_row_memory(token, segment_code)
        if row:
            return row
    try:
        with _scrip_master_connection() as conn:
            if segment_code:
                row = conn.execute(
                    """
                    SELECT SegmentCode, ShortName, ExpiryDate, StrikePrice, OptionType
                    FROM ws_token_index
                    WHERE Token = ? AND SegmentCode = ?
                    LIMIT 1
                    """,
                    (token, segment_code),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT SegmentCode, ShortName, ExpiryDate, StrikePrice, OptionType
                    FROM ws_token_index
                    WHERE Token = ?
                    LIMIT 1
                    """,
                    (token,),
                ).fetchone()
    except sqlite3.Error:
        _logger.debug("ws_token_index lookup failed for token %s", token, exc_info=True)
        return None
    return row


@lru_cache(maxsize=4096)
def lookup_contract_by_token(token: int, segment_code: str | None = None) -> WsTokenContract | None:
    row = _lookup_token_row(token, segment_code)
    if not row:
        return None
    seg, short, expiry, strike, option_type = row
    exchange_code = str(seg or segment_code or "").strip().upper() or cfg.NFO
    stock = str(short or "").strip().upper()
    if not stock:
        return None
    strike_parsed = parse_strike(strike)
    if strike_parsed is None:
        return None
    return WsTokenContract(
        exchange_code=exchange_code,
        stock_code=stock,
        expiry_display=_expiry_to_display(expiry),
        strike_price=strike_parsed,
        option_type=canonical_option_type(option_type),
    )


def lookup_contract_by_ws_symbol(symbol: str) -> WsTokenContract | None:
    parsed = parse_ws_symbol(symbol)
    if parsed is None:
        return None
    prefix, token = parsed
    segment_code = exchange_from_ws_prefix(prefix)
    if segment_code is None:
        _logger.debug("Unknown WS symbol prefix %r in %r", prefix, symbol)
        return None
    contract = lookup_contract_by_token(token, segment_code)
    if contract is None:
        _logger.debug("No ws_token_index row for %s (token %s)", symbol, token)
    return contract


def clear_token_lookup_cache() -> None:
    lookup_contract_by_token.cache_clear()
    lookup_token_for_contract.cache_clear()


@lru_cache(maxsize=8192)
def lookup_token_for_contract(
    segment_code: str,
    stock_code: str,
    expiry_display: str,
    strike_price: float,
    option_type: str,
) -> int | None:
    """Resolve ICICI WS token for a specific option contract."""
    ensure_token_map_ready()
    opt = canonical_option_type(option_type)
    strike = parse_strike(strike_price)
    if strike is None:
        return None
    disp = normalize_expiry_display(expiry_display)
    short = scrip_short_name(stock_code).upper()
    ckey = contract_index_key(segment_code, short, disp, strike, opt)
    with _lock:
        ws_symbol = _token_by_contract.get(ckey)
    if not ws_symbol:
        for alias in underlying_aliases(stock_code):
            ckey = contract_index_key(segment_code, alias.upper(), disp, strike, opt)
            with _lock:
                ws_symbol = _token_by_contract.get(ckey)
            if ws_symbol:
                break
    if ws_symbol:
        parsed = parse_ws_symbol(ws_symbol)
        if parsed:
            return parsed[1]

    expiry_values = scrip_master_expiry_sql_values(expiry_display)
    if not expiry_values:
        return None
    placeholders = ",".join("?" * len(expiry_values))
    sql = f"""
        SELECT Token FROM ws_token_index
        WHERE SegmentCode = ? AND ShortName = ?
          AND ExpiryDate IN ({placeholders})
          AND StrikePrice = ? AND OptionType = ?
        LIMIT 1
    """
    params: tuple[Any, ...] = (
        segment_code.upper(),
        stock_code.upper(),
        *expiry_values,
        strike,
        opt,
    )
    try:
        with _scrip_master_connection() as conn:
            row = conn.execute(sql, params).fetchone()
    except sqlite3.Error:
        _logger.debug("lookup_token_for_contract failed", exc_info=True)
        return None
    if not row:
        return None
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return None


def populate_ws_token_index_from_raw(cursor: sqlite3.Cursor, exchange_code: str) -> None:
    """Rebuild ws_token_index rows for one segment from raw_scrip_data (before it is dropped)."""
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS ws_token_index (
            Token INTEGER PRIMARY KEY,
            SegmentCode TEXT,
            ShortName TEXT,
            ExpiryDate TEXT,
            StrikePrice REAL,
            OptionType TEXT
        )
        """
    )
    cursor.execute("DELETE FROM ws_token_index WHERE SegmentCode = ?", (exchange_code,))
    cursor.execute(
        """
        SELECT Token, ShortName, ExpiryDate, StrikePrice, OptionType
        FROM raw_scrip_data
        WHERE Series = "OPTION"
        """
    )
    rows = cursor.fetchall()
    if rows:
        normalized = [
            (
                int(token),
                exchange_code,
                str(short or "").strip(),
                normalize_expiry_display(str(expiry or "")),
                parse_strike(strike),
                canonical_option_type(str(opt or "")),
            )
            for token, short, expiry, strike, opt in rows
            if parse_strike(strike) is not None and canonical_option_type(str(opt or "")) in {"CE", "PE"}
        ]
        cursor.executemany(
            """
            INSERT INTO ws_token_index (Token, SegmentCode, ShortName, ExpiryDate, StrikePrice, OptionType)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            normalized,
        )
