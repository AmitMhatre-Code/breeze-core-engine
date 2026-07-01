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
from icici_breeze_backend.app.core.strike import parse_strike
from icici_breeze_backend.app.services.reference_data.scrip_master_sql import (
    normalize_expiry_display,
    scrip_master_expiry_sql_values,
)

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
    return normalize_expiry_display(str(raw or ""))


def canonical_option_type(option_type: str) -> str:
    """Map scrip master / tick option type to CE or PE."""
    opt = str(option_type or "").strip().upper()
    if opt in {"CE", "C", "CALL"}:
        return "CE"
    if opt in {"PE", "P", "PUT"}:
        return "PE"
    return opt


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
    opt = canonical_option_type(option_type)
    strike = parse_strike(strike_price)
    if strike is None:
        return None
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
