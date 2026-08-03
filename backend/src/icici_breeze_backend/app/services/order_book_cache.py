"""Short-TTL cache for the Strategy Group order-book reads.

Scoped deliberately to the SG surfaces (`strategy_group_lifecycle`), not bolted onto
`processor.get_orders` itself. The Orders page calls that same method on an explicit user
refresh and must never be answered from a stale cache — "I clicked refresh and it showed
me the old book" is a worse bug than the one being fixed here. The SG paths are different:
they are *polled* on a timer, and on 2026-07-31 that polling spent 4236 of 4730 daily
broker calls (89.6%) re-reading a book that had not changed.

A 30s TTL is not a freshness compromise, because it is not the freshness mechanism. Real
order state arrives over the WS order feed, which calls `invalidate_user` — so a fill is
reflected on the next read, not up to 30s later. The TTL only bounds how long a *quiet*
book goes un-refetched, which is exactly the traffic that was wasteful.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Iterable

_TTL_SECONDS = 30.0

_lock = threading.Lock()
_entries: dict[tuple[str, str, str, str], tuple[float, dict[str, Any]]] = {}


def _key(
    user_id: str, window: tuple[str, str], exchanges: Iterable[str]
) -> tuple[str, str, str, str]:
    start, end = window
    return (user_id, start, end, ",".join(sorted({str(e).upper() for e in exchanges})))


def get_or_fetch(
    user_id: str,
    window: tuple[str, str],
    exchanges: Iterable[str],
    fetch: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    """Return a cached order-book response, or call `fetch` and cache its result.

    Only successful responses are cached. Caching an error would pin a transient broker
    failure in place for the whole TTL and make every SG surface look broken for 30s off
    one bad call.
    """
    key = _key(user_id, window, exchanges)
    now = time.monotonic()
    with _lock:
        hit = _entries.get(key)
        if hit is not None and now - hit[0] < _TTL_SECONDS:
            return hit[1]

    resp = fetch()

    if isinstance(resp, dict) and resp.get("Status") == 200:
        with _lock:
            _entries[key] = (time.monotonic(), resp)
    return resp


def invalidate_user(user_id: str) -> None:
    """Drop every cached window for one user.

    Called from the WS order-notification path: any order event means the book this user
    is caching may be wrong, and an SG deciding Completed or Reset off a stale read is
    precisely the class of error the cache must not introduce.
    """
    uid = str(user_id or "")
    if not uid:
        return
    with _lock:
        for key in [k for k in _entries if k[0] == uid]:
            _entries.pop(key, None)


def clear() -> None:
    """Test seam / process-wide reset."""
    with _lock:
        _entries.clear()
