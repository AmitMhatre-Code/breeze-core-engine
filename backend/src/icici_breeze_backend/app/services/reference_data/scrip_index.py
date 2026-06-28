"""Publish and read scrip master underlyings/strikes from Redis."""
from __future__ import annotations

import datetime as dt
import logging
import sqlite3
from collections import defaultdict
from typing import Any

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.strike import Strike, parse_strike, strikes_sorted
from icici_breeze_backend.app.db.redis_client import cache_get_json, cache_set_json
from icici_breeze_backend.app.services.reference_data.aliases import scrip_short_name, underlying_aliases
from icici_breeze_backend.app.services.reference_data.bhavcopy_common import display_from_iso_date
from icici_breeze_backend.app.services.reference_data.keys import (
    exchange_code_map_key,
    strikes_key,
    underlyings_key,
)
from icici_breeze_backend.app.services.reference_data.versioning import bump_refdata_version

_logger = logging.getLogger(__name__)


def _scrip_master_connection():
    return sqlite3.connect(cfg.DATA_PATH + cfg.SCRIP_DB)


def scrip_master_row_count() -> int:
    try:
        with _scrip_master_connection() as conn:
            row = conn.execute("SELECT COUNT(*) FROM scrip_master").fetchone()
            return int(row[0]) if row else 0
    except Exception:
        _logger.debug("scrip_master row count check failed", exc_info=True)
        return 0


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


def _expiry_to_iso(raw: Any) -> str:
    if isinstance(raw, dt.date):
        return raw.isoformat()
    disp = _expiry_to_display(raw)
    try:
        return dt.datetime.strptime(disp, "%d-%b-%Y").date().isoformat()
    except ValueError:
        return str(raw or "")[:10]


def _next_version() -> int:
    from icici_breeze_backend.app.db.redis_client import get_redis
    from icici_breeze_backend.app.services.reference_data.keys import CURRENT_VERSION_KEY

    raw = get_redis().get(CURRENT_VERSION_KEY)
    try:
        current = int(raw or "0")
    except (TypeError, ValueError):
        current = 0
    return current + 1


def publish_scrip_index_from_db(version: int | None = None) -> int:
    """Rebuild scrip master Redis index from SQLite. Returns version number."""
    ver = version if version is not None else _next_version()
    exchange_code_map: dict[str, str] = {}

    for exchange_code in (cfg.NFO, cfg.BFO):
        with _scrip_master_connection() as conn:
            if exchange_code == cfg.NFO:
                rows = conn.execute(
                    """
                    SELECT DISTINCT ShortName, CompanyName, ExpiryDate, ExchangeCode, StrikePrice
                    FROM scrip_master
                    WHERE SegmentCode = ? OR SegmentCode IS NULL
                    """,
                    (exchange_code,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT DISTINCT ShortName, CompanyName, ExpiryDate, ExchangeCode, StrikePrice
                    FROM scrip_master
                    WHERE SegmentCode = ?
                    """,
                    (exchange_code,),
                ).fetchall()

        grouped: dict[tuple[str, str], list[str]] = defaultdict(list)
        strikes_by_key: dict[tuple[str, str], set[Strike]] = defaultdict(set)

        for short, long_name, expiry, ex_code, strike in rows:
            short_s = str(short or "").strip()
            if not short_s:
                continue
            disp = _expiry_to_display(expiry)
            grouped[(short_s, str(long_name or ""))].append(disp)
            if ex_code:
                exchange_code_map[short_s.upper()] = str(ex_code).strip().upper()
            strike_f = parse_strike(strike)
            if strike_f is not None:
                strikes_by_key[(short_s, disp)].add(strike_f)

        underlyings = [
            {
                "stock_code": short,
                "long_name": long,
                "expiry_dates": sorted(
                    {_expiry_to_display(d) for d in dates},
                    key=lambda d: _expiry_to_iso(d),
                ),
            }
            for (short, long), dates in grouped.items()
        ]
        cache_set_json(underlyings_key(ver, exchange_code), underlyings)
        for (short, disp), strike_set in strikes_by_key.items():
            cache_set_json(
                strikes_key(ver, exchange_code, short, disp),
                strikes_sorted(strike_set),
            )

    cache_set_json(exchange_code_map_key(ver), exchange_code_map)
    bump_refdata_version(ver)
    _logger.info("Published scrip index version %s", ver)
    return ver


def current_version() -> int:
    from icici_breeze_backend.app.db.redis_client import get_redis
    from icici_breeze_backend.app.services.reference_data.keys import CURRENT_VERSION_KEY

    raw = get_redis().get(CURRENT_VERSION_KEY)
    try:
        return int(raw or "0")
    except (TypeError, ValueError):
        return 0


def get_underlyings(exchange_code: str = cfg.NFO) -> list[dict[str, Any]] | None:
    version = current_version()
    if version <= 0:
        return None
    data = cache_get_json(underlyings_key(version, exchange_code))
    return data if isinstance(data, list) else None


def get_strikes(
    stock_code: str,
    expiry_display: str,
    exchange_code: str = cfg.NFO,
) -> list[Strike] | None:
    version = current_version()
    if version <= 0:
        return None
    short = scrip_short_name(stock_code)
    data = cache_get_json(strikes_key(version, exchange_code, short, expiry_display))
    if isinstance(data, list) and data:
        return strikes_sorted(data)
    for alias in underlying_aliases(stock_code):
        data = cache_get_json(strikes_key(version, exchange_code, alias, expiry_display))
        if isinstance(data, list) and data:
            return strikes_sorted(data)
    return None


def get_exchange_ticker(short_name: str) -> str:
    version = current_version()
    if version <= 0:
        return scrip_short_name(short_name)
    mapping = cache_get_json(exchange_code_map_key(version)) or {}
    short = scrip_short_name(short_name).upper()
    if isinstance(mapping, dict) and mapping.get(short):
        return str(mapping[short])
    # fallback: query sqlite
    try:
        with _scrip_master_connection() as conn:
            row = conn.execute(
                """
                SELECT TRIM(ExchangeCode) FROM scrip_master
                WHERE ShortName = ? AND ExchangeCode IS NOT NULL AND TRIM(ExchangeCode) != ''
                LIMIT 1
                """,
                (short,),
            ).fetchone()
        if row and row[0]:
            return str(row[0]).strip().upper()
    except sqlite3.Error:
        pass
    return short
