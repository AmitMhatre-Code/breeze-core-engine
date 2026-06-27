"""Bhavcopy index storage and chain assembly."""
from __future__ import annotations

import datetime as dt
import logging
import threading
from collections import defaultdict
from typing import Any

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.db.redis_client import cache_get_json, cache_set_json, get_redis
from icici_breeze_backend.app.services.reference_data.aliases import underlying_aliases
from icici_breeze_backend.app.services.reference_data.bhavcopy_common import safe_float, safe_int
from icici_breeze_backend.app.services.reference_data.keys import (
    CURRENT_VERSION_KEY,
    bhav_index_key,
    bhav_meta_key,
    version_prefix,
)
from icici_breeze_backend.app.services.reference_data.scrip_index import (
    current_version,
    get_exchange_ticker,
    get_strikes,
)

_logger = logging.getLogger(__name__)
_lock = threading.RLock()

# Process-local cache mirroring Redis index for fast reads
_local: dict[str, Any] = {
    "nfo": {"meta": {}, "by_strike": {}},
    "bfo": {"meta": {}, "by_strike": {}},
}


def _segment_key(exchange_code: str) -> str:
    return "nfo" if exchange_code == cfg.NFO else "bfo"


def _rebuild_indexes(rows: list[dict[str, str]], segment: str) -> dict[str, Any]:
    by_strike: dict[str, dict[str, str]] = {}
    for row in rows or []:
        stock = str(row.get("stock_code") or "").strip().upper()
        disp = str(row.get("expiry_display") or "").strip()
        right = str(row.get("right") or "").strip()
        try:
            strike = int(float(str(row.get("strike_price") or "0")))
        except (TypeError, ValueError):
            continue
        if not stock or not disp or strike <= 0:
            continue
        key = f"{stock}|{disp}|{right}|{strike}"
        by_strike[key] = row
    return {"by_strike": by_strike}


def publish_bhavcopy_rows(
    rows: list[dict[str, str]],
    *,
    segment: str,
    source_date: dt.date,
    source_url: str,
    version: int | None = None,
) -> int:
    ver = version if version is not None else _next_version_for_bhav()
    seg = segment.lower()
    index = _rebuild_indexes(rows, seg)
    meta = {
        "source_date": source_date.isoformat(),
        "source_url": source_url,
        "row_count": len(rows),
        "segment": seg,
    }
    cache_set_json(bhav_meta_key(ver, seg), meta)
    cache_set_json(bhav_index_key(ver, seg), index)
    with _lock:
        _local[seg] = {"meta": meta, "by_strike": index["by_strike"]}
    get_redis().set(CURRENT_VERSION_KEY, str(ver))
    _logger.info("Published bhavcopy %s version %s rows=%s date=%s", seg, ver, len(rows), source_date)
    return ver


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
        return None
    try:
        return dt.date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _lookup_bhav_row(
    stock_code: str,
    expiry_display: str,
    right: str,
    strike: int,
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
        key = f"{alias.upper()}|{expiry_display}|{right}|{strike}"
        row = by_strike.get(key)
        if row:
            return row
    return None


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
    return {
        "stock_code": stock_code,
        "strike_price": safe_int(row.get("strike_price"), 0),
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
) -> dict[str, Any] | None:
    strikes = get_strikes(stock_code, expiry_display, exchange_code=exchange_code)
    if not strikes:
        return None
    lot_val = int(lot_size or 0)
    calls: list[dict[str, Any]] = []
    puts: list[dict[str, Any]] = []
    spot_price: float | None = None
    for strike in strikes:
        for right in (cfg.CALL, cfg.PUT):
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


def purge_old_bhav_versions(keep_version: int) -> None:
    from icici_breeze_backend.app.db.redis_client import cache_delete_pattern

    for seg in ("nfo", "bfo"):
        for v in range(1, keep_version):
            cache_delete_pattern(f"{version_prefix(v)}:bhav:{seg}:*")
