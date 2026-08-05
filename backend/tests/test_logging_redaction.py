"""Tests for `app.core.logging`'s secret redaction.

The Telegram bot token rides in the URL path, so `httpx`'s per-request INFO
line and any exception carrying that URL would otherwise write a live,
fleet-wide credential into logs sitting on customer-owned hosts.
"""
from __future__ import annotations

import logging

from icici_breeze_backend.app.core.logging import SecretRedactingFilter, configure_logging

_TOKEN_URL = "https://api.telegram.org/bot8869124261:AAH8nAfoe_ARSIYjm05ECvUOcwnY6JPAV-Q/getUpdates"


def _record(msg, args=()):
    return logging.LogRecord("test", logging.INFO, __file__, 1, msg, args, None)


def _redact(msg, args=()):
    record = _record(msg, args)
    assert SecretRedactingFilter().filter(record) is True
    return record.getMessage()


class TestSecretRedactingFilter:
    def test_redacts_bot_token_from_url(self):
        assert "AAH8nAfoe" not in _redact("HTTP Request: GET %s", (_TOKEN_URL,))

    def test_keeps_the_rest_of_the_url_readable(self):
        out = _redact("HTTP Request: GET %s", (_TOKEN_URL,))
        assert out == "HTTP Request: GET https://api.telegram.org/bot<redacted>/getUpdates"

    def test_redacts_every_occurrence_in_one_message(self):
        # httpx's own HTTPError string repeats the URL, hence sub() not a single replace.
        out = _redact(f"failed for url '{_TOKEN_URL}' — see {_TOKEN_URL}")
        assert "AAH8nAfoe" not in out
        assert out.count("/bot<redacted>") == 2

    def test_leaves_unrelated_messages_untouched(self):
        record = _record("Incoming request GET /health user_id=%s", ("unknown",))
        SecretRedactingFilter().filter(record)
        assert record.getMessage() == "Incoming request GET /health user_id=unknown"
        assert record.args == ("unknown",)  # untouched records keep lazy formatting

    def test_never_drops_a_record_whose_formatting_raises(self):
        record = _record("bad format %d", ("not-an-int",))
        assert SecretRedactingFilter().filter(record) is True


class TestConfigureLogging:
    def test_attaches_the_filter_to_root_handlers(self):
        configure_logging(level="INFO")
        root = logging.getLogger()
        assert root.handlers
        for handler in root.handlers:
            assert any(isinstance(f, SecretRedactingFilter) for f in handler.filters)

    def test_reconfiguring_does_not_stack_duplicate_filters(self):
        configure_logging(level="INFO")
        configure_logging(level="INFO")
        for handler in logging.getLogger().handlers:
            matching = [f for f in handler.filters if isinstance(f, SecretRedactingFilter)]
            assert len(matching) == 1
