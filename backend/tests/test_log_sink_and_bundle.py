"""Tests for the on-disk log sink and the downloadable bundle it feeds."""
from __future__ import annotations

import io
import json
import logging
import os
import time
import zipfile
from logging.handlers import RotatingFileHandler

import pytest

from icici_breeze_backend.app.core import log_sink
from icici_breeze_backend.app.core.logging import configure_logging
from icici_breeze_backend.app.services import log_bundle


@pytest.fixture
def sink_dir(tmp_path, monkeypatch):
    directory = tmp_path / "logs"
    directory.mkdir()
    monkeypatch.setattr(log_sink, "logs_dir", lambda: str(directory))
    monkeypatch.setattr(log_bundle, "logs_dir", lambda: str(directory))
    return directory


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


def _write(directory, name, content="{}\n", age_days=0.0):
    path = directory / name
    path.write_text(content, encoding="utf-8")
    if age_days:
        old = time.time() - age_days * 86400
        os.utime(path, (old, old))
    return path


class TestJsonLinesFormatter:
    def test_emits_one_parseable_object_per_record(self):
        record = logging.LogRecord(
            "app.svc", logging.INFO, __file__, 1, "loaded %d strikes", (412,), None
        )
        payload = json.loads(log_sink.JsonLinesFormatter().format(record))
        assert payload["level"] == "INFO"
        assert payload["logger"] == "app.svc"
        assert payload["message"] == "loaded 412 strikes"

    def test_unserialisable_arg_degrades_to_repr(self):
        class Opaque:
            def __repr__(self):
                return "<opaque>"

        record = logging.LogRecord(
            "app.svc", logging.INFO, __file__, 1, "got %s", (Opaque(),), None
        )
        assert "<opaque>" in log_sink.JsonLinesFormatter().format(record)

    def test_exception_is_included(self):
        try:
            raise ValueError("kaboom")
        except ValueError:
            import sys

            record = logging.LogRecord(
                "app.svc", logging.ERROR, __file__, 1, "failed", (), sys.exc_info()
            )
        payload = json.loads(log_sink.JsonLinesFormatter().format(record))
        assert "kaboom" in payload["exception"]


class TestSinkHandler:
    def test_writes_json_lines_to_the_process_file(self, sink_dir):
        handler = log_sink.build_handler("backend")
        handler.handle(
            logging.LogRecord("app", logging.INFO, __file__, 1, "hello", (), None)
        )
        handler.close()
        written = (sink_dir / "backend.jsonl").read_text(encoding="utf-8")
        assert json.loads(written)["message"] == "hello"

    def test_each_process_gets_its_own_file(self, sink_dir):
        # A shared RotatingFileHandler across processes corrupts rotation.
        for name in ("backend", "chain-builder"):
            log_sink.build_handler(name).close()
        assert (sink_dir / "backend.jsonl").exists()
        assert (sink_dir / "chain-builder.jsonl").exists()

    def test_returns_none_when_directory_cannot_be_created(self, monkeypatch, tmp_path):
        blocker = tmp_path / "not-a-dir"
        blocker.write_text("", encoding="utf-8")
        monkeypatch.setattr(log_sink, "logs_dir", lambda: str(blocker / "logs"))
        # A read-only or full data volume must not stop the app from starting.
        assert log_sink.build_handler("backend") is None

    def test_rotation_is_capped(self, sink_dir, monkeypatch):
        monkeypatch.setenv("LOG_SINK_MAX_BYTES", "200")
        monkeypatch.setenv("LOG_SINK_BACKUP_COUNT", "2")
        handler = log_sink.build_handler("backend")
        for i in range(200):
            handler.handle(
                logging.LogRecord("app", logging.INFO, __file__, 1, "x" * 50, (), None)
            )
        handler.close()
        files = [f for f in os.listdir(sink_dir) if f.startswith("backend.jsonl")]
        assert len(files) <= 3  # active + 2 backups


class TestPruning:
    def test_removes_files_past_retention(self, sink_dir):
        _write(sink_dir, "backend.jsonl.9", age_days=30)
        _write(sink_dir, "backend.jsonl")
        assert log_sink.prune_expired(str(sink_dir), days=7) == 1
        assert not (sink_dir / "backend.jsonl.9").exists()
        assert (sink_dir / "backend.jsonl").exists()

    def test_missing_directory_is_not_an_error(self, tmp_path):
        assert log_sink.prune_expired(str(tmp_path / "nope"), days=7) == 0


