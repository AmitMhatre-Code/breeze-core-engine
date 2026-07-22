"""Redis-backed registry of option chains with active WS subscribers."""
from __future__ import annotations

import logging
import threading
from typing import Any

from icici_breeze_backend.app.db.redis_client import get_redis

_logger = logging.getLogger(__name__)

CHAIN_ACTIVE_SET = "chain:active"

_lock = threading.RLock()
_chain_refcount: dict[str, int] = {}
_holder_chains: dict[str, set[str]] = {}


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
    """Clear Redis chain:active and in-process holder refcounts (API startup)."""
    with _lock:
        _chain_refcount.clear()
        _holder_chains.clear()
    try:
        get_redis().delete(CHAIN_ACTIVE_SET)
    except Exception:
        _logger.debug("reset_active_chains_registry failed", exc_info=True)
