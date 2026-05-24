"""Tests for deployment license status cache."""

import json

import pytest

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.services import deployment_license_status as dls


@pytest.fixture(autouse=True)
def _reset_license_state(monkeypatch):
    monkeypatch.setattr(cfg, "DEPLOYMENT_LICENSE_KEY", "test-license-key")
    monkeypatch.setattr(cfg, "PORTAL_API_BASE_URL", "https://breeze-ui.com")
    dls.reset_for_tests()
    yield
    dls.reset_for_tests()


def test_update_from_portal_response_active_on_200():
    dls.update_from_portal_response(200, {"status": "OK"}, source="heartbeat")
    assert dls.get_license_status() == "active"
    assert dls.trading_mutations_allowed() is True
    api = dls.get_license_status_for_api()
    assert api == {
        "deployment_license_status": "active",
        "deployment_license_read_only": False,
    }


@pytest.mark.parametrize(
    "detail,expected",
    [
        ("License revoked", "revoked"),
        ("License expired", "expired"),
    ],
)
def test_update_from_portal_response_403(detail, expected):
    body = json.dumps({"detail": detail})
    dls.update_from_portal_response(403, body, source="heartbeat")
    assert dls.get_license_status() == expected
    assert dls.trading_mutations_allowed() is (expected != "revoked")


def test_expired_allows_trading_mutations():
    dls.update_from_portal_response(
        403,
        {"detail": "License expired"},
        source="deployment-login",
    )
    assert dls.trading_mutations_allowed() is True
    api = dls.get_license_status_for_api()
    assert api["deployment_license_read_only"] is False


def test_revoked_blocks_trading_mutations():
    dls.update_from_portal_response(
        403,
        {"detail": "License revoked"},
        source="deployment-login",
    )
    assert dls.trading_mutations_allowed() is False
    api = dls.get_license_status_for_api()
    assert api["deployment_license_read_only"] is True


def test_no_api_fields_when_license_env_missing(monkeypatch):
    monkeypatch.setattr(cfg, "DEPLOYMENT_LICENSE_KEY", "")
    dls.update_from_portal_response(403, {"detail": "License revoked"}, source="heartbeat")
    assert dls.get_license_status_for_api() is None
    assert dls.trading_mutations_allowed() is True


def test_require_trading_not_revoked_raises_when_revoked():
    from fastapi import HTTPException

    from icici_breeze_backend.app.api.deps_license import require_trading_not_revoked

    dls.update_from_portal_response(403, {"detail": "License revoked"}, source="heartbeat")
    with pytest.raises(HTTPException) as exc:
        require_trading_not_revoked()
    assert exc.value.status_code == 403
    assert "Read-only mode" in exc.value.detail


def test_require_trading_not_revoked_allows_expired():
    from icici_breeze_backend.app.api.deps_license import require_trading_not_revoked

    dls.update_from_portal_response(403, {"detail": "License expired"}, source="heartbeat")
    require_trading_not_revoked()


def test_home_data_response_includes_license_fields():
    from icici_breeze_backend.app.domain.responses import HomeDataResponse

    dls.update_from_portal_response(403, {"detail": "License revoked"}, source="heartbeat")
    license_fields = dls.get_license_status_for_api()
    assert license_fields is not None

    resp = HomeDataResponse(**license_fields)
    assert resp.deployment_license_status == "revoked"
    assert resp.deployment_license_read_only is True
