"""Circuit breaker and resilience for ICICI API (Phase 6 T096)."""
import time
import threading
from typing import Callable, TypeVar

from core.errors import ServiceUnavailableError

T = TypeVar("T")


class BreakerClient:
    """Circuit breaker: open after failure_threshold failures within window_seconds."""

    def __init__(self, failure_threshold: int = 3, window_seconds: float = 60.0):
        self.failure_threshold = failure_threshold
        self.window_seconds = window_seconds
        self._failures: list[float] = []
        self._lock = threading.Lock()

    def _recent_failures(self) -> int:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            self._failures = [t for t in self._failures if t > cutoff]
            return len(self._failures)

    @property
    def is_open(self) -> bool:
        """True if circuit is open (too many failures in window)."""
        return self._recent_failures() >= self.failure_threshold

    def record_failure(self) -> None:
        with self._lock:
            self._failures.append(time.monotonic())

    def record_success(self) -> None:
        with self._lock:
            self._failures.clear()

    def call(self, func: Callable[..., T], *args, **kwargs) -> T:
        """Execute func; record failure on exception. Raises if circuit open or func fails."""
        if self.is_open:
            raise ServiceUnavailableError(
                "ICICI API circuit breaker is open; try again later"
            )
        try:
            result = func(*args, **kwargs)
            self.record_success()
            return result
        except Exception:
            self.record_failure()
            raise
