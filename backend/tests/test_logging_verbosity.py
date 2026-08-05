"""Tests for log-level resolution and the noise cuts in `app.core.logging`.

Production ran at INFO with no way to turn it down: `LOG_LEVEL` was read only from a
`.env` file the deployed container doesn't have, and `uvicorn.access` was pinned to
INFO regardless. These cover both, plus the probe-path filter.
"""
from __future__ import annotations

import importlib
import io
import logging

import pytest

from icici_breeze_backend.app.core.logging import (
    QuietAccessPathFilter,
    configure_logging,
)


@pytest.fixture(autouse=True)
def _restore_logging_state():
    """These tests mutate global logging state; put it back for the rest of the suite."""
    root = logging.getLogger()
    saved_root = (list(root.handlers), root.level)
    names = ("uvicorn", "uvicorn.error", "uvicorn.access")
    saved = {
        name: (
            list(logging.getLogger(name).handlers),
            list(logging.getLogger(name).filters),
            logging.getLogger(name).level,
            logging.getLogger(name).propagate,
        )
        for name in names
    }
    yield
    for handler in list(root.handlers):
        root.removeHandler(handler)
    for handler in saved_root[0]:
        root.addHandler(handler)
    root.setLevel(saved_root[1])
    for name, (handlers, filters, level, propagate) in saved.items():
        logger = logging.getLogger(name)
        logger.handlers, logger.filters = list(handlers), list(filters)
        logger.setLevel(level)
        logger.propagate = propagate


