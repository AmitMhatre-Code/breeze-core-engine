"""Per-process in-memory Breeze session cache. Keyed by (user_id, broker_token).
TTL: configurable seconds or until end of day IST. Invalidated on ICICI auth failure and on logout.
Portfolio/order data are NOT cached; only the Breeze session (auth) is cached (FR-010).
"""
import hashlib
import logging
import threading
import time
from typing import Any, Optional

from icici_breeze_backend.app.core.timezone import now_ist

_logger = logging.getLogger(__name__)

# Default: use end of day IST as TTL cap. Override with BREEZE_SESSION_CACHE_TTL_SECONDS (e.g. 14400 for 4h).


def _cache_key(user_id: str, broker_token: str) -> str:
    """Stable key so same user+token always hits same entry; new token after re-login gets new entry."""
    raw = f"{user_id}:{broker_token}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _seconds_until_midnight_ist() -> float:
    """Seconds from now until next midnight IST (capped at 24h)."""
    now = now_ist()
    from datetime import timedelta
    next_midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    delta = (next_midnight - now).total_seconds()
    return min(max(0, delta), 86400)


def _ttl_seconds() -> float:
    """TTL in seconds: from config if set, else until midnight IST."""
    try:
        import icici_breeze_backend.app.core.config as cfg
        ttl = getattr(cfg, "BREEZE_SESSION_CACHE_TTL_SECONDS", None)
        if ttl is not None and isinstance(ttl, (int, float)) and ttl > 0:
            return float(ttl)
    except Exception:
        pass
    return _seconds_until_midnight_ist()


class _CacheEntry:
    __slots__ = ("session", "expires_at")

    def __init__(self, session: Any, expires_at: float):
        self.session = session
        self.expires_at = expires_at


_cache: dict[str, _CacheEntry] = {}
_lock = threading.Lock()


def get(user_id: str, broker_token: str) -> Optional[Any]:
    """Return cached BreezeConnect session if present and not expired; else None."""
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
        return entry.session


def set(user_id: str, broker_token: str, session: Any) -> None:
    """Store BreezeConnect session for (user_id, broker_token) with TTL."""
    if not broker_token:
        return
    key = _cache_key(user_id, broker_token)
    ttl = _ttl_seconds()
    expires_at = time.monotonic() + ttl
    with _lock:
        _cache[key] = _CacheEntry(session, expires_at)
    _logger.debug("breeze_session_cache: set key=%s ttl=%.0fs", key[:16], ttl)


def evict(user_id: str, broker_token: str) -> None:
    """Remove cached session for (user_id, broker_token). Call on logout or ICICI auth failure."""
    if not broker_token:
        return
    key = _cache_key(user_id, broker_token)
    with _lock:
        if key in _cache:
            del _cache[key]
            _logger.debug("breeze_session_cache: evicted key=%s", key[:16])


def is_icici_auth_failure(response: Optional[dict]) -> bool:
    """True if ICICI API response indicates session/auth invalid (evict cache)."""
    if not response or not isinstance(response, dict):
        return False
    status = response.get("Status") or response.get("status")
    err = (response.get("Error") or response.get("error") or "").lower()
    if status == 401:
        return True
    if status is not None and status != 200 and any(
        x in err for x in ("session invalid", "invalid checksum", "unauthorized", "token", "session expired")
    ):
        return True
    return False


def evict_if_icici_auth_failure(user_id: str, broker_token: str, response: Optional[dict]) -> None:
    """If response indicates ICICI auth/session failure, evict cache for this user/token."""
    if is_icici_auth_failure(response):
        evict(user_id, broker_token)
