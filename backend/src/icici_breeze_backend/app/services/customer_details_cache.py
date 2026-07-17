"""Per-process in-memory cache of ICICI get_customer_details() responses.

Keyed by (user_id, broker_token). Customer details don't change within a broker
session, and the session itself expires at end-of-day IST -- so this uses the same
TTL-till-midnight-IST convention as breeze_session_cache instead of hitting ICICI
on every order placement, settings/admin page load, etc.
"""
import hashlib
import logging
import threading
import time
from typing import Any, Optional

from icici_breeze_backend.app.services.breeze_session_cache import _ttl_seconds

_logger = logging.getLogger(__name__)


def _cache_key(user_id: str, broker_token: str) -> str:
    raw = f"{user_id}:{broker_token}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class _CacheEntry:
    __slots__ = ("customer", "expires_at")

    def __init__(self, customer: Any, expires_at: float):
        self.customer = customer
        self.expires_at = expires_at


_cache: dict[str, _CacheEntry] = {}
_lock = threading.Lock()


def get(user_id: str, broker_token: str) -> Optional[Any]:
    """Return cached customer details if present and not expired; else None."""
    if not broker_token:
        return None
    key = _cache_key(user_id, broker_token)
    with _lock:
        entry = _cache.get(key)
        if entry is None:
            return None
        if time.monotonic() >= entry.expires_at:
            del _cache[key]
            return None
        return entry.customer


def set(user_id: str, broker_token: str, customer: Any) -> None:
    """Store customer details for (user_id, broker_token) with TTL until midnight IST."""
    if not broker_token:
        return
    key = _cache_key(user_id, broker_token)
    ttl = _ttl_seconds()
    expires_at = time.monotonic() + ttl
    with _lock:
        _cache[key] = _CacheEntry(customer, expires_at)
    _logger.debug("customer_details_cache: set key=%s ttl=%.0fs", key[:16], ttl)


def evict(user_id: str, broker_token: str) -> None:
    """Remove cached customer details for (user_id, broker_token). Call on logout or ICICI auth failure."""
    if not broker_token:
        return
    key = _cache_key(user_id, broker_token)
    with _lock:
        if key in _cache:
            del _cache[key]
            _logger.debug("customer_details_cache: evicted key=%s", key[:16])
