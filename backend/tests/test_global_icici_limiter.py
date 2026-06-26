"""Tests for GlobalIciciApiLimiter transport gate."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from icici_breeze_backend.app.services.icici_api_pacing import (
    GlobalIciciApiLimiter,
    GlobalIciciApiPacer,
    is_breeze_rate_limited,
    is_icici_per_minute_limit_exceeded,
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

    def test_breeze_status_5_per_minute(self):
        err = "Limit exceed: API call per minute:Try after some time"
        self.assertTrue(is_breeze_rate_limited(5, err))
        self.assertTrue(is_breeze_rate_limited(401, err))
        self.assertTrue(is_icici_per_minute_limit_exceeded(err))

    def test_per_minute_not_daily(self):
        err = "Limit exceed: API call per minute:Try after some time"
        from icici_breeze_backend.app.services.icici_api_pacing import (
            is_icici_daily_limit_exceeded,
        )

        self.assertFalse(is_icici_daily_limit_exceeded(err))


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
        self.assertTrue(out.get("rate_limit_backoff_exhausted"))
        self.assertIn("backoff up to 5 seconds", out.get("Error", ""))

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
        self.assertIn("backoff up to 5 seconds", out["Error"])
        self.assertIn("Please wait and try again", out["Error"])
        self.assertFalse(out["daily_limit_exhausted"])
        self.assertTrue(out.get("rate_limit_backoff_exhausted"))

    def test_per_minute_throttle_message(self, *_mocks):
        err = "Limit exceed: API call per minute:Try after some time"
        out = GlobalIciciApiLimiter.build_throttle_error(
            "limit-user", broker_error_text=err
        )
        self.assertIn("per-minute", out["Error"].lower())
        self.assertIn("backoff up to 5 seconds", out["Error"])
        self.assertTrue(out.get("icici_minute_limit_exceeded"))

    def test_backoff_capped_at_five_seconds(self):
        GlobalIciciApiPacer.reset_user("limit-user")
        values = [
            GlobalIciciApiPacer.rate_limit_backoff("limit-user", 1.0, endpoint="test")
            for _ in range(5)
        ]
        self.assertAlmostEqual(values[0], 1.0)
        self.assertAlmostEqual(values[1], 2.0)
        self.assertAlmostEqual(values[2], 4.0)
        self.assertAlmostEqual(values[3], 5.0)
        self.assertAlmostEqual(values[4], 5.0)

    @patch("icici_breeze_backend.app.services.icici_api_pacing.time.sleep")
    @patch(
        "icici_breeze_backend.app.services.user_rate_limit_prefs.get_icici_rate_limit_pause_seconds",
        return_value=1.0,
    )
    @patch(
        "icici_breeze_backend.app.services.api_usage.is_daily_limit_reached",
        return_value=False,
    )
    def test_success_always_waits_for_slot(self, *_mocks):
        with patch.object(GlobalIciciApiPacer, "wait_for_slot") as wait_slot:
            with patch.object(GlobalIciciApiLimiter, "_record_call"):
                out = GlobalIciciApiLimiter.request_breeze_dict(
                    lambda: {"Status": 200, "Success": []},
                    user_id="limit-user",
                    record_url="https://api.icicidirect.com/breezeapi/api/v1/quotes",
                )
        wait_slot.assert_called_once()
        self.assertEqual(out.get("Status"), 200)

    @patch(
        "icici_breeze_backend.app.services.api_usage.is_daily_limit_reached",
        return_value=False,
    )
    @patch(
        "icici_breeze_backend.app.services.user_rate_limit_prefs.get_icici_rate_limit_pause_seconds",
        return_value=1.0,
    )
    @patch("icici_breeze_backend.app.services.icici_api_pacing.time.sleep")
    def test_two_success_calls_enforce_pause_gap(self, mock_sleep, *_mocks):
        with patch.object(GlobalIciciApiLimiter, "_record_call"):
            GlobalIciciApiLimiter.request_breeze_dict(
                lambda: {"Status": 200, "Success": []},
                user_id="limit-user",
                record_url="https://api.icicidirect.com/breezeapi/api/v1/quotes",
            )
            GlobalIciciApiLimiter.request_breeze_dict(
                lambda: {"Status": 200, "Success": []},
                user_id="limit-user",
                record_url="https://api.icicidirect.com/breezeapi/api/v1/quotes",
            )
        proactive_sleeps = [
            c[0][0]
            for c in mock_sleep.call_args_list
            if len(c[0]) >= 1
        ]
        self.assertTrue(
            any(0.9 <= s <= 1.0 for s in proactive_sleeps),
            f"expected ~1.0s proactive gap, got {proactive_sleeps}",
        )

    @patch("icici_breeze_backend.app.services.icici_api_pacing.GlobalIciciApiPacer._sleep_with_status")
    @patch(
        "icici_breeze_backend.app.services.user_rate_limit_prefs.get_icici_rate_limit_pause_seconds",
        return_value=1.0,
    )
    @patch(
        "icici_breeze_backend.app.services.api_usage.is_daily_limit_reached",
        return_value=False,
    )
    def test_429_activates_throttling_and_waits_on_next_call(self, *_mocks):
        calls = {"n": 0}

        def perform():
            calls["n"] += 1
            if calls["n"] <= 4:
                return {"Status": 429, "Error": "too many"}
            return {"Status": 200, "Success": []}

        with patch.object(GlobalIciciApiLimiter, "_record_call"):
            with patch.object(GlobalIciciApiPacer, "wait_for_slot") as wait_slot:
                out1 = GlobalIciciApiLimiter.request_breeze_dict(
                    perform,
                    user_id="limit-user",
                    record_url="https://api.icicidirect.com/breezeapi/api/v1/quotes",
                )
                self.assertTrue(GlobalIciciApiPacer.is_throttling_active("limit-user"))
                self.assertEqual(wait_slot.call_count, 1)

                out2 = GlobalIciciApiLimiter.request_breeze_dict(
                    perform,
                    user_id="limit-user",
                    record_url="https://api.icicidirect.com/breezeapi/api/v1/quotes",
                )
                self.assertEqual(wait_slot.call_count, 2)
                self.assertEqual(out2.get("Status"), 200)
                self.assertFalse(GlobalIciciApiPacer.is_throttling_active("limit-user"))

        self.assertTrue(out1.get("icici_throttled"))

    @patch("icici_breeze_backend.app.services.icici_api_pacing.GlobalIciciApiPacer._sleep_with_status")
    @patch(
        "icici_breeze_backend.app.services.user_rate_limit_prefs.get_icici_rate_limit_pause_seconds",
        return_value=0.5,
    )
    @patch(
        "icici_breeze_backend.app.services.api_usage.is_daily_limit_reached",
        return_value=False,
    )
    def test_status_5_uses_capped_backoff_then_retries(self, mock_sleep_status, *_mocks):
        calls = {"n": 0}
        err = "Limit exceed: API call per minute:Try after some time"

        def perform():
            calls["n"] += 1
            if calls["n"] == 1:
                return {"Status": 5, "Error": err}
            return {"Status": 200, "Success": {"order_id": "1"}}

        with patch.object(GlobalIciciApiLimiter, "_record_call"):
            out = GlobalIciciApiLimiter.request_breeze_dict(
                perform,
                user_id="limit-user",
                endpoint="order",
                record_url="https://api.icicidirect.com/breezeapi/api/v1/order",
            )

        self.assertEqual(calls["n"], 2)
        self.assertEqual(out.get("Status"), 200)
        mock_sleep_status.assert_called_once()
        self.assertAlmostEqual(mock_sleep_status.call_args[0][1], 0.5)


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
        self.assertEqual(v, 0.5)
        args = inst.execute.call_args[0][1]
        self.assertEqual(args[0], 0.5)


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
