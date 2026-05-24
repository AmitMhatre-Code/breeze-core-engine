"""Tests for GET /deployment/license-status handler."""

import asyncio

import pytest

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.api.v1.route_deployment import get_deployment_license_status
from icici_breeze_backend.app.auth.context import RequestContext
from icici_breeze_backend.app.services import deployment_license_status as dls


@pytest.fixture(autouse=True)
def _license_env(monkeypatch):
    monkeypatch.setattr(cfg, "DEPLOYMENT_LICENSE_KEY", "test-license-key")
    monkeypatch.setattr(cfg, "PORTAL_API_BASE_URL", "https://breeze-ui.com")
    dls.reset_for_tests()
    yield
    dls.reset_for_tests()


def test_license_status_returns_cached_fields_without_broker_token():
    dls.update_from_portal_response(
        403, {"detail": "License expired"}, source="heartbeat"
    )
    ctx = RequestContext(
        user_id="uid1",
        username="uid1",
        roles=["trader"],
        is_authenticated=True,
        broker_token=None,
    )
    resp = asyncio.run(get_deployment_license_status(ctx))
    assert resp.deployment_license_status == "expired"
    assert resp.deployment_license_read_only is False
    assert resp.contact_sales is not None
    assert resp.contact_sales.license_key == "test-license-key"


def test_license_status_unlicensed_when_portal_without_key(monkeypatch):
    monkeypatch.setattr(cfg, "DEPLOYMENT_LICENSE_KEY", "")
    ctx = RequestContext(
        user_id="uid1",
        username="uid1",
        roles=["trader"],
        is_authenticated=True,
    )
    resp = asyncio.run(get_deployment_license_status(ctx))
    assert resp.deployment_license_status == "unlicensed"
    assert resp.deployment_license_read_only is True


def test_license_status_empty_when_portal_not_configured(monkeypatch):
    monkeypatch.setattr(cfg, "PORTAL_API_BASE_URL", "")
    monkeypatch.setattr(cfg, "DEPLOYMENT_LICENSE_KEY", "")
    ctx = RequestContext(
        user_id="uid1",
        username="uid1",
        roles=["trader"],
        is_authenticated=True,
    )
    resp = asyncio.run(get_deployment_license_status(ctx))
    assert resp.deployment_license_status is None
    assert resp.deployment_license_read_only is False
