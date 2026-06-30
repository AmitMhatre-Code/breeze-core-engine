"""Tradeable option contracts (MarginPercentage > 0 in ICICI scrip master)."""
from __future__ import annotations

import sqlite3
from typing import Any

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.strike import Strike, parse_strike, strikes_sorted


def is_tradeable(margin_percentage: int | None) -> bool:
    return int(margin_percentage or 0) > 0


def _scrip_conn() -> sqlite3.Connection:
    return sqlite3.connect(cfg.DATA_PATH + cfg.SCRIP_DB)


def _expiry_sql_values(expiry_date: str) -> list[str]:
    from icici_breeze_backend.app.services.reference_data.scrip_master_sql import (
        scrip_master_expiry_sql_values,
    )

    return list(scrip_master_expiry_sql_values(expiry_date))


def _segment_clause(exchange_code: str) -> tuple[str, tuple[Any, ...]]:
    if exchange_code == cfg.NFO:
        return "(SegmentCode = ? OR SegmentCode IS NULL)", (exchange_code,)
    return "SegmentCode = ?", (exchange_code,)


def list_tradeable_strikes(
    stock_code: str,
    expiry_date: str,
    *,
    exchange_code: str = cfg.NFO,
) -> list[Strike]:
    """Distinct strikes with at least one CE/PE row where MarginPercentage > 0."""
    expiry_sql_values = _expiry_sql_values(expiry_date)
    if not expiry_sql_values:
        return []
    seg_clause, seg_params = _segment_clause(exchange_code)
    expiry_placeholders = ",".join("?" * len(expiry_sql_values))
    sql = f"""
        SELECT DISTINCT StrikePrice FROM scrip_master
        WHERE ShortName = ? AND ExpiryDate IN ({expiry_placeholders})
          AND {seg_clause}
          AND StrikePrice IS NOT NULL AND StrikePrice > 0
          AND MarginPercentage > 0
        ORDER BY StrikePrice
    """
    params: tuple[Any, ...] = (stock_code, *expiry_sql_values, *seg_params)
    with _scrip_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    out: list[Strike] = []
    for row in rows:
        strike_f = parse_strike(row[0])
        if strike_f is not None:
            out.append(strike_f)
    return strikes_sorted(out)


def is_tradeable_contract(
    stock_code: str,
    expiry_date: str,
    strike: Strike,
    option_type: str,
    *,
    exchange_code: str = cfg.NFO,
) -> bool:
    """True when the specific CE/PE contract has MarginPercentage > 0."""
    expiry_sql_values = _expiry_sql_values(expiry_date)
    if not expiry_sql_values:
        return False
    opt = str(option_type or "").strip().upper()
    if opt in {"CALL", "C"}:
        opt = "CE"
    elif opt in {"PUT", "P"}:
        opt = "PE"
    seg_clause, seg_params = _segment_clause(exchange_code)
    expiry_placeholders = ",".join("?" * len(expiry_sql_values))
    sql = f"""
        SELECT MarginPercentage FROM scrip_master
        WHERE ShortName = ? AND ExpiryDate IN ({expiry_placeholders})
          AND StrikePrice = ? AND OptionType = ?
          AND {seg_clause}
        LIMIT 1
    """
    params: tuple[Any, ...] = (
        stock_code,
        *expiry_sql_values,
        strike,
        opt,
        *seg_params,
    )
    with _scrip_conn() as conn:
        row = conn.execute(sql, params).fetchone()
    if not row:
        return False
    return is_tradeable(row[0])
