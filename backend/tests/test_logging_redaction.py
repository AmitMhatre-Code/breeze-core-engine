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


class TestQueryStringSecrets:
    """ICICI hands the broker session key back on the `/icici-return` redirect, so
    `uvicorn.access` wrote a live, day-valid credential into a log that
    `/diagnostics/logs/download` zips up for support."""

    def test_redacts_apisession_from_an_access_line(self):
        out = _redact(
            '%s - "%s %s HTTP/%s" %d',
            ("1.2.3.4:0", "POST", "/icici-return?apisession=56609767", "1.1", 303),
        )
        assert "56609767" not in out
        assert out == '1.2.3.4:0 - "POST /icici-return?apisession=<redacted> HTTP/1.1" 303'

    def test_keeps_the_parameter_name(self):
        # A redacted line still has to say *which* credential it hid.
        assert "apisession=<redacted>" in _redact("GET /icici-return?apisession=abc123")

    def test_redacts_only_the_secret_among_several_params(self):
        out = _redact("GET /x?stock_code=BSESEN&apisession=abc123&expiry_date=06-Aug-2026")
        assert "abc123" not in out
        assert "stock_code=BSESEN" in out
        assert "expiry_date=06-Aug-2026" in out

    def test_leaves_stock_token_readable(self):
        """`stock_token` merely ends in "token" and is not a secret -- it is the WS
        subscribe path's primary debugging handle, so the name match is whole-word."""
        out = _redact("GET /market-data?stock_token=4.1!40879")
        assert out == "GET /market-data?stock_token=4.1!40879"

    def test_redacts_underscore_prefixed_credentials(self):
        assert "xyz789" not in _redact("GET /x?broker_token=xyz789")
        assert "xyz789" not in _redact("GET /x?session_token=xyz789")

    def test_matches_regardless_of_case(self):
        assert "abc123" not in _redact("GET /x?ApiSession=abc123")

    def test_redacts_every_occurrence(self):
        out = _redact("retry /icici-return?apisession=aaa after /icici-return?apisession=bbb")
        assert "aaa" not in out and "bbb" not in out
        assert out.count("apisession=<redacted>") == 2

    def test_redaction_is_idempotent(self):
        """The filter is attached to every root handler, so each record passes through it
        once per handler. A rule that matched its own output produced
        `apisession=<redacted><redacted><redacted>` in the sink."""
        record = _record("GET /icici-return?apisession=56609767")
        for _ in range(3):
            SecretRedactingFilter().filter(record)
        assert record.getMessage() == "GET /icici-return?apisession=<redacted>"

    def test_leaves_a_valueless_parameter_alone(self):
        assert _redact("GET /icici-return?apisession=") == "GET /icici-return?apisession="

    def test_stops_at_the_parameter_boundary(self):
        out = _redact("GET /x?apisession=abc123&next=/portfolio")
        assert "abc123" not in out
        assert "next=/portfolio" in out


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
