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
    dls.update_from_verified_policy({"deployment_license_status": "expired"}, source="heartbeat")
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


def _gated(router, path: str, method: str) -> bool:
    from icici_breeze_backend.app.api.deps_license import require_trading_not_revoked

    for route in router.routes:
        if route.path == path and method in route.methods:
            return any(d.call is require_trading_not_revoked for d in route.dependant.dependencies)
    raise AssertionError(f"no route {method} {path}")


def test_risk_reducing_routes_stay_open_in_read_only_mode():
    """B-09: read-only mode blocks opening and changing trades, never leaving one."""
    from icici_breeze_backend.app.api.v1 import (
        route_book,
        route_gtt_exit_orders,
        route_order,
        route_squareoff_rules,
    )

    book = route_book.router.prefix
    sq = route_squareoff_rules.router.prefix
    gtt = route_gtt_exit_orders.router.prefix
    open_routes = [
        (route_book.router, f"{book}/cancel-one", "POST"),
        (route_book.router, f"{book}/cancel-commit", "POST"),
        (route_book.router, f"{book}/parked-orders/{{order_id}}", "DELETE"),
        (route_book.router, f"{book}/parked-orders/delete-many", "POST"),
        (route_squareoff_rules.router, f"{sq}", "POST"),
        (route_squareoff_rules.router, f"{sq}/{{rule_id}}", "DELETE"),
        (route_squareoff_rules.router, f"{sq}/{{rule_id}}/cancel-orphan-orders", "POST"),
        (route_gtt_exit_orders.router, f"{gtt}/{{gtt_order_id}}", "DELETE"),
    ]
    for router, path, method in open_routes:
        assert not _gated(router, path, method), f"{method} {path} must not be licence-gated"

    still_gated = [
        (route_order.router, f"{route_order.router.prefix}", "POST"),
        (route_book.router, f"{book}/modify-leg-step", "POST"),
        (route_book.router, f"{book}/parked-orders", "POST"),
        (route_gtt_exit_orders.router, f"{gtt}", "POST"),
    ]
    for router, path, method in still_gated:
        assert _gated(router, path, method), f"{method} {path} must stay licence-gated"
