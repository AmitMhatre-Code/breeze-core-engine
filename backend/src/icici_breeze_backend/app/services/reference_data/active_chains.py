"""Redis-backed registry of option chains with active WS subscribers."""
from __future__ import annotations

import datetime as dt
import logging
import threading
from datetime import date
from typing import Any

from icici_breeze_backend.app.db.redis_client import get_redis

_logger = logging.getLogger(__name__)

CHAIN_ACTIVE_SET = "chain:active"

# Often enough to catch a dead expiry registered mid-session (the order book pins one
# whenever an older date range is viewed), rare enough to be invisible.
_SWEEP_INTERVAL_SECONDS = 900.0

_lock = threading.RLock()
_chain_refcount: dict[str, int] = {}
_holder_chains: dict[str, set[str]] = {}
_last_full_reset_date: date | None = None


def chain_registry_key(exchange_code: str, stock_code: str, expiry_display: str) -> str:
    return f"{exchange_code.upper()}|{stock_code.upper()}|{expiry_display}"


def parse_chain_registry_key(key: str) -> tuple[str, str, str] | None:
    parts = str(key or "").split("|", 2)
    if len(parts) != 3:
        return None
    exchange, stock, expiry = parts
    if not exchange or not stock or not expiry:
        return None
    return exchange, stock, expiry


def _redis_sadd(chain_key: str) -> None:
    try:
        get_redis().sadd(CHAIN_ACTIVE_SET, chain_key)
    except Exception:
        _logger.debug("active chain SADD failed for %s", chain_key, exc_info=True)


def _redis_srem(chain_key: str) -> None:
    try:
        get_redis().srem(CHAIN_ACTIVE_SET, chain_key)
    except Exception:
        _logger.debug("active chain SREM failed for %s", chain_key, exc_info=True)


def register_holder_chain(
    holder_id: str,
    exchange_code: str,
    stock_code: str,
    expiry_display: str,
) -> None:
    """Mark chain as active while holder_id is subscribed."""
    hid = str(holder_id or "").strip()
    if not hid:
        return
    chain_key = chain_registry_key(exchange_code, stock_code, expiry_display)
    with _lock:
        chains = _holder_chains.setdefault(hid, set())
        if chain_key in chains:
            return
        chains.add(chain_key)
        count = _chain_refcount.get(chain_key, 0) + 1
        _chain_refcount[chain_key] = count
        if count == 1:
            _redis_sadd(chain_key)


def release_holder_chains(holder_id: str) -> int:
    """Drop all active chain registrations for holder_id. Returns chains released."""
    hid = str(holder_id or "").strip()
    if not hid:
        return 0
    with _lock:
        chain_keys = list(_holder_chains.pop(hid, set()))
    released = 0
    for chain_key in chain_keys:
        with _lock:
            count = _chain_refcount.get(chain_key, 0) - 1
            if count <= 0:
                _chain_refcount.pop(chain_key, None)
                _redis_srem(chain_key)
            else:
                _chain_refcount[chain_key] = count
        released += 1
    return released


def list_active_chains() -> list[str]:
    try:
        members = get_redis().smembers(CHAIN_ACTIVE_SET)
        if not members:
            return []
        return sorted(str(m) for m in members)
    except Exception:
        _logger.debug("list_active_chains failed", exc_info=True)
        with _lock:
            return sorted(k for k, c in _chain_refcount.items() if c > 0)


def active_chain_stats() -> dict[str, Any]:
    with _lock:
        local = dict(_chain_refcount)
    return {"active_chains": list_active_chains(), "local_refcounts": local}


def reset_active_chains_registry() -> None:
    """Clear Redis chain:active and in-process holder refcounts (API startup).

    Both halves must be cleared together: `register_holder_chain` early-returns when
    its in-process bookkeeping already lists the chain for that holder, so clearing
    only the Redis set would leave chains that can never re-register.
    """
    with _lock:
        _chain_refcount.clear()
        _holder_chains.clear()
        global _last_full_reset_date
        _last_full_reset_date = _today_ist()
    try:
        get_redis().delete(CHAIN_ACTIVE_SET)
    except Exception:
        _logger.debug("reset_active_chains_registry failed", exc_info=True)


def _today_ist() -> "date":
    from icici_breeze_backend.app.core.timezone import IST

    return dt.datetime.now(IST).date()


def _drop_chain_everywhere(chain_key: str) -> None:
    """Remove one chain from every holder's set, the refcounts, and Redis."""
    with _lock:
        for chains in _holder_chains.values():
            chains.discard(chain_key)
        _chain_refcount.pop(chain_key, None)
    _redis_srem(chain_key)


def sweep_expired_active_chains() -> int:
    """Drop chains whose expiry has already passed. Returns the number dropped.

    Nothing releases these on their own: `register_holder_chain` only ever adds, and
    the order book legitimately registers chains for *past* expiries whenever someone
    views an older date range -- which then stay in the rebuild set forever.
    """
    today = _today_ist()
    dropped = 0
    for chain_key in list_active_chains():
        parsed = parse_chain_registry_key(chain_key)
        if parsed is None:
            continue
        try:
            expiry = dt.datetime.strptime(parsed[2], "%d-%b-%Y").date()
        except (TypeError, ValueError):
            continue  # unparseable -> leave alone rather than guess
        if expiry >= today:
            continue
        _drop_chain_everywhere(chain_key)
        dropped += 1
    if dropped:
        _logger.info("active-chain sweep: dropped %s expired chain(s)", dropped)
    return dropped


def maybe_daily_reset_active_chains() -> bool:
    """Once per calendar day (IST), clear the registry outright so a long-running
    instance starts each session with only what is actually asked for again.

    No-ops on an instance that booted today, because startup already reset and
    stamped the date -- which is the common case here, since many deployments are
    shut down overnight and booted the next working morning. This exists for the
    instances that stay up for weeks.
    """
    today = _today_ist()
    with _lock:
        if _last_full_reset_date == today:
            return False
    _logger.info("active-chain registry: daily reset")
    reset_active_chains_registry()
    return True


async def run_active_chain_sweep_loop() -> None:
    """Periodic registry hygiene: expired-expiry sweep plus the once-daily reset.

    Mirrors the heartbeat / pnl-loop idiom: infinite loop cancelled only by the
    lifespan's `task.cancel()`, `CancelledError` re-raised, everything else logged
    and swallowed. Runs in the API process, never the worker -- the worker sees only
    the Redis set, so a removal there would desync the in-process refcounts.
    """
    import asyncio

    while True:
        try:
            await asyncio.sleep(_SWEEP_INTERVAL_SECONDS)
            await asyncio.to_thread(maybe_daily_reset_active_chains)
            await asyncio.to_thread(sweep_expired_active_chains)
        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.exception("active-chain sweep failed")
