"""Keep recent context in memory and emit it only when something actually fails.

Running the console at WARNING keeps the terminal readable but throws away exactly the
lines you want when an error shows up — the ones *before* it. Escalating the level after
an ERROR doesn't fix that either: by then the cause has already gone unlogged.

So the console stays quiet while this handler holds the last N sub-threshold records in
memory. On an ERROR it flushes that buffer to the console first, then lets the error line
through — you get the run-up to the failure without paying for it in steady state.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from typing import Deque, Optional

DEFAULT_CAPACITY = 2000
DEFAULT_COOLDOWN_SECONDS = 60.0
DEFAULT_CAPTURE_LEVEL = logging.INFO

_FLUSH_HEADER = "--- %d buffered log line(s) leading up to the error above ---"
_FLUSH_FOOTER = "--- end buffered context ---"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name) or default)
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name) or default)
    except (TypeError, ValueError):
        return default


def buffer_enabled() -> bool:
    return (os.environ.get("LOG_ERROR_CONTEXT", "").strip().lower()
            not in ("0", "false", "no", "off"))


def _detach_record(record: logging.LogRecord) -> logging.LogRecord:
    """Render a record's message now so the buffer holds strings, not live objects.

    A buffered record keeps a reference to everything passed as a `%s` arg. On a 2 GiB
    box, 2000 records pinning option chains or DataFrames is its own outage. Formatting
    up front costs a little CPU on a path that was going to format anyway if an error
    follows, and bounds what the buffer can retain.
    """
    try:
        record.msg = record.getMessage()
    except Exception:  # noqa: BLE001 - a bad format string must not break logging
        record.msg = f"<unformattable log record: {record.msg!r}>"
    record.args = None
    if record.exc_info:
        if not record.exc_text:
            try:
                record.exc_text = logging.Formatter().formatException(record.exc_info)
            except Exception:  # noqa: BLE001
                record.exc_text = "<unformattable traceback>"
        record.exc_info = None
    return record


class ErrorContextBufferHandler(logging.Handler):
    """Buffers sub-threshold records; replays them to `target` when an error lands."""

    def __init__(
        self,
        target: logging.Handler,
        capacity: int = DEFAULT_CAPACITY,
        trigger_level: int = logging.ERROR,
        cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
    ) -> None:
        super().__init__(level=logging.NOTSET)
        self._target = target
        self._capacity = max(1, capacity)
        self._trigger_level = trigger_level
        self._cooldown_seconds = max(0.0, cooldown_seconds)
        self._buffer: Deque[logging.LogRecord] = deque(maxlen=self._capacity)
        self._buffer_lock = threading.Lock()
        self._last_flush_at: Optional[float] = None

    @property
    def target(self) -> logging.Handler:
        return self._target

    def buffered_count(self) -> int:
        with self._buffer_lock:
            return len(self._buffer)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if record.levelno >= self._trigger_level:
                self._flush()
                return
            # Anything the console prints itself must not be buffered, or a flush would
            # print it a second time out of order.
            if record.levelno >= self._target.level:
                return
            with self._buffer_lock:
                self._buffer.append(_detach_record(record))
        except Exception:  # noqa: BLE001 - logging must never take the app down
            self.handleError(record)

    def _flush(self) -> None:
        """Replay buffered records to the target, oldest first. Cleared either way.

        The buffer is dropped even when the cooldown suppresses the replay: those lines
        precede an error that has already been reported, so replaying them later would
        attach stale context to an unrelated failure.
        """
        now = time.monotonic()
        with self._buffer_lock:
            records = list(self._buffer)
            self._buffer.clear()
            suppressed = (
                self._last_flush_at is not None
                and (now - self._last_flush_at) < self._cooldown_seconds
            )
            if suppressed:
                return
            self._last_flush_at = now
        if not records:
            return
        self._emit_marker(_FLUSH_HEADER % len(records), records[0])
        for buffered in records:
            self._target.handle(buffered)
        self._emit_marker(_FLUSH_FOOTER, records[-1])

    def _emit_marker(self, message: str, like: logging.LogRecord) -> None:
        marker = logging.LogRecord(
            name="log.context", level=self._trigger_level, pathname=like.pathname,
            lineno=0, msg=message, args=None, exc_info=None,
        )
        self._target.handle(marker)

    def close(self) -> None:
        with self._buffer_lock:
            self._buffer.clear()
        super().close()


def capture_level_for(console_level: int) -> int:
    """Root level needed so the buffer sees records the console filters out.

    Capped at the console level: if someone already runs DEBUG there is nothing quieter
    to capture, and INFO rather than DEBUG by default so the buffer doesn't force every
    DEBUG record in the codebase to be constructed on a small instance.
    """
    return min(console_level, _env_int("LOG_CAPTURE_LEVEL", DEFAULT_CAPTURE_LEVEL))


def build_handler(target: logging.Handler) -> ErrorContextBufferHandler:
    return ErrorContextBufferHandler(
        target,
        capacity=_env_int("LOG_ERROR_CONTEXT_LINES", DEFAULT_CAPACITY),
        cooldown_seconds=_env_float(
            "LOG_ERROR_CONTEXT_COOLDOWN_SECONDS", DEFAULT_COOLDOWN_SECONDS
        ),
    )