def _fresh_console(level: str) -> io.StringIO:
    """Reconfigure logging onto a captured stream and return it.

    Root handlers are cleared first because `configure_logging` deliberately reuses an
    existing console handler — without this it would keep writing to the real stderr.
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    stream = io.StringIO()
    configure_logging(level=level, stream=stream)
    return stream


def _access_record(path: str) -> logging.LogRecord:
    """A record shaped the way uvicorn.access emits them."""
    return logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:1234", "GET", path, "1.1", 200),
        None,
    )


class TestQuietAccessPathFilter:
    @pytest.mark.parametrize("path", ["/health", "/metrics"])
    def test_drops_probe_paths(self, path):
        assert QuietAccessPathFilter().filter(_access_record(path)) is False

    def test_keeps_real_traffic(self):
        assert QuietAccessPathFilter().filter(_access_record("/portfolio/data")) is True

    def test_ignores_query_string_when_matching(self):
        assert QuietAccessPathFilter().filter(_access_record("/health?probe=1")) is False

    def test_falls_back_to_message_when_arg_shape_changes(self):
        # A uvicorn upgrade that changes the args tuple must not crash the filter.
        record = logging.LogRecord(
            "uvicorn.access", logging.INFO, __file__, 1,
            '127.0.0.1 - "GET /health HTTP/1.1" 200', (), None,
        )
        assert QuietAccessPathFilter().filter(record) is False

    def test_never_raises_on_unformattable_record(self):
        record = logging.LogRecord(
            "uvicorn.access", logging.INFO, __file__, 1, "%d", ("not-an-int",), None,
        )
        assert QuietAccessPathFilter().filter(record) is True


class TestConfigureLogging:
    def test_access_lines_are_not_printed_at_warning(self):
        # Pinned to INFO, LOG_LEVEL could never quieten the per-request lines.
        console = _fresh_console(level="WARNING")
        logging.getLogger("uvicorn.access").handle(_access_record("/portfolio/data"))
        assert "/portfolio/data" not in console.getvalue()

    def test_access_lines_are_printed_at_info(self):
        console = _fresh_console(level="INFO")
        logging.getLogger("uvicorn.access").handle(_access_record("/portfolio/data"))
        assert "/portfolio/data" in console.getvalue()

    def test_probe_paths_stay_out_even_at_info(self):
        console = _fresh_console(level="INFO")
        logging.getLogger("uvicorn.access").handle(_access_record("/health"))
        assert "/health" not in console.getvalue()

    @pytest.mark.parametrize("name", ["uvicorn", "uvicorn.error", "uvicorn.access"])
    def test_uvicorn_loggers_route_through_root_handlers(self, name):
        # uvicorn gives these their own handlers with propagate=False, which bypasses
        # the console level, the redaction filter and the error-context buffer.
        logger = logging.getLogger(name)
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
        configure_logging(level="WARNING")
        assert logger.propagate is True
        assert logger.handlers == []

    def test_parent_uvicorn_logger_does_not_swallow_access_records(self):
        # `uvicorn` holds a handler *and* propagate=False. Left alone, access lines
        # print there and never reach root — so the console level stops gating them.
        parent = logging.getLogger("uvicorn")
        parent.addHandler(logging.StreamHandler(io.StringIO()))
        parent.propagate = False
        console = _fresh_console(level="WARNING")
        logging.getLogger("uvicorn.access").handle(_access_record("/portfolio/data"))
        assert parent.handlers == []
        assert "/portfolio/data" not in console.getvalue()

    def test_access_level_not_held_up_by_parent_uvicorn_logger(self):
        # Level lookup walks up the hierarchy: a parent left at INFO would pin the
        # child above the root level no matter what the child itself says.
        logging.getLogger("uvicorn").setLevel(logging.INFO)
        configure_logging(level="WARNING")
        access = logging.getLogger("uvicorn.access")
        assert access.getEffectiveLevel() == logging.getLogger().level

    def test_access_lines_are_captured_as_error_context(self):
        # Quiet on the console, but still available when something actually fails —
        # "which request was in flight" is the first thing you want after an error.
        console = _fresh_console(level="WARNING")
        logging.getLogger("uvicorn.access").handle(_access_record("/order/place"))
        logging.getLogger("test.app").error("order submission failed")
        assert "/order/place" in console.getvalue()

    def test_lifecycle_lines_follow_the_console_level(self):
        # Deliberate: uvicorn's startup lines are gated like everything else rather than
        # force-printed. LOG_LEVEL defaults to INFO, so they stay visible unless an
        # operator opts into WARNING — and an error still replays them from the buffer.
        console = _fresh_console(level="INFO")
        logging.getLogger("uvicorn.error").info("Application startup complete.")
        assert "Application startup complete." in console.getvalue()

        console = _fresh_console(level="WARNING")
        logging.getLogger("uvicorn.error").info("Application startup complete.")
        assert "Application startup complete." not in console.getvalue()

    def test_lifecycle_lines_replay_as_error_context(self):
        console = _fresh_console(level="WARNING")
        logging.getLogger("uvicorn.error").info("Application startup complete.")
        logging.getLogger("test.app").error("boom")
        assert "Application startup complete." in console.getvalue()

    def test_probe_filter_attached_once_when_called_repeatedly(self):
        configure_logging(level="INFO")
        configure_logging(level="INFO")
        access = logging.getLogger("uvicorn.access")
        attached = [f for f in access.filters if isinstance(f, QuietAccessPathFilter)]
        assert len(attached) == 1


class TestLogConfigResolution:
    """`main._get_log_config`: .env file wins, os.environ is the fallback."""

    @staticmethod
    def _main():
        return importlib.import_module("icici_breeze_backend.main")

    def test_reads_from_environ_when_no_env_file(self, monkeypatch):
        main = self._main()
        monkeypatch.setattr(main, "_env_paths_tried", [])
        monkeypatch.setenv("LOG_LEVEL", "WARNING")
        assert main._get_log_config()["LOG_LEVEL"] == "WARNING"

    def test_env_file_wins_over_environ(self, monkeypatch, tmp_path):
        main = self._main()
        env_file = tmp_path / ".env"
        env_file.write_text("LOG_LEVEL=ERROR\n", encoding="utf-8")
        monkeypatch.setattr(main, "_env_paths_tried", [str(env_file)])
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        assert main._get_log_config()["LOG_LEVEL"] == "ERROR"

    def test_environ_fills_key_absent_from_env_file(self, monkeypatch, tmp_path):
        main = self._main()
        env_file = tmp_path / ".env"
        env_file.write_text("LOG_LEVEL=ERROR\n", encoding="utf-8")
        monkeypatch.setattr(main, "_env_paths_tried", [str(env_file)])
        monkeypatch.setenv("LOG_FILE", "/tmp/breeze.log")
        config = main._get_log_config()
        assert config["LOG_LEVEL"] == "ERROR"
        assert config["LOG_FILE"] == "/tmp/breeze.log"

    def test_blank_environ_value_is_ignored(self, monkeypatch):
        main = self._main()
        monkeypatch.setattr(main, "_env_paths_tried", [])
        monkeypatch.setenv("LOG_LEVEL", "   ")
        assert "LOG_LEVEL" not in main._get_log_config()
