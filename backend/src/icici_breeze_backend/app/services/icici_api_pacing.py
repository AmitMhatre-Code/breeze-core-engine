"""Universal per-user ICICI API call pacing (proactive spacing + 503 backoff)."""
from __future__ import annotations

import logging
import threading
import time

_logger = logging.getLogger(__name__)

_MAX_503_BACKOFF_SEC = 3.0


class GlobalIciciApiPacer:
    """Thread-safe register of last ICICI API call time per user."""

    _lock = threading.Lock()
    _last_call_mono: dict[str, float] = {}
    _consecutive_rate_limited: dict[str, int] = {}

    @classmethod
    def wait_for_slot(cls, user_id: str, base_spacing_sec: float, *, endpoint: str = "icici") -> None:
        base = max(0.0, float(base_spacing_sec))
        with cls._lock:
            now = time.monotonic()
            last = cls._last_call_mono.get(user_id, 0.0)
            wait = max(0.0, base - (now - last))
        if wait > 0:
            _logger.info(
                "ICICI API pacing: sleeping %.3fs before %s (user=%s)",
                wait,
                endpoint,
                user_id,
            )
            time.sleep(wait)

    @classmethod
    def mark_call_complete(cls, user_id: str) -> None:
        with cls._lock:
            cls._last_call_mono[user_id] = time.monotonic()

    @classmethod
    def on_success(cls, user_id: str) -> None:
        with cls._lock:
            cls._consecutive_rate_limited[user_id] = 0

    @classmethod
    def rate_limit_backoff(cls, user_id: str, base_spacing_sec: float, *, endpoint: str = "icici") -> float:
        base = max(0.0, float(base_spacing_sec))
        with cls._lock:
            n = cls._consecutive_rate_limited.get(user_id, 0) + 1
            cls._consecutive_rate_limited[user_id] = n
            backoff = min(_MAX_503_BACKOFF_SEC, base * (2 ** (n - 1)))
        _logger.warning(
            "ICICI rate limit %s: consecutive=%d backoff=%.3fs (user=%s)",
            endpoint,
            n,
            backoff,
            user_id,
        )
        return backoff

    @classmethod
    def reset_user(cls, user_id: str) -> None:
        with cls._lock:
            cls._last_call_mono.pop(user_id, None)
            cls._consecutive_rate_limited.pop(user_id, None)
