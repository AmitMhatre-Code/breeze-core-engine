"""Publish and read scrip master underlyings/strikes from Redis + in-process mirror."""
from __future__ import annotations

import datetime as dt
import logging
import sqlite3
import threading
from collections import defaultdict
from typing import Any

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.strike import Strike, parse_strike, strike_key, strikes_sorted
from icici_breeze_backend.app.db.redis_client import cache_get_json, cache_set_json
from icici_breeze_backend.app.services.reference_data.aliases import scrip_short_name, underlying_aliases
from icici_breeze_backend.app.services.reference_data.keys import (
    exchange_code_map_key,
    scrip_contracts_key,
    strikes_key,
    underlyings_key,
)
from icici_breeze_backend.app.services.reference_data.versioning import bump_refdata_version

_logger = logging.getLogger(__name__)
_lock = threading.RLock()
_local: dict[str, Any] = {
    "version": 0,
    "contracts": {},
    "exchange_code_map": {},
    "underlyings": {},
    "strikes": {},
    "lot_sizes": {},
}


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


def _canonical_option_type(option_type: str) -> str:
    opt = str(option_type or "").strip().upper()
    if opt in {"CE", "C", "CALL"}:
        return "CE"
    if opt in {"PE", "P", "PUT"}:
        return "PE"
    return opt


def contract_index_key(
    exchange_code: str,
    stock_code: str,
    expiry_display: str,
    strike: Strike,
    option_type: str,
) -> str:
    return (
        f"{exchange_code.upper()}|{stock_code.upper()}|{expiry_display}|"
        f"{strike_key(strike)}|{_canonical_option_type(option_type)}"
    )


def _lot_size_key(exchange_code: str, stock_code: str, expiry_display: str) -> str:
    return f"{exchange_code.upper()}|{stock_code.upper()}|{expiry_display}"


def _strikes_cache_key(exchange_code: str, stock_code: str, expiry_display: str) -> str:
    return f"{exchange_code.upper()}|{stock_code.upper()}|{expiry_display}"


def _next_version() -> int:
    from icici_breeze_backend.app.db.redis_client import get_redis
    from icici_breeze_backend.app.services.reference_data.keys import CURRENT_VERSION_KEY

    raw = get_redis().get(CURRENT_VERSION_KEY)
    try:
        current = int(raw or "0")
    except (TypeError, ValueError):
        current = 0
    return current + 1


def _apply_local_mirror(
    ver: int,
    *,
    contracts: dict[str, dict[str, Any]],
    exchange_code_map: dict[str, str],
    underlyings_by_exchange: dict[str, list[dict[str, Any]]],
    strikes_by_key: dict[str, list[Strike]],
    lot_sizes: dict[str, int],
) -> None:
    with _lock:
        _local["version"] = ver
        _local["contracts"] = dict(contracts)
        _local["exchange_code_map"] = dict(exchange_code_map)
        _local["underlyings"] = dict(underlyings_by_exchange)
        _local["strikes"] = dict(strikes_by_key)
        _local["lot_sizes"] = dict(lot_sizes)


def load_local_from_redis() -> None:
    ver = current_version()
    if ver <= 0:
        return
    contracts = cache_get_json(scrip_contracts_key(ver)) or {}
    exchange_code_map = cache_get_json(exchange_code_map_key(ver)) or {}
    underlyings_by_exchange: dict[str, list[dict[str, Any]]] = {}
    strikes_by_key: dict[str, list[Strike]] = {}
    for exchange_code in (cfg.NFO, cfg.BFO):
        data = cache_get_json(underlyings_key(ver, exchange_code))
        underlyings_by_exchange[exchange_code] = data if isinstance(data, list) else []
        for entry in underlyings_by_exchange[exchange_code]:
            short = str(entry.get("stock_code") or "").strip()
            for disp in entry.get("expiry_dates") or []:
                sk = _strikes_cache_key(exchange_code, short, str(disp))
                strike_data = cache_get_json(strikes_key(ver, exchange_code, short, str(disp)))
                if isinstance(strike_data, list):
                    strikes_by_key[sk] = strikes_sorted(strike_data)
    lot_sizes: dict[str, int] = {}
    if isinstance(contracts, dict):
        for meta in contracts.values():
            if not isinstance(meta, dict):
                continue
            ls_key = _lot_size_key(
                str(meta.get("exchange_code") or ""),
                str(meta.get("stock_code") or ""),
                str(meta.get("expiry_display") or ""),
            )
            lot_val = meta.get("lot_size")
            if lot_val and ls_key not in lot_sizes:
                try:
                    lot_sizes[ls_key] = int(lot_val)
                except (TypeError, ValueError):
                    pass
    _apply_local_mirror(
        ver,
        contracts=contracts if isinstance(contracts, dict) else {},
        exchange_code_map=exchange_code_map if isinstance(exchange_code_map, dict) else {},
        underlyings_by_exchange=underlyings_by_exchange,
        strikes_by_key=strikes_by_key,
        lot_sizes=lot_sizes,
    )


