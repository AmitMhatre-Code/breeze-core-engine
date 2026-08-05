"""Tests for the error-triggered log context buffer (`app.core.log_buffer`)."""
from __future__ import annotations

import logging

import pytest

from icici_breeze_backend.app.core.log_buffer import (
    ErrorContextBufferHandler,
    capture_level_for,
)
from icici_breeze_backend.app.core.logging import configure_logging


class RecordingHandler(logging.Handler):
    """Stands in for the console handler; records what it was asked to emit."""

    def __init__(self, level=logging.WARNING):
        super().__init__(level=level)
        self.messages: list[str] = []

    def emit(self, record):
        self.messages.append(record.getMessage())


@pytest.fixture
def target():
    return RecordingHandler()


@pytest.fixture
def buffer_handler(target):
    return ErrorContextBufferHandler(target, capacity=5, cooldown_seconds=0.0)


def _record(level, msg, args=(), name="test"):
    return logging.LogRecord(name, level, __file__, 1, msg, args, None)


class TestBuffering:
    def test_sub_threshold_records_are_not_printed(self, buffer_handler, target):
        buffer_handler.emit(_record(logging.INFO, "quiet"))
        assert target.messages == []
        assert buffer_handler.buffered_count() == 1

    def test_records_the_target_prints_are_not_buffered(self, buffer_handler):
        # Otherwise a flush would print them a second time, out of order.
        buffer_handler.emit(_record(logging.WARNING, "already printed"))
        assert buffer_handler.buffered_count() == 0

    def test_buffer_evicts_oldest_beyond_capacity(self, buffer_handler, target):
        for i in range(8):
            buffer_handler.emit(_record(logging.INFO, "line %d", (i,)))
        assert buffer_handler.buffered_count() == 5
        buffer_handler.emit(_record(logging.ERROR, "boom"))
        assert "line 3" in target.messages
        assert "line 2" not in target.messages


class TestFlush:
    def test_error_flushes_preceding_context_in_order(self, buffer_handler, target):
        buffer_handler.emit(_record(logging.INFO, "first"))
        buffer_handler.emit(_record(logging.INFO, "second"))
        buffer_handler.emit(_record(logging.ERROR, "boom"))
        assert "first" in target.messages
        assert target.messages.index("first") < target.messages.index("second")

    def test_flush_is_wrapped_in_markers(self, buffer_handler, target):
        buffer_handler.emit(_record(logging.INFO, "context"))
        buffer_handler.emit(_record(logging.ERROR, "boom"))
        assert "2 buffered log line(s)" not in target.messages[0]
        assert "1 buffered log line(s)" in target.messages[0]
        assert "end buffered context" in target.messages[-1]

    def test_buffer_is_empty_after_flush(self, buffer_handler):
        buffer_handler.emit(_record(logging.INFO, "context"))
        buffer_handler.emit(_record(logging.ERROR, "boom"))
        assert buffer_handler.buffered_count() == 0

    def test_error_itself_is_left_to_the_normal_handler(self, buffer_handler, target):
        # The buffer only replays context; the console emits the error line on its own.
        buffer_handler.emit(_record(logging.ERROR, "boom"))
        assert "boom" not in target.messages

    def test_no_markers_when_nothing_was_buffered(self, buffer_handler, target):
        buffer_handler.emit(_record(logging.ERROR, "boom"))
        assert target.messages == []


class TestCooldown:
    def test_second_error_within_cooldown_does_not_replay(self, target):
        handler = ErrorContextBufferHandler(target, capacity=5, cooldown_seconds=300.0)
        handler.emit(_record(logging.INFO, "first context"))
        handler.emit(_record(logging.ERROR, "boom"))
        target.messages.clear()
        handler.emit(_record(logging.INFO, "second context"))
        handler.emit(_record(logging.ERROR, "boom again"))
        assert target.messages == []

    def test_suppressed_flush_still_clears_stale_context(self, target):
        # Stale lines must not resurface attached to a later, unrelated error.
        handler = ErrorContextBufferHandler(target, capacity=5, cooldown_seconds=300.0)
        handler.emit(_record(logging.INFO, "context"))
        handler.emit(_record(logging.ERROR, "boom"))
        handler.emit(_record(logging.INFO, "stale"))
        handler.emit(_record(logging.ERROR, "boom again"))
        assert handler.buffered_count() == 0


