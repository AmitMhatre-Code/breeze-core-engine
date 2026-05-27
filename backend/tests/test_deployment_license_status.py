"""Tests for deployment license status cache."""

import json
from datetime import datetime, timedelta, timezone

import pytest

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.services import deployment_license_status as dls


@pytest.fixture(autouse=True)
def _reset_license_state(monkeypatch):
    monkeypatch.setattr(cfg, "DEPLOYMENT_LICENSE_KEY", "test-license-key")
    monkeypatch.setattr(cfg, "PORTAL_API_BASE_URL", "https://portal.example")
    dls.reset_for_tests()
    yield
    dls.reset_for_tests()


def test_update_from_verified_policy_active():
    dls.update_from_verified_policy(
        {"deployment_license_status": "active", "heartbeat_interval_sec": 600},
        source="heartbeat",
    )
    assert dls.get_license_status() == "active"


@pytest.mark.parametrize(
    "policy,expected,trading_allowed,read_only",
    [
        ({"deployment_license_status": "expired"}, "expired", True, False),
        ({"deployment_license_status": "revoked"}, "revoked", False, True),
        ({"deployment_license_status": "unlicensed"}, "unlicensed", False, True),
    ],
)
def test_update_from_verified_policy_license_status(policy, expected, trading_allowed, read_only):
    dls.update_from_verified_policy(policy, source="heartbeat")
    assert dls.get_license_status() == expected
    assert dls.trading_mutations_allowed() is trading_allowed
    api = dls.get_license_status_for_api()
    assert api is not None
    assert api["deployment_license_status"] == expected
    assert api["deployment_license_read_only"] is read_only
    if expected in ("expired", "revoked", "unlicensed"):
        assert "contact_sales" in api


def test_expired_allows_trading_mutations(monkeypatch):
    monkeypatch.setattr(cfg, "PUBLIC_FRONTEND_ORIGIN", "http://203.0.113.10")
    dls.update_from_verified_policy({"deployment_license_status": "expired"}, source="deployment-login")
    assert dls.trading_mutations_allowed() is True
    api = dls.get_license_status_for_api()
    assert api["deployment_license_read_only"] is False
    assert api["contact_sales"]["license_key"] == "test-license-key"
    assert api["contact_sales"]["public_ip"] == "203.0.113.10"


def test_revoked_blocks_trading_mutations():
    dls.update_from_verified_policy({"deployment_license_status": "revoked"}, source="deployment-login")
    assert dls.trading_mutations_allowed() is False
    api = dls.get_license_status_for_api()
    assert api["deployment_license_read_only"] is True


def test_no_api_fields_when_portal_not_configured(monkeypatch):
    monkeypatch.setattr(cfg, "PORTAL_API_BASE_URL", "")
    monkeypatch.setattr(cfg, "DEPLOYMENT_LICENSE_KEY", "")
    assert dls.get_license_status_for_api() is None
    assert dls.trading_mutations_allowed() is True


def test_portal_without_license_key_is_unlicensed_read_only(monkeypatch):
    monkeypatch.setattr(cfg, "DEPLOYMENT_LICENSE_KEY", "")
    monkeypatch.setattr(cfg, "PUBLIC_FRONTEND_ORIGIN", "http://203.0.113.10")
    assert dls.get_license_status() == "unlicensed"
    assert dls.trading_mutations_allowed() is False
    api = dls.get_license_status_for_api()
    assert api is not None
    assert api["deployment_license_status"] == "unlicensed"
    assert api["deployment_license_read_only"] is True
    assert api["contact_sales"]["public_ip"] == "203.0.113.10"


def test_unlicensed_from_heartbeat_blocks_trading():
    dls.update_from_verified_policy({"deployment_license_status": "unlicensed"}, source="heartbeat")
    assert dls.trading_mutations_allowed() is False


def test_fail_closed_when_no_verified_policy_yet():
    assert dls.get_license_status() is None
    assert dls.trading_mutations_allowed() is False
    api = dls.get_license_status_for_api()
    assert api["deployment_license_status"] == "unlicensed"
    assert api["deployment_license_read_only"] is True


def test_stale_policy_blocks_trading(monkeypatch):
    dls.update_from_verified_policy(
        {"deployment_license_status": "active", "heartbeat_interval_sec": 300},
        source="heartbeat",
    )
    stale = datetime.now(timezone.utc) - timedelta(seconds=700)
    with dls._lock:
        dls._verified_at = stale  # noqa: SLF001
    assert dls.trading_mutations_allowed() is False
    assert dls.get_license_status() == "unlicensed"


def test_require_trading_not_revoked_raises_when_revoked():
    from fastapi import HTTPException

    from icici_breeze_backend.app.api.deps_license import require_trading_not_revoked

    dls.update_from_verified_policy({"deployment_license_status": "revoked"}, source="heartbeat")
    with pytest.raises(HTTPException) as exc:
        require_trading_not_revoked()
    assert exc.value.status_code == 403
    assert "Read-only mode" in exc.value.detail


def test_require_trading_not_revoked_allows_expired():
    from icici_breeze_backend.app.api.deps_license import require_trading_not_revoked

    dls.update_from_verified_policy({"deployment_license_status": "expired"}, source="heartbeat")
    require_trading_not_revoked()


def test_home_data_response_includes_license_fields():
    from icici_breeze_backend.app.domain.responses import HomeDataResponse

    dls.update_from_verified_policy({"deployment_license_status": "revoked"}, source="heartbeat")
    license_fields = dls.get_license_status_for_api()
    assert license_fields is not None

    resp = HomeDataResponse(**license_fields)
    assert resp.deployment_license_status == "revoked"
    assert resp.deployment_license_read_only is True