def ensure_scrip_memory_ready() -> bool:
    with _lock:
        ready = bool(_local.get("contracts"))
    if ready:
        return True
    load_local_from_redis()
    with _lock:
        if _local.get("contracts"):
            return True
    if scrip_master_row_count() > 0:
        try:
            publish_scrip_index_from_db()
            return True
        except Exception:
            _logger.warning("ensure_scrip_memory_ready publish failed", exc_info=True)
    return bool(_local.get("contracts"))


def _scrip_master_columns(conn: sqlite3.Connection) -> set[str]:
    try:
        return {str(row[1]) for row in conn.execute("PRAGMA table_info(scrip_master)")}
    except sqlite3.Error:
        return set()


def _liquid_contract_rows(conn: sqlite3.Connection, exchange_code: str) -> list[tuple[Any, ...]]:
    cols = _scrip_master_columns(conn)
    company_expr = "CompanyName" if "CompanyName" in cols else "ShortName"
    exchange_expr = "ExchangeCode" if "ExchangeCode" in cols else "SegmentCode"
    if exchange_code == cfg.NFO:
        where = "(SegmentCode = ? OR SegmentCode IS NULL)"
    else:
        where = "SegmentCode = ?"
    sql = f"""
        SELECT ShortName, {company_expr}, ExpiryDate, {exchange_expr},
               StrikePrice, OptionType, MarginPercentage, LotSize
        FROM scrip_master
        WHERE {where}
          AND MarginPercentage > 0
          AND StrikePrice IS NOT NULL AND StrikePrice > 0
    """
    try:
        return conn.execute(sql, (exchange_code,)).fetchall()
    except sqlite3.Error:
        return []


def publish_scrip_index_from_db(version: int | None = None) -> int:
    """Rebuild scrip master Redis index from SQLite. Returns version number."""
    from icici_breeze_backend.app.services.reference_data.ws_token_index import (
        publish_ws_token_map_from_db,
    )

    ver = version if version is not None else _next_version()
    exchange_code_map: dict[str, str] = {}
    contracts: dict[str, dict[str, Any]] = {}
    lot_sizes: dict[str, int] = {}
    underlyings_by_exchange: dict[str, list[dict[str, Any]]] = {}
    strikes_flat: dict[str, list[Strike]] = {}

    for exchange_code in (cfg.NFO, cfg.BFO):
        with _scrip_master_connection() as conn:
            contract_rows = _liquid_contract_rows(conn, exchange_code)

        grouped: dict[tuple[str, str], list[str]] = defaultdict(list)
        strikes_by_key: dict[tuple[str, str], set[Strike]] = defaultdict(set)

        for short, long_name, expiry, ex_code, strike, opt_type, margin, lot in contract_rows:
            short_s = str(short or "").strip()
            if not short_s:
                continue
            disp = _expiry_to_display(expiry)
            grouped[(short_s, str(long_name or ""))].append(disp)
            if ex_code:
                exchange_code_map[short_s.upper()] = str(ex_code).strip().upper()
            strike_f = parse_strike(strike)
            opt = _canonical_option_type(str(opt_type or ""))
            if strike_f is None or opt not in {"CE", "PE"}:
                continue
            strikes_by_key[(short_s, disp)].add(strike_f)
            ckey = contract_index_key(exchange_code, short_s, disp, strike_f, opt)
            try:
                lot_val = int(lot or 0)
            except (TypeError, ValueError):
                lot_val = 0
            contracts[ckey] = {
                "exchange_code": exchange_code,
                "stock_code": short_s.upper(),
                "expiry_display": disp,
                "strike_price": strike_f,
                "option_type": opt,
                "margin_percentage": int(margin or 0),
                "lot_size": lot_val,
            }
            ls_key = _lot_size_key(exchange_code, short_s, disp)
            if lot_val > 0 and ls_key not in lot_sizes:
                lot_sizes[ls_key] = lot_val

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
        underlyings_by_exchange[exchange_code] = underlyings
        cache_set_json(underlyings_key(ver, exchange_code), underlyings)
        for (short, disp), strike_set in strikes_by_key.items():
            sorted_strikes = strikes_sorted(strike_set)
            cache_set_json(
                strikes_key(ver, exchange_code, short, disp),
                sorted_strikes,
            )
            strikes_flat[_strikes_cache_key(exchange_code, short, disp)] = sorted_strikes

    cache_set_json(exchange_code_map_key(ver), exchange_code_map)
    cache_set_json(scrip_contracts_key(ver), contracts)
    _apply_local_mirror(
        ver,
        contracts=contracts,
        exchange_code_map=exchange_code_map,
        underlyings_by_exchange=underlyings_by_exchange,
        strikes_by_key=strikes_flat,
        lot_sizes=lot_sizes,
    )
    publish_ws_token_map_from_db(ver)
    bump_refdata_version(ver)
    _logger.info("Published scrip index version %s contracts=%s", ver, len(contracts))
    return ver


