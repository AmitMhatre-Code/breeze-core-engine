"""Tests for GlobalIciciApiLimiter transport gate."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from icici_breeze_backend.app.services.icici_api_pacing import (
    GlobalIciciApiLimiter,
    GlobalIciciApiPacer,
    is_breeze_rate_limited,
)


class TestIsBreezeRateLimited(unittest.TestCase):
    def test_http_429(self):
        self.assertTrue(is_breeze_rate_limited(429, ""))

    def test_http_503(self):
        self.assertTrue(is_breeze_rate_limited(503, ""))

    def test_html_429_body(self):
        self.assertTrue(
            is_breeze_rate_limited(
                502,
                "<html><title>429 Too Many Requests</title></html>",
            )
        )


class TestGlobalIciciApiLimiter(unittest.TestCase):
    def setUp(self) -> None:
        GlobalIciciApiPacer.reset_user("limit-user")

    @patch("icici_breeze_backend.app.services.icici_api_pacing.time.sleep")
    @patch(
        "icici_breeze_backend.app.services.user_rate_limit_prefs.get_icici_rate_limit_pause_seconds",
        return_value=1.0,
    )
    @patch(
        "icici_breeze_backend.app.services.api_usage.is_daily_limit_reached",
        return_value=False,
    )
    def test_retries_then_returns_throttle_error(self, *_mocks):
        calls = {"n": 0}

        def perform():
            calls["n"] += 1
            return {"Status": 429, "Error": "too many"}

        with patch.object(GlobalIciciApiLimiter, "_record_call"):
            out = GlobalIciciApiLimiter.request_breeze_dict(
                perform,
                user_id="limit-user",
                endpoint="quotes",
                record_url="https://api.icicidirect.com/breezeapi/api/v1/quotes",
            )

        self.assertEqual(calls["n"], 4)
        self.assertTrue(out.get("icici_throttled"))
        self.assertIn("throttled by ICICI", out.get("Error", ""))

    @patch(
        "icici_breeze_backend.app.services.api_usage.is_daily_limit_reached",
        return_value=True,
    )
    @patch(
        "icici_breeze_backend.app.services.api_usage.get_today_count",
        return_value=5000,
    )
    def test_blocks_at_daily_cap_without_http(self, *_mocks):
        perform = MagicMock(return_value={"Status": 200})

        with patch.object(GlobalIciciApiLimiter, "_record_call") as record:
            out = GlobalIciciApiLimiter.request_breeze_dict(
                perform,
                user_id="limit-user",
                record_url="https://api.icicidirect.com/breezeapi/api/v1/quotes",
            )

        perform.assert_not_called()
        record.assert_not_called()
        self.assertTrue(out.get("daily_limit_exhausted"))
        self.assertIn("midnight IST", out.get("Error", ""))

    @patch(
        "icici_breeze_backend.app.services.api_usage.is_daily_limit_reached",
        return_value=False,
    )
    @patch(
        "icici_breeze_backend.app.services.api_usage.get_today_count",
        return_value=100,
    )
    def test_transient_throttle_message(self, *_mocks):
        out = GlobalIciciApiLimiter.build_throttle_error("limit-user")
        self.assertIn("try again in a minute", out["Error"])
        self.assertFalse(out["daily_limit_exhausted"])

    def test_backoff_capped_at_three_seconds(self):
        b1 = GlobalIciciApiPacer.rate_limit_backoff("limit-user", 1.0, endpoint="test")
        b2 = GlobalIciciApiPacer.rate_limit_backoff("limit-user", 1.0, endpoint="test")
        b3 = GlobalIciciApiPacer.rate_limit_backoff("limit-user", 1.0, endpoint="test")
        self.assertAlmostEqual(b1, 1.0)
        self.assertAlmostEqual(b2, 2.0)
        self.assertAlmostEqual(b3, 3.0)

    def test_backoff_from_zero_base(self):
        GlobalIciciApiPacer.reset_user("zero-user")
        b1 = GlobalIciciApiPacer.rate_limit_backoff("zero-user", 0.0, endpoint="test")
        b2 = GlobalIciciApiPacer.rate_limit_backoff("zero-user", 0.0, endpoint="test")
        b3 = GlobalIciciApiPacer.rate_limit_backoff("zero-user", 0.0, endpoint="test")
        self.assertEqual(b1, 0.0)
        self.assertEqual(b2, 0.0)
        self.assertEqual(b3, 0.0)


class TestUserRateLimitPrefsBounds(unittest.TestCase):
    def test_clamp_on_read(self):
        from icici_breeze_backend.app.services import user_rate_limit_prefs as prefs

        with patch.object(
            prefs,
            "ensure_icici_rate_limit_pause_column",
        ), patch("sqlite3.connect") as mock_conn:
            inst = mock_conn.return_value.__enter__.return_value
            inst.execute.return_value.fetchone.return_value = (10.0,)
            v = prefs.get_icici_rate_limit_pause_seconds("u1")
        self.assertEqual(v, 3.0)

    def test_clamp_on_write(self):
        from icici_breeze_backend.app.services import user_rate_limit_prefs as prefs

        with patch.object(
            prefs,
            "ensure_icici_rate_limit_pause_column",
        ), patch("sqlite3.connect") as mock_conn:
            inst = mock_conn.return_value.__enter__.return_value
            v = prefs.set_icici_rate_limit_pause_seconds("u1", 0.1)
        self.assertEqual(v, 0.1)
        args = inst.execute.call_args[0][1]
        self.assertEqual(args[0], 0.1)

    def test_clamp_on_write_zero(self):
        from icici_breeze_backend.app.services import user_rate_limit_prefs as prefs

        with patch.object(
            prefs,
            "ensure_icici_rate_limit_pause_column",
        ), patch("sqlite3.connect") as mock_conn:
            inst = mock_conn.return_value.__enter__.return_value
            v = prefs.set_icici_rate_limit_pause_seconds("u1", 0)
        self.assertEqual(v, 0.0)
        args = inst.execute.call_args[0][1]
        self.assertEqual(args[0], 0.0)

    def test_clamp_on_write_negative(self):
        from icici_breeze_backend.app.services import user_rate_limit_prefs as prefs

        with patch.object(
            prefs,
            "ensure_icici_rate_limit_pause_column",
        ), patch("sqlite3.connect") as mock_conn:
            inst = mock_conn.return_value.__enter__.return_value
            v = prefs.set_icici_rate_limit_pause_seconds("u1", -1)
        self.assertEqual(v, 0.0)
        args = inst.execute.call_args[0][1]
        self.assertEqual(args[0], 0.0)


class TestApiUsageWarning(unittest.TestCase):
    def test_warning_in_final_band(self):
        from icici_breeze_backend.app.services.api_usage import get_usage_warning

        with patch(
            "icici_breeze_backend.app.services.api_usage.get_today_count",
            return_value=4000,
        ):
            msg = get_usage_warning("u1")
        self.assertIsNotNone(msg)
        self.assertIn("1000", msg or "")

    def test_no_warning_below_threshold(self):
        from icici_breeze_backend.app.services.api_usage import get_usage_warning

        with patch(
            "icici_breeze_backend.app.services.api_usage.get_today_count",
            return_value=3999,
        ):
            self.assertIsNone(get_usage_warning("u1"))
