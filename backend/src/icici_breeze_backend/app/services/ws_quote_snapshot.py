"""Durable last-known-good option-chain quote snapshot, captured from live ticks.

Why this exists: BSE wipes market depth the moment the session closes, so the
post-close ICICI REST fallback returns zeroed `total_buy_qty`/`total_sell_qty`/
`best_bid_price`/`best_offer_price` for BFO contracts. It also only covers a
limited strike band. Bhavcopy has no depth columns at all for either exchange.

A scheduled scrape at close cannot fix this: the raw tick caches are TTL'd
(`WS_RAW_QUOTE_TTL_SECONDS`=300, `WEBSOCKET_QUOTE_TTL_SECONDS`=120), so by the
time a close-time job ran, the illiquid strikes -- exactly the ones whose depth
you most need -- would already have expired. Instead the snapshot is written
*continuously* from `ws_tick_pipeline._cache_loop`: every contract holds its own
genuine last tick, whenever that happened, and there is no timing window to miss.

Storage is two-tier: a no-TTL Redis hash per (exchange, trading date, underlying,
expiry) for reads, periodically flushed to SQLite so the snapshot survives an app
restart (portal-approved upgrades recreate the container) or Redis loss (Redis is
optional here and falls back to an in-process store).

Coverage limit worth knowing: only contracts that actually ticked are captured,
which means only chains someone subscribed to during the session (see
`reference_data/active_chains`). A chain nobody opened has no snapshot and falls
through to bhavcopy/REST.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import sqlite3
import threading
import time
from typing import Any

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.strike import Strike, parse_strike, strike_key
from icici_breeze_backend.app.core.timezone import IST, now_ist
from icici_breeze_backend.app.db.redis_client import cache_get_json, cache_set_json, get_redis

_logger = logging.getLogger(__name__)

SNAPSHOT_SOURCE = "snapshot"

_flush_lock = threading.Lock()


# --------------------------------------------------------------------------- config


def snapshot_enabled() -> bool:
    return bool(getattr(cfg, "WS_QUOTE_SNAPSHOT_ENABLED", True))


def _flush_interval_seconds() -> int:
    try:
        return max(30, int(getattr(cfg, "WS_QUOTE_SNAPSHOT_FLUSH_SECONDS", 300) or 300))
    except (TypeError, ValueError):
        return 300


def _retention_days() -> int:
    try:
        return max(1, int(getattr(cfg, "WS_QUOTE_SNAPSHOT_RETENTION_DAYS", 5) or 5))
    except (TypeError, ValueError):
        return 5


def _snapshot_db_path() -> str:
    return cfg.DATA_PATH + cfg.SCRIP_DB


# --------------------------------------------------------------------------- keys


def _date_key(trading_date: dt.date) -> str:
    return trading_date.isoformat()


def chain_hash_key(
    exchange_code: str,
    trading_date: dt.date,
    stock_code: str,
    expiry_display: str,
) -> str:
    return (
        f"quotes:snapshot:{exchange_code.upper()}:{_date_key(trading_date)}:"
        f"{stock_code.upper()}:{expiry_display}"
    )


def _index_key(trading_date: dt.date) -> str:
    """Set of every chain hash key written for a trading date, so flush/prune
    never has to SCAN (the in-process Redis fallback cannot scan hashes)."""
    return f"quotes:snapshot:index:{_date_key(trading_date)}"


def _meta_key(exchange_code: str, trading_date: dt.date) -> str:
    return f"quotes:snapshot:meta:{exchange_code.upper()}:{_date_key(trading_date)}"


def contract_field(strike: Strike, right: str) -> str:
    return f"{strike_key(strike)}:{_right_key(right)}"


def _right_key(right: str) -> str:
    r = str(right or "").strip().lower()
    if r in {"ce", "c", "call"}:
        return "call"
    return "put"


# --------------------------------------------------------------------------- dates


def latest_snapshot_trading_date(now: dt.datetime | None = None) -> dt.date:
    """The only session a snapshot may be served from -- mirrors
    `quote_source_router.bhavcopy_is_fresh`, so a Friday book is never served on
    Tuesday. Imported lazily: the router imports this module at module level."""
    from icici_breeze_backend.app.services.quote_source_router import (
        latest_concluded_trading_day,
    )

    return latest_concluded_trading_day(now)


# --------------------------------------------------------------------------- write


def record_cells(
    entries: list[tuple[str, str, str, Strike, str, dict[str, Any]]],
    *,
    trading_date: dt.date | None = None,
) -> int:
    """Persist normalized chain cells captured from live ticks.

    Entries are (exchange_code, stock_code, expiry_display, strike, right, cell).
    Called from the tick pipeline's cache loop on already-coalesced batches, so
    this runs at most once per ~`WS_TICK_COALESCE_MS` per contract, not per tick.
    """
    if not snapshot_enabled() or not entries:
        return 0
    trading_date = trading_date or now_ist().date()
    grouped: dict[tuple[str, str, str], dict[str, str]] = {}
    for exchange_code, stock_code, expiry_display, strike, right, cell in entries:
        if not isinstance(cell, dict):
            continue
        group = (exchange_code.upper(), stock_code.upper(), expiry_display)
        grouped.setdefault(group, {})[contract_field(strike, right)] = json.dumps(
            cell, separators=(",", ":"), default=str
        )
    if not grouped:
        return 0

    client = get_redis()
    written = 0
    exchanges: set[str] = set()
    for (exchange_code, stock_code, expiry_display), mapping in grouped.items():
        key = chain_hash_key(exchange_code, trading_date, stock_code, expiry_display)
        try:
            client.hset(key, mapping=mapping)
            client.sadd(_index_key(trading_date), key)
        except Exception:
            _logger.debug("Snapshot write failed for %s", key, exc_info=True)
            continue
        written += len(mapping)
        exchanges.add(exchange_code)

    # Cheap per-batch marker so `snapshot_is_fresh` never has to read a hash.
    for exchange_code in exchanges:
        cache_set_json(
            _meta_key(exchange_code, trading_date),
            {"updated_at": time.time(), "trading_date": _date_key(trading_date)},
        )
    return written


# --------------------------------------------------------------------------- read


def snapshot_is_fresh(exchange_code: str, now: dt.datetime | None = None) -> bool:
    """True when this exchange has a snapshot for the latest concluded session."""
    if not snapshot_enabled():
        return False
    trading_date = latest_snapshot_trading_date(now)
    meta = cache_get_json(_meta_key(exchange_code, trading_date))
    return isinstance(meta, dict) and bool(meta.get("updated_at"))


def snapshot_chain_cells(
    exchange_code: str,
    stock_code: str,
    expiry_display: str,
    *,
    now: dt.datetime | None = None,
) -> dict[str, dict[str, Any]]:
    """All snapshot cells for one chain, keyed by `contract_field`."""
    if not snapshot_enabled():
        return {}
    trading_date = latest_snapshot_trading_date(now)
    key = chain_hash_key(exchange_code, trading_date, stock_code, expiry_display)
    try:
        raw = get_redis().hgetall(key)
    except Exception:
        _logger.debug("Snapshot read failed for %s", key, exc_info=True)
        return {}
    out: dict[str, dict[str, Any]] = {}
    for field, value in (raw or {}).items():
        try:
            cell = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(cell, dict):
            out[str(field)] = cell
    return out


def snapshot_cell(
    exchange_code: str,
    stock_code: str,
    expiry_display: str,
    strike: Strike,
    right: str,
    *,
    now: dt.datetime | None = None,
) -> dict[str, Any] | None:
    if not snapshot_enabled():
        return None
    trading_date = latest_snapshot_trading_date(now)
    key = chain_hash_key(exchange_code, trading_date, stock_code, expiry_display)
    field = contract_field(strike, right)
    try:
        # hmget (not hget): the in-process Redis fallback implements hmget only.
        values = get_redis().hmget(key, [field])
    except Exception:
        _logger.debug("Snapshot cell read failed for %s", key, exc_info=True)
        return None
    if not values or values[0] is None:
        return None
    try:
        cell = json.loads(values[0])
    except (TypeError, json.JSONDecodeError):
        return None
    return cell if isinstance(cell, dict) else None


def cell_depth_as_of(cell: dict[str, Any]) -> str | None:
    """ISO timestamp of the tick a snapshot cell was captured from."""
    try:
        ts = float(cell.get("updated_at"))
    except (TypeError, ValueError):
        return None
    if ts <= 0:
        return None
    return dt.datetime.fromtimestamp(ts, tz=IST).isoformat()


# --------------------------------------------------------------------------- flush


def _iter_chain_keys(trading_date: dt.date) -> list[str]:
    try:
        return sorted(str(k) for k in get_redis().smembers(_index_key(trading_date)))
    except Exception:
        _logger.debug("Snapshot index read failed", exc_info=True)
        return []


def _parse_chain_key(key: str) -> tuple[str, str, str, str] | None:
    """`quotes:snapshot:{EX}:{DATE}:{STOCK}:{EXPIRY}` -> its four parts."""
    parts = key.split(":")
    if len(parts) != 6 or parts[0] != "quotes" or parts[1] != "snapshot":
        return None
    return parts[2], parts[3], parts[4], parts[5]


def flush_snapshot_to_sqlite(trading_date: dt.date | None = None) -> int:
    """Upsert the in-Redis snapshot into SQLite. Incremental and idempotent --
    run on an interval during the session so a crash costs minutes, not the day."""
    if not snapshot_enabled():
        return 0
    target_date = trading_date or now_ist().date()
    keys = _iter_chain_keys(target_date)
    if not keys:
        return 0

    rows: list[tuple[Any, ...]] = []
    for key in keys:
        parsed = _parse_chain_key(key)
        if parsed is None:
            continue
        exchange_code, date_key, stock_code, expiry_display = parsed
        try:
            raw = get_redis().hgetall(key)
        except Exception:
            continue
        for field, value in (raw or {}).items():
            field_str = str(field)
            strike_part, _, right_part = field_str.rpartition(":")
            if not strike_part:
                continue
            try:
                captured_at = float(json.loads(value).get("updated_at") or 0.0)
            except (TypeError, ValueError, json.JSONDecodeError):
                captured_at = 0.0
            rows.append(
                (
                    date_key,
                    exchange_code,
                    stock_code,
                    expiry_display,
                    strike_part,
                    right_part,
                    value,
                    captured_at,
                )
            )
    if not rows:
        return 0

    with _flush_lock:
        try:
            with sqlite3.connect(_snapshot_db_path()) as conn:
                conn.executemany(
                    """
                    INSERT INTO ws_quote_snapshot (
                        trading_date, exchange_code, stock_code, expiry_display,
                        strike_key, option_right, cell_json, captured_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(trading_date, exchange_code, stock_code,
                                expiry_display, strike_key, option_right)
                    DO UPDATE SET cell_json = excluded.cell_json,
                                  captured_at = excluded.captured_at
                    """,
                    rows,
                )
                conn.execute(
                    "DELETE FROM ws_quote_snapshot WHERE trading_date < ?",
                    ((target_date - dt.timedelta(days=_retention_days())).isoformat(),),
                )
                conn.commit()
        except sqlite3.Error:
            _logger.exception("Snapshot flush to SQLite failed")
            return 0
    _logger.info("Flushed %s snapshot cells for %s", len(rows), target_date)
    return len(rows)


def load_snapshot_from_sqlite(trading_date: dt.date | None = None) -> int:
    """Rehydrate Redis from SQLite at startup, so a restart (or a Redis wipe)
    doesn't lose the session's captured depth."""
    if not snapshot_enabled():
        return 0
    target_date = trading_date or latest_snapshot_trading_date()
    try:
        with sqlite3.connect(_snapshot_db_path()) as conn:
            cur = conn.execute(
                """
                SELECT exchange_code, stock_code, expiry_display,
                       strike_key, option_right, cell_json
                FROM ws_quote_snapshot WHERE trading_date = ?
                """,
                (target_date.isoformat(),),
            )
            fetched = cur.fetchall()
    except sqlite3.Error:
        _logger.exception("Snapshot reload from SQLite failed")
        return 0
    if not fetched:
        return 0

    client = get_redis()
    grouped: dict[str, dict[str, str]] = {}
    exchanges: set[str] = set()
    for exchange_code, stock_code, expiry_display, strike_part, right_part, cell_json in fetched:
        key = chain_hash_key(exchange_code, target_date, stock_code, expiry_display)
        grouped.setdefault(key, {})[f"{strike_part}:{right_part}"] = cell_json
        exchanges.add(str(exchange_code).upper())
    for key, mapping in grouped.items():
        try:
            client.hset(key, mapping=mapping)
            client.sadd(_index_key(target_date), key)
        except Exception:
            _logger.debug("Snapshot rehydrate failed for %s", key, exc_info=True)
    for exchange_code in exchanges:
        cache_set_json(
            _meta_key(exchange_code, target_date),
            {"updated_at": time.time(), "trading_date": target_date.isoformat()},
        )
    _logger.info("Reloaded %s snapshot cells for %s from SQLite", len(fetched), target_date)
    return len(fetched)


def prune_stale_redis_snapshots(keep_dates: set[str] | None = None) -> int:
    """Expire chain hashes for sessions we will never serve again. Uses EXPIRE
    rather than DEL because the in-process fallback's `delete` only covers plain
    keys, not hashes."""
    today = now_ist().date()
    keep = keep_dates or {
        today.isoformat(),
        latest_snapshot_trading_date().isoformat(),
    }
    client = get_redis()
    expired = 0
    for offset in range(1, _retention_days() + 2):
        old = today - dt.timedelta(days=offset)
        if old.isoformat() in keep:
            continue
        for key in _iter_chain_keys(old):
            try:
                client.expire(key, 60)
                client.srem(_index_key(old), key)
                expired += 1
            except Exception:
                continue
    return expired


async def run_snapshot_flush_loop() -> None:
    """Periodic incremental flush. Deliberately *not* a single close-time job:
    an interval flush means an app crash at 15:29 costs minutes of captured
    depth rather than the entire session."""
    if not snapshot_enabled():
        return
    while True:
        try:
            await asyncio.sleep(_flush_interval_seconds())
            await asyncio.to_thread(flush_snapshot_to_sqlite)
            await asyncio.to_thread(prune_stale_redis_snapshots)
        except asyncio.CancelledError:
            # Final flush on shutdown so an orderly restart loses nothing.
            try:
                await asyncio.to_thread(flush_snapshot_to_sqlite)
            except Exception:
                _logger.debug("Final snapshot flush failed", exc_info=True)
            raise
        except Exception:
            _logger.exception("Snapshot flush loop iteration failed")


def build_chain_from_snapshot(
    stock_code: str,
    expiry_display: str,
    exchange_code: str,
    *,
    lot_size: int | None = None,
    strikes: list[Strike] | None = None,
) -> dict[str, dict[str, Any]]:
    """Snapshot cells for a chain, keyed by (strike_key, right) for the merge in
    `quote_source_router`. Returns {} when nothing was captured for this chain."""
    cells = snapshot_chain_cells(exchange_code, stock_code, expiry_display)
    if not cells:
        return {}
    wanted: set[str] | None = None
    if strikes:
        wanted = {strike_key(s) for s in strikes}
    lot_val = int(lot_size or 0)
    out: dict[str, dict[str, Any]] = {}
    for field, cell in cells.items():
        strike_part, _, _right_part = field.rpartition(":")
        if wanted is not None and strike_part not in wanted:
            continue
        if parse_strike(strike_part) is None:
            continue
        cell = dict(cell)
        if lot_val:
            cell["lot_size"] = lot_val
        out[field] = cell
    return out