def current_version() -> int:
    from icici_breeze_backend.app.db.redis_client import get_redis
    from icici_breeze_backend.app.services.reference_data.keys import CURRENT_VERSION_KEY

    raw = get_redis().get(CURRENT_VERSION_KEY)
    try:
        return int(raw or "0")
    except (TypeError, ValueError):
        return 0


def get_contract_meta(
    stock_code: str,
    expiry_display: str,
    strike: Strike,
    option_type: str,
    *,
    exchange_code: str = cfg.NFO,
) -> dict[str, Any] | None:
    ensure_scrip_memory_ready()
    short = scrip_short_name(stock_code).upper()
    disp = _expiry_to_display(expiry_display)
    ckey = contract_index_key(exchange_code, short, disp, strike, option_type)
    with _lock:
        meta = (_local.get("contracts") or {}).get(ckey)
    if meta:
        return meta
    for alias in underlying_aliases(stock_code):
        ckey = contract_index_key(exchange_code, alias.upper(), disp, strike, option_type)
        with _lock:
            meta = (_local.get("contracts") or {}).get(ckey)
        if meta:
            return meta
    return None


def is_tradeable_contract_memory(
    stock_code: str,
    expiry_display: str,
    strike: Strike,
    option_type: str,
    *,
    exchange_code: str = cfg.NFO,
) -> bool:
    meta = get_contract_meta(
        stock_code, expiry_display, strike, option_type, exchange_code=exchange_code
    )
    if not meta:
        return False
    return int(meta.get("margin_percentage") or 0) > 0


def list_tradeable_strikes_memory(
    stock_code: str,
    expiry_display: str,
    *,
    exchange_code: str = cfg.NFO,
) -> list[Strike]:
    ensure_scrip_memory_ready()
    short = scrip_short_name(stock_code)
    disp = _expiry_to_display(expiry_display)
    sk = _strikes_cache_key(exchange_code, short, disp)
    with _lock:
        strikes = (_local.get("strikes") or {}).get(sk)
    if strikes:
        return list(strikes)
    for alias in underlying_aliases(stock_code):
        sk = _strikes_cache_key(exchange_code, alias, disp)
        with _lock:
            strikes = (_local.get("strikes") or {}).get(sk)
        if strikes:
            return list(strikes)
    return []


def get_lot_size_memory(
    stock_code: str,
    expiry_display: str,
    *,
    exchange_code: str = cfg.NFO,
) -> int | None:
    ensure_scrip_memory_ready()
    short = scrip_short_name(stock_code)
    disp = _expiry_to_display(expiry_display)
    ls_key = _lot_size_key(exchange_code, short, disp)
    with _lock:
        lot = (_local.get("lot_sizes") or {}).get(ls_key)
    if lot:
        return lot
    for alias in underlying_aliases(stock_code):
        ls_key = _lot_size_key(exchange_code, alias, disp)
        with _lock:
            lot = (_local.get("lot_sizes") or {}).get(ls_key)
        if lot:
            return lot
    return None


def get_underlyings(exchange_code: str = cfg.NFO) -> list[dict[str, Any]] | None:
    ensure_scrip_memory_ready()
    with _lock:
        data = (_local.get("underlyings") or {}).get(exchange_code.upper())
    if isinstance(data, list):
        return data
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
    strikes = list_tradeable_strikes_memory(stock_code, expiry_display, exchange_code=exchange_code)
    return strikes if strikes else None


def get_exchange_ticker(short_name: str) -> str:
    ensure_scrip_memory_ready()
    short = scrip_short_name(short_name).upper()
    with _lock:
        mapping = _local.get("exchange_code_map") or {}
    if isinstance(mapping, dict) and mapping.get(short):
        return str(mapping[short])
    return short
