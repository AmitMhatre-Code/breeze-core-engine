"""SPAN baseline margin index in Redis with process-local mirror."""
from __future__ import annotations

import logging
import math
import sqlite3
import threading
from typing import Any

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.strike import Strike, parse_strike, strike_key
from icici_breeze_backend.app.db.redis_client import cache_get_json, cache_set_json
from icici_breeze_backend.app.services.reference_data.aliases import scrip_short_name, underlying_aliases
from icici_breeze_backend.app.services.reference_data.keys import (
    span_baseline_meta_key,
    span_baseline_sheet_key,
)
from icici_breeze_backend.app.services.reference_data.scrip_index import current_version
from icici_breeze_backend.app.services.reference_data.versioning import bump_refdata_version

_logger = logging.getLogger(__name__)
_lock = threading.RLock()

_local: dict[str, Any] = {
    "meta": {},  # exchange_code -> meta dict
    "by_sheet": {},  # sheet_key -> contracts dict
}


def _scrip_conn() -> sqlite3.Connection:
    return sqlite3.connect(cfg.DATA_PATH + cfg.SCRIP_DB)


def _sheet_local_key(exchange_code: str, short_name: str, expiry_display: str) -> str:
    return f"{exchange_code.upper()}|{short_name.upper()}|{expiry_display}"


def _contract_key(strike_price: Strike | float | int | str, option_type: str) -> str:
    return f"{strike_key(strike_price)}:{option_type.upper()}"


def _right_to_option_type(right: str) -> str:
    r = str(right or "").strip()
    if r in (cfg.CALL, "Call", "CE", "C"):
        return "CE"
    return "PE"


def span_baseline_row_count(exchange_code: str | None = None) -> int:
    try:
        with _scrip_conn() as conn:
            if exchange_code:
                row = conn.execute(
                    "SELECT COUNT(*) FROM exchange_margin_baseline WHERE exchange_code = ?",
                    (exchange_code.upper(),),
                ).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) FROM exchange_margin_baseline").fetchone()
            return int(row[0]) if row else 0
    except Exception:
        _logger.debug("span baseline row count failed", exc_info=True)
        return 0


def is_span_baseline_cached(exchange_code: str) -> bool:
    ver = current_version()
    if ver <= 0:
        return False
    meta = cache_get_json(span_baseline_meta_key(ver, exchange_code))
    return isinstance(meta, dict) and int(meta.get("row_count") or 0) > 0


def publish_span_baseline_from_db(version: int | None = None) -> int:
    """Publish all SPAN baseline rows from SQLite to Redis. Returns version number."""
    from icici_breeze_backend.app.services.reference_data.scrip_index import _next_version

    ver = version if version is not None else _next_version()
    sheets: dict[str, dict[str, dict[str, float | int]]] = {}
    meta_by_exchange: dict[str, dict[str, Any]] = {}

    try:
        with _scrip_conn() as conn:
            rows = conn.execute(
                """
                SELECT exchange_code, short_name, expiry_date, strike_price, option_type,
                       margin_per_lot, lot_size, source_file, source_date
                FROM exchange_margin_baseline
                """
            ).fetchall()
    except sqlite3.Error as exc:
        _logger.warning("SPAN baseline publish failed reading SQLite: %s", exc)
        return ver

    for ex, short, expiry, strike, opt_type, mpl, ls, src_file, src_date in rows:
        ex_u = str(ex or "").strip().upper()
        short_u = str(short or "").strip().upper()
        expiry_s = str(expiry or "").strip()
        opt = str(opt_type or "").strip().upper()
        if not ex_u or not short_u or not expiry_s or opt not in ("CE", "PE"):
            continue
        strike_f = parse_strike(strike)
        if strike_f is None:
            continue
        try:
            margin_per_lot = float(mpl)
            lot_size = int(ls) if ls else 0
        except (TypeError, ValueError):
            continue
        if margin_per_lot < 0:
            continue

        sheet_key = _sheet_local_key(ex_u, short_u, expiry_s)
        sheets.setdefault(sheet_key, {})[_contract_key(strike_f, opt)] = {
            "margin_per_lot": margin_per_lot,
            "lot_size": lot_size,
        }
        m = meta_by_exchange.setdefault(
            ex_u,
            {
                "row_count": 0,
                "source_date": str(src_date or ""),
                "source_file": str(src_file or ""),
            },
        )
        m["row_count"] = int(m["row_count"]) + 1
        if src_date and str(src_date) > str(m.get("source_date") or ""):
            m["source_date"] = str(src_date)
            m["source_file"] = str(src_file or "")

    by_sheet: dict[str, dict[str, dict[str, float | int]]] = {}
    for sheet_key, contracts in sheets.items():
        ex_u, short_u, expiry_s = sheet_key.split("|", 2)
        cache_set_json(
            span_baseline_sheet_key(ver, ex_u, short_u, expiry_s),
            contracts,
        )
        by_sheet[sheet_key] = contracts

    for ex_u, meta in meta_by_exchange.items():
        cache_set_json(span_baseline_meta_key(ver, ex_u), meta)

    with _lock:
        _local["meta"] = dict(meta_by_exchange)
        _local["by_sheet"] = by_sheet

    bump_refdata_version(ver)
    _logger.info(
        "Published SPAN baseline version %s sheets=%s rows=%s",
        ver,
        len(sheets),
        sum(int(m.get("row_count") or 0) for m in meta_by_exchange.values()),
    )
    return ver


