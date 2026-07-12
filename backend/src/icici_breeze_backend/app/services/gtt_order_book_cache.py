"""Short-lived cache of the account's full raw GTT order book (both exchanges), so the
Order Book page's exit-rule exclusion pass doesn't call ICICI on every load.

Keyed by user_id. TTL 60s — same idea as `broker_snapshot_cache.py`, simpler shape
(a plain list of raw rows rather than a dataclass) since the only consumer is the
exit-rule order-matching service.
"""
from __future__ import annotations

import threading
import time
from typing import Any

_TTL_SECONDS = 60.0

_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_lock = threading.Lock()


def get(user_id: str) -> list[dict[str, Any]] | None:
    if not user_id:
        return None
    with _lock:
        entry = _cache.get(user_id)
        if entry is None:
            return None
        expires_at, rows = entry
        if time.monotonic() >= expires_at:
            del _cache[user_id]
            return None
        return rows


def set_rows(user_id: str, rows: list[dict[str, Any]]) -> None:
    if not user_id:
        return
    with _lock:
        _cache[user_id] = (time.monotonic() + _TTL_SECONDS, rows)


def evict(user_id: str) -> None:
    with _lock:
        _cache.pop(user_id, None)
