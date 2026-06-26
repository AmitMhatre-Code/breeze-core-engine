"""Tests for DEBUG-gated ICICI Breeze HTTP forensic audit logging."""
from __future__ import annotations

import logging
import unittest
from unittest.mock import MagicMock, patch

from icici_breeze_backend.app.core.requests_patch import _SyntheticRawResponse, _log_breeze_parse_failure
from icici_breeze_backend.app.services.breeze_http_audit import (
    is_breeze_http_audit_enabled,
    log_breeze_http_attempt,
    resolve_breeze_http_origin,
)
from icici_breeze_backend.app.services.icici_api_pacing import GlobalIciciApiLimiter


class _MockUpstreamResponse:
    def __init__(self, status_code: int, text: str, headers: dict | None = None) -> None:
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}

    def json(self):
        import json

        return json.loads(self.text)


class TestBreezeHttpAudit(unittest.TestCase):
    def setUp(self) -> None:
        self.audit_logger = logging.getLogger("icici_breeze_backend.app.services.breeze_http_audit")
        self.prev_level = self.audit_logger.level
        self.prev_propagate = self.audit_logger.propagate
        self.handler = logging.Handler()
        self.handler.setLevel(logging.DEBUG)
        self.records: list[logging.LogRecord] = []
        self.handler.emit = lambda record: self.records.append(record)
        self.audit_logger.handlers.clear()
        self.audit_logger.addHandler(self.handler)
        self.audit_logger.setLevel(logging.DEBUG)
        self.audit_logger.propagate = False

    def tearDown(self) -> None:
        self.audit_logger.removeHandler(self.handler)
        self.audit_logger.setLevel(self.prev_level)
        self.audit_logger.propagate = self.prev_propagate

    def test_audit_emits_at_debug_for_upstream(self) -> None:
        raw = _MockUpstreamResponse(
            401,
            '{"Status":5,"Error":"Limit exceed: API call per minute:Try after some time"}',
            headers={
                "Server": "nginx",
                "X-SessionToken": "secret",
                "X-Checksum": "token abc",
                "Date": "Fri, 26 Jun 2026 08:12:24 GMT",
            },
        )
        log_breeze_http_attempt(
            breeze_call_id="call-1",
            method="POST",
            url="https://api.icicidirect.com/breezeapi/api/v1/order",
            endpoint="order",
            attempt=1,
            origin="upstream",
            elapsed_ms=12.3,
            http_status=401,
            body={"Status": 5, "Error": "Limit exceed: API call per minute:Try after some time"},
            err_text="Limit exceed: API call per minute:Try after some time",
            raw=raw,
            user_id="U1",
            route_id="POST /order/break-chunk",
            correlation_id="cid-1",
        )
        self.assertEqual(len(self.records), 1)
        msg = self.records[0].getMessage()
        self.assertIn("breeze_http_audit", msg)
        self.assertIn("origin=upstream", msg)
        self.assertIn("http_status=401", msg)
        self.assertIn("breeze_status=5", msg)
        self.assertIn("icici_minute_limit=True", msg)
        self.assertIn("correlation_id=cid-1", msg)
        self.assertIn("'server': 'nginx'", msg)
        self.assertNotIn("X-SessionToken", msg)
        self.assertNotIn("X-Checksum", msg)

    def test_synthetic_origin_marker(self) -> None:
        raw = _SyntheticRawResponse(429, '{"Status":429,"Error":"throttled"}')
        self.assertEqual(resolve_breeze_http_origin(raw), "synthetic")

    @patch(
        "icici_breeze_backend.app.services.api_usage.is_daily_limit_reached",
        return_value=True,
    )
    def test_daily_blocked_origin(self, *_mocks) -> None:
        perform = MagicMock()
        with patch.object(GlobalIciciApiLimiter, "_record_call"):
            out = GlobalIciciApiLimiter.request_breeze_dict(
                perform,
                user_id="limit-user",
                record_url="https://api.icicidirect.com/breezeapi/api/v1/quotes",
            )
        perform.assert_not_called()
        self.assertTrue(out.get("daily_limit_exhausted"))
        self.assertTrue(any("origin=daily_blocked" in r.getMessage() for r in self.records))

    def test_no_audit_at_info_level(self) -> None:
        self.audit_logger.setLevel(logging.INFO)
        self.assertFalse(is_breeze_http_audit_enabled())
        log_breeze_http_attempt(
            breeze_call_id="call-2",
            method="GET",
            url="https://api.icicidirect.com/breezeapi/api/v1/quotes",
            endpoint="quotes",
            attempt=1,
            origin="upstream",
            elapsed_ms=1.0,
            http_status=200,
            body={"Status": 200},
            err_text=None,
            raw=None,
        )
        self.assertEqual(len(self.records), 0)

    @patch("icici_breeze_backend.app.services.icici_api_pacing.time.sleep")
    @patch(
        "icici_breeze_backend.app.services.api_usage.is_daily_limit_reached",
        return_value=False,
    )
    def test_synthetic_after_exhausted_retries(self, *_mocks) -> None:
        perform = MagicMock(
            return_value={"Status": 429, "Error": "too many"},
        )
        with patch.object(GlobalIciciApiLimiter, "_record_call"):
            out = GlobalIciciApiLimiter.request_breeze_dict(
                perform,
                user_id="limit-user",
                record_url="https://api.icicidirect.com/breezeapi/api/v1/quotes",
            )
        self.assertTrue(out.get("icici_throttled"))
        self.assertTrue(any("origin=synthetic" in r.getMessage() for r in self.records))
        self.assertEqual(perform.call_count, 4)

    def test_parse_failure_downgrades_to_debug_when_audit_enabled(self) -> None:
        patch_logger = logging.getLogger("icici_breeze_backend.app.core.requests_patch")
        prev = patch_logger.level
        captured: list[logging.LogRecord] = []
        handler = logging.Handler()
        handler.setLevel(logging.DEBUG)
        handler.emit = lambda record: captured.append(record)
        patch_logger.addHandler(handler)
        patch_logger.setLevel(logging.DEBUG)
        try:
            _log_breeze_parse_failure(
                reason="non_200_http",
                method="POST",
                url="https://api.icicidirect.com/breezeapi/api/v1/order",
                http_status=401,
                content_type="application/json",
                body_len=76,
                body_preview='{"Status":5,"Error":"Limit exceed"}',
            )
        finally:
            patch_logger.removeHandler(handler)
            patch_logger.setLevel(prev)
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0].levelno, logging.DEBUG)


if __name__ == "__main__":
    unittest.main()