class TestBundle:
    def test_lists_only_log_files(self, sink_dir):
        _write(sink_dir, "backend.jsonl")
        _write(sink_dir, "users.sqlite3")
        names = [f.name for f in log_bundle.list_log_files(7, str(sink_dir))]
        assert names == ["backend.jsonl"]

    def test_includes_rotated_backups(self, sink_dir):
        _write(sink_dir, "backend.jsonl")
        _write(sink_dir, "backend.jsonl.1")
        names = {f.name for f in log_bundle.list_log_files(7, str(sink_dir))}
        assert names == {"backend.jsonl", "backend.jsonl.1"}

    def test_excludes_files_outside_the_window(self, sink_dir):
        _write(sink_dir, "backend.jsonl")
        _write(sink_dir, "backend.jsonl.1", age_days=10)
        names = [f.name for f in log_bundle.list_log_files(7, str(sink_dir))]
        assert names == ["backend.jsonl"]

    def test_zip_contains_the_in_window_files(self, sink_dir):
        _write(sink_dir, "backend.jsonl", content='{"message":"hi"}\n')
        _write(sink_dir, "chain-builder.jsonl", content='{"message":"worker"}\n')
        _write(sink_dir, "backend.jsonl.5", age_days=99)
        archive = zipfile.ZipFile(io.BytesIO(log_bundle.build_zip(7, str(sink_dir))))
        assert set(archive.namelist()) == {"backend.jsonl", "chain-builder.jsonl"}
        assert b"worker" in archive.read("chain-builder.jsonl")

    def test_zip_is_valid_when_there_is_nothing_to_send(self, sink_dir):
        archive = zipfile.ZipFile(io.BytesIO(log_bundle.build_zip(7, str(sink_dir))))
        assert archive.namelist() == []

    @pytest.mark.parametrize(
        "requested,expected", [(0, 1), (-5, 1), (999, log_bundle.MAX_DAYS), (7, 7)]
    )
    def test_day_window_is_clamped(self, requested, expected):
        assert log_bundle.clamp_days(requested) == expected

    def test_non_numeric_days_falls_back_to_retention(self, monkeypatch):
        monkeypatch.setenv("LOG_SINK_RETENTION_DAYS", "7")
        assert log_bundle.clamp_days("garbage") == 7

    def test_filename_reflects_the_clamped_window(self):
        assert log_bundle.bundle_filename(999).endswith(f"-{log_bundle.MAX_DAYS}d.zip")


class TestConfigureLoggingIntegration:
    def test_sink_keeps_info_while_console_shows_warning(self, sink_dir):
        # The whole point of flipping production to WARNING: the console goes quiet but
        # the downloadable log must not.
        configure_logging(level="WARNING", stream=io.StringIO())
        logging.getLogger("app.svc").info("still recorded")
        for handler in logging.getLogger().handlers:
            handler.flush()
        written = (sink_dir / "backend.jsonl").read_text(encoding="utf-8")
        assert "still recorded" in written

    def test_sink_level_survives_the_buffer_being_disabled(self, sink_dir, monkeypatch):
        # Root's level is the floor across all sinks; turning one off must not starve
        # another.
        monkeypatch.setenv("LOG_ERROR_CONTEXT", "off")
        configure_logging(level="WARNING", stream=io.StringIO())
        assert logging.getLogger().level == logging.INFO
        logging.getLogger("app.svc").info("recorded anyway")
        for handler in logging.getLogger().handlers:
            handler.flush()
        assert "recorded anyway" in (sink_dir / "backend.jsonl").read_text("utf-8")

    def test_sink_can_be_disabled(self, sink_dir, monkeypatch):
        monkeypatch.setenv("LOG_SINK", "off")
        configure_logging(level="WARNING", stream=io.StringIO())
        assert not any(
            isinstance(h, RotatingFileHandler) for h in logging.getLogger().handlers
        )

    def test_sink_handler_is_not_stacked_on_reentry(self, sink_dir):
        configure_logging(level="INFO", stream=io.StringIO())
        configure_logging(level="INFO", stream=io.StringIO())
        attached = [
            h for h in logging.getLogger().handlers
            if isinstance(h, RotatingFileHandler)
        ]
        assert len(attached) == 1

    def test_secrets_are_redacted_before_reaching_disk(self, sink_dir):
        # Redaction has to happen on the way in: the file sits on a host volume, so
        # scrubbing at download time would be far too late.
        configure_logging(level="WARNING", stream=io.StringIO())
        logging.getLogger("app.svc").error(
            "GET https://api.telegram.org/bot8869124261:AAH8nAfoe_ARSIYjm05EC/getUpdates"
        )
        for handler in logging.getLogger().handlers:
            handler.flush()
        written = (sink_dir / "backend.jsonl").read_text(encoding="utf-8")
        assert "AAH8nAfoe" not in written
        assert "/bot<redacted>" in written