def load_local_from_redis() -> None:
    ver = current_version()
    if ver <= 0:
        return
    meta_by_exchange: dict[str, dict[str, Any]] = {}
    by_sheet: dict[str, dict[str, Any]] = {}
    for ex in (cfg.NFO, cfg.BFO):
        meta = cache_get_json(span_baseline_meta_key(ver, ex)) or {}
        if isinstance(meta, dict):
            meta_by_exchange[ex] = meta
    # Rebuild by_sheet from Redis keys is expensive; sheets are loaded on demand in get_span_baseline_sheet
    with _lock:
        _local["meta"] = meta_by_exchange
        if not _local.get("by_sheet"):
            _local["by_sheet"] = by_sheet


def _load_sheet_into_local(exchange_code: str, short_name: str, expiry_display: str) -> dict[str, Any] | None:
    ver = current_version()
    if ver <= 0:
        return None
    data = cache_get_json(
        span_baseline_sheet_key(ver, exchange_code, short_name, expiry_display),
    )
    if not isinstance(data, dict) or not data:
        return None
    sheet_key = _sheet_local_key(exchange_code, short_name, expiry_display)
    with _lock:
        _local.setdefault("by_sheet", {})[sheet_key] = data
    return data


def get_span_baseline_sheet(
    exchange_code: str,
    stock_code: str,
    expiry_display: str,
) -> dict[str, Any]:
    """Return margin contracts for one underlying expiry (Redis/local first, SQLite fallback)."""
    ex = str(exchange_code or cfg.NFO).strip().upper()
    expiry = str(expiry_display or "").strip()
    if not expiry:
        return {"found": False, "contracts": {}, "source_date": None, "source_file": None}

    aliases = underlying_aliases(stock_code)
    short_candidates = [scrip_short_name(stock_code)] + list(aliases)

    for short in short_candidates:
        short_u = str(short or "").strip().upper()
        if not short_u:
            continue
        sheet_key = _sheet_local_key(ex, short_u, expiry)
        with _lock:
            contracts = (_local.get("by_sheet") or {}).get(sheet_key)
        if not contracts:
            contracts = _load_sheet_into_local(ex, short_u, expiry)
        if contracts:
            with _lock:
                meta = (_local.get("meta") or {}).get(ex) or {}
            return {
                "found": True,
                "contracts": dict(contracts),
                "source_date": meta.get("source_date"),
                "source_file": meta.get("source_file"),
            }

    contracts = _load_sheet_from_sqlite(ex, stock_code, expiry, short_candidates)
    if contracts:
        return {
            "found": True,
            "contracts": contracts,
            "source_date": None,
            "source_file": None,
        }
    return {"found": False, "contracts": {}, "source_date": None, "source_file": None}


def _load_sheet_from_sqlite(
    exchange_code: str,
    stock_code: str,
    expiry_display: str,
    short_candidates: tuple[str, ...] | list[str],
) -> dict[str, dict[str, float | int]] | None:
    names = {str(s).strip().upper() for s in short_candidates if s}
    if not names:
        names = {scrip_short_name(stock_code)}
    try:
        with _scrip_conn() as conn:
            placeholders = ",".join("?" for _ in names)
            rows = conn.execute(
                f"""
                SELECT strike_price, option_type, margin_per_lot, lot_size
                FROM exchange_margin_baseline
                WHERE exchange_code = ? AND expiry_date = ? AND short_name IN ({placeholders})
                """,
                (exchange_code, expiry_display, *sorted(names)),
            ).fetchall()
    except sqlite3.Error:
        return None
    if not rows:
        return None
    out: dict[str, dict[str, float | int]] = {}
    for strike, opt_type, mpl, ls in rows:
        try:
            out[_contract_key(strike, str(opt_type))] = {
                "margin_per_lot": float(mpl),
                "lot_size": int(ls) if ls else 0,
            }
        except (TypeError, ValueError):
            continue
    return out or None


def compute_span_margin_required(
    contracts: dict[str, Any],
    strike_price: Strike,
    right: str,
    quantity: int,
) -> dict[str, Any]:
    opt = _right_to_option_type(right)
    entry = contracts.get(_contract_key(strike_price, opt))
    if not entry:
        return {"found": False}
    try:
        margin_per_lot = float(entry.get("margin_per_lot"))
        lot_size = int(entry.get("lot_size") or 0)
    except (TypeError, ValueError):
        return {"found": False}
    if not lot_size or lot_size <= 0:
        return {"found": False}
    lots = max(1, int(math.ceil(float(quantity) / float(lot_size))))
    return {
        "found": True,
        "span_margin_required": margin_per_lot * lots,
        "margin_per_lot": margin_per_lot,
        "lot_size": lot_size,
    }


def resolve_margin_from_store(
    exchange_code: str,
    stock_code: str,
    expiry_display: str,
    strike_price: Strike,
    right: str,
    quantity: int,
) -> dict[str, Any]:
    sheet = get_span_baseline_sheet(exchange_code, stock_code, expiry_display)
    if not sheet.get("found"):
        return {"found": False}
    return compute_span_margin_required(
        sheet.get("contracts") or {},
        strike_price,
        right,
        quantity,
    )
