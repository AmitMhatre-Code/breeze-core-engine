"""Resolve ICICI WebSocket symbol tokens (e.g. 8.1!820390) to option contracts."""
from __future__ import annotations

import datetime as dt
import logging
import re
import sqlite3
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import icici_breeze_backend.app.core.config as cfg

_logger = logging.getLogger(__name__)

_WS_SYMBOL_RE = re.compile(r"^([\d.]+)!(\d+)$")

# ICICI isec_token_level1 prefix → options segment.
_PREFIX_TO_EXCHANGE: dict[str, str] = {
    "4.1": cfg.NFO,
    "8.1": cfg.BFO,
}


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
    s = str(raw or "").strip()
    if len(s) == 10 and s[4] == "-":
        try:
            return dt.datetime.strptime(s, "%Y-%m-%d").strftime("%d-%b-%Y")
        except ValueError:
            return s
    return s


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
    opt = str(option_type or "").strip().upper()
    if opt in {"CE", "C", "CALL"}:
        return "call"
    return "put"


def _lookup_token_row(token: int, segment_code: str | None) -> tuple[Any, ...] | None:
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
    try:
        strike_f = float(strike)
    except (TypeError, ValueError):
        return None
    return WsTokenContract(
        exchange_code=exchange_code,
        stock_code=stock,
        expiry_display=_expiry_to_display(expiry),
        strike_price=strike_f,
        option_type=str(option_type or "").strip().upper(),
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
    opt = str(option_type or "").strip().upper()
    if opt in {"CALL", "C"}:
        opt = "CE"
    elif opt in {"PUT", "P"}:
        opt = "PE"
    expiry_values = [expiry_display]
    if len(expiry_display) == 10 and expiry_display[4] == "-":
        try:
            expiry_values.append(
                dt.datetime.strptime(expiry_display, "%Y-%m-%d").strftime("%d-%b-%Y")
            )
        except ValueError:
            pass
    elif "-" in expiry_display and len(expiry_display) == 11:
        try:
            expiry_values.append(
                dt.datetime.strptime(expiry_display, "%d-%b-%Y").date().isoformat()
            )
        except ValueError:
            pass
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
        float(strike_price),
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
            ExpiryDate DATE,
            StrikePrice REAL,
            OptionType TEXT
        )
        """
    )
    cursor.execute("DELETE FROM ws_token_index WHERE SegmentCode = ?", (exchange_code,))
    cursor.execute(
        """
        INSERT INTO ws_token_index (Token, SegmentCode, ShortName, ExpiryDate, StrikePrice, OptionType)
        SELECT Token, ?, ShortName, ExpiryDate, StrikePrice, OptionType
        FROM raw_scrip_data
        WHERE Series = "OPTION"
        """,
        (exchange_code,),
    )