class TestRecordDetaching:
    def test_message_is_rendered_before_buffering(self, buffer_handler, target):
        # The buffer must not hold references to the objects passed as args.
        payload = {"big": "object"}
        buffer_handler.emit(_record(logging.INFO, "payload=%s", (payload,)))
        buffer_handler.emit(_record(logging.ERROR, "boom"))
        assert f"payload={payload}" in target.messages

    def test_bad_format_string_does_not_break_logging(self, buffer_handler, target):
        buffer_handler.emit(_record(logging.INFO, "%d", ("not-an-int",)))
        buffer_handler.emit(_record(logging.ERROR, "boom"))
        assert any("unformattable" in m for m in target.messages)

    def test_traceback_is_preserved_as_text(self, buffer_handler, target):
        try:
            raise ValueError("original cause")
        except ValueError:
            record = _record(logging.INFO, "failed")
            import sys

            record.exc_info = sys.exc_info()
        buffer_handler.emit(record)
        assert buffer_handler.buffered_count() == 1
        buffered = list(buffer_handler._buffer)[0]
        assert buffered.exc_info is None
        assert "original cause" in buffered.exc_text


class TestConfigureLoggingIntegration:
    def test_root_stays_verbose_enough_to_feed_the_buffer(self):
        configure_logging(level="WARNING")
        root = logging.getLogger()
        assert root.level == logging.INFO
        console = [
            h for h in root.handlers
            if isinstance(h, logging.StreamHandler)
            and not isinstance(h, (logging.FileHandler, ErrorContextBufferHandler))
        ]
        assert console[0].level == logging.WARNING

    def test_buffer_handler_is_not_duplicated_across_calls(self):
        configure_logging(level="WARNING")
        configure_logging(level="WARNING")
        attached = [
            h for h in logging.getLogger().handlers
            if isinstance(h, ErrorContextBufferHandler)
        ]
        assert len(attached) == 1

    def test_opt_out_leaves_root_at_the_configured_level(self, monkeypatch):
        # The download sink also holds root at INFO, so both have to be off for the
        # configured level to be the only floor.
        monkeypatch.setenv("LOG_ERROR_CONTEXT", "off")
        monkeypatch.setenv("LOG_SINK", "off")
        configure_logging(level="WARNING")
        root = logging.getLogger()
        assert root.level == logging.WARNING
        assert not any(
            isinstance(h, ErrorContextBufferHandler) for h in root.handlers
        )

    def test_capture_level_never_exceeds_console_verbosity(self, monkeypatch):
        monkeypatch.delenv("LOG_CAPTURE_LEVEL", raising=False)
        assert capture_level_for(logging.DEBUG) == logging.DEBUG
        assert capture_level_for(logging.WARNING) == logging.INFO

    def test_log_file_handler_is_not_stacked_on_reentry(self, tmp_path):
        path = str(tmp_path / "app.log")
        configure_logging(level="INFO", log_file=path)
        configure_logging(level="INFO", log_file=path)
        # pytest attaches its own /dev/null FileHandler to root, so match on the path.
        import os

        attached = [
            h for h in logging.getLogger().handlers
            if isinstance(h, logging.FileHandler)
            and os.path.abspath(getattr(h, "baseFilename", "")) == os.path.abspath(path)
        ]
        assert len(attached) == 1


@pytest.fixture(autouse=True)
def _restore_root_logging():
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    yield
    for handler in list(root.handlers):
        root.removeHandler(handler)
    for handler in saved_handlers:
        root.addHandler(handler)
    root.setLevel(saved_level)
