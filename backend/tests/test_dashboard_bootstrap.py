"""Tests for dashboard bootstrap and broker snapshot cache."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from icici_breeze_backend.app.external.icici_api import session_token_from_customer_response
from icici_breeze_backend.app.services.broker_snapshot_cache import (
    get_snapshot,
    set_snapshot,
)
from icici_breeze_backend.app.services.dashboard_bootstrap import build_dashboard_bootstrap
from icici_breeze_backend.app.services.processor import build_margin_situation_from_raw


@pytest.fixture(autouse=True)
def _clear_snapshot_cache():
    from icici_breeze_backend.app.services import broker_snapshot_cache as mod

    with mod._lock:
        mod._cache.clear()
    yield
    with mod._lock:
        mod._cache.clear()


def test_session_token_from_customer_response_fallbacks():
    assert session_token_from_customer_response(
        {"Success": {"session_token": "tok-a"}},
        raw_session="",
        broker_token="apisession",
    ) == "tok-a"
    assert session_token_from_customer_response(
        None,
        raw_session="",
        broker_token="apisession",
    ) == "apisession"


def test_snapshot_cache_hit_skips_live_home_fields():
    user = "U1"
    token = "tok-abc"
    customer = {"Status": 200, "Success": {"idirect_user_name": "Test User"}}
    margin = build_margin_situation_from_raw(
        {
            "Status": 200,
            "Success": {"cash_limit": 100.0, "limit_list": []},
        }
    )
    set_snapshot(
        user,
        token,
        customer_details=customer,
        margin_situation=margin,
        customerdetails_session_token="sess-tok",
    )
    snap = get_snapshot(user, token)
    assert snap is not None
    assert snap.customer_details == customer
    assert snap.customerdetails_session_token == "sess-tok"


def test_build_dashboard_bootstrap_uses_snapshot_and_skips_vix_options():
    user = "U1"
    token = "broker-tok"
    customer = {"Status": 200, "Success": {"idirect_user_name": "Cached"}}
    margin = build_margin_situation_from_raw(
        {"Status": 200, "Success": {"cash_limit": 500.0, "limit_list": []}}
    )
    set_snapshot(
        user,
        token,
        customer_details=customer,
        margin_situation=margin,
        customerdetails_session_token="raw-sess",
    )

    proc = MagicMock()
    proc.get_customer_details = MagicMock(side_effect=AssertionError("should use cache"))
    proc.get_margin_situation = MagicMock(side_effect=AssertionError("should use cache"))
    mock_breeze = MagicMock()
    proc.get_session_breeze.return_value = mock_breeze
    proc.get_positions.return_value = {"Status": 200, "Success": []}

    vix_payload = {
        "current_vix": 14.5,
        "nifty_spot": 24000.0,
        "vix_trend_pct": 0.0,
        "nifty_spot_trend_pct": 2.0,
        "vix_30d": [],
        "error": None,
    }

    with patch(
        "icici_breeze_backend.app.services.dashboard_bootstrap.fetch_vix_headline",
        return_value=(vix_payload, {"ltp": 24000}),
    ) as headline_mock, patch(
        "icici_breeze_backend.app.services.api_usage.get_usage_for_display",
        return_value={
            "api_calls_today": 1,
            "api_calls_limit": 5000,
            "api_usage_band": "green",
        },
    ), patch(
        "icici_breeze_backend.app.services.api_usage.get_usage_warning",
        return_value=None,
    ), patch(
        "icici_breeze_backend.app.services.api_usage.is_daily_limit_reached",
        return_value=False,
    ), patch(
        "icici_breeze_backend.app.services.deployment_license_status.get_license_status_for_api",
        return_value={},
    ), patch(
        "icici_breeze_backend.audit.logger.AuditLogger.log_portfolio_access",
    ):
        payload = build_dashboard_bootstrap(user, proc, broker_token=token)

    proc.get_customer_details.assert_not_called()
    proc.get_margin_situation.assert_not_called()
    proc.get_session_breeze.assert_called_once()
    proc.get_positions.assert_called_once_with(user, session_token="raw-sess")
    headline_mock.assert_called_once()
    assert "vix_options" not in payload
    assert payload["home"]["customer"] == customer
    assert payload["vix"]["vix_30d"] == []


def test_fetch_vix_history_returns_series():
    from icici_breeze_backend.app.services.dashboard_vix import fetch_vix_history

    proc = MagicMock()
    proc.get_session_breeze.return_value = MagicMock()
    with patch(
        "icici_breeze_backend.app.services.dashboard_vix._historical_vix_range",
        return_value=[{"date": "2026-06-01", "value": 14.0}],
    ):
        rows = fetch_vix_history("U1", proc)
    assert rows == [{"date": "2026-06-01", "value": 14.0}]
