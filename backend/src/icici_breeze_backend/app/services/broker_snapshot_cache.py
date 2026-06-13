"""Short-lived cache of broker snapshots from login (customer, margin, session_token).

Keyed by (user_id, broker_token). TTL 60s — covers login redirect → dashboard mount.
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

_logger = logging.getLogger(__name__)

_TTL_SECONDS = 60.0


def _cache_key(user_id: str, broker_token: str) -> str:
    raw = f"{user_id}:{broker_token}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass
class BrokerSnapshot:
    customer_details: dict[str, Any]
    margin_situation: dict[str, Any]
    customerdetails_session_token: str


class _Entry:
    __slots__ = ("snapshot", "expires_at")

    def __init__(self, snapshot: BrokerSnapshot, expires_at: float):
        self.snapshot = snapshot
        self.expires_at = expires_at


_cache: dict[str, _Entry] = {}
_lock = threading.Lock()


def set_snapshot(
    user_id: str,
    broker_token: str,
    *,
    customer_details: dict[str, Any],
    margin_situation: dict[str, Any],
    customerdetails_session_token: str = "",
) -> None:
    if not user_id or not broker_token:
        return
    key = _cache_key(user_id, broker_token)
    snap = BrokerSnapshot(
        customer_details=dict(customer_details or {}),
        margin_situation=dict(margin_situation or {}),
        customerdetails_session_token=(customerdetails_session_token or "").strip(),
    )
    expires_at = time.monotonic() + _TTL_SECONDS
    with _lock:
        _cache[key] = _Entry(snap, expires_at)
    _logger.debug("broker_snapshot_cache: set user=%s ttl=%.0fs", user_id, _TTL_SECONDS)


def get_snapshot(user_id: str, broker_token: str) -> Optional[BrokerSnapshot]:
    if not user_id or not broker_token:
        return None
    key = _cache_key(user_id, broker_token)
    with _lock:
        entry = _cache.get(key)
        if entry is None:
            return None
        if time.monotonic() >= entry.expires_at:
            del _cache[key]
            return None
        return entry.snapshot


def evict(user_id: str, broker_token: str) -> None:
    if not user_id or not broker_token:
        return
    key = _cache_key(user_id, broker_token)
    with _lock:
        if key in _cache:
            del _cache[key]
            _logger.debug("broker_snapshot_cache: evicted user=%s", user_id)


def evict_broker_snapshot(user_id: str, broker_token: str) -> None:
    """Evict cached login snapshot so /home/data refetches fresh margin."""
    evict(user_id, broker_token)
