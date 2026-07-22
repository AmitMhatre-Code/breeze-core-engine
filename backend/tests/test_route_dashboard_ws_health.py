"""Tests for GET /dashboard/ws-health (navbar market-data health dot)."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from icici_breeze_backend.app.api.v1.route_dashboard import router
from icici_breeze_backend.app.auth.context import RequestContext, get_request_context


@pytest.fixture
def ws_health_client():
    async def _ctx():
        return RequestContext(
            user_id="user1",
            username="user1",
            roles=["trader"],
            is_authenticated=True,
            broker_token=None,
        )

    app = FastAPI()
    app.include_router(router, prefix="/dashboard")
    app.dependency_overrides[get_request_context] = _ctx
    with TestClient(app) as client:
        yield client


def test_ws_health_passes_through_status(ws_health_client, monkeypatch):
    fake_status = {
        "status": "green",
        "reason": "NIFTY & SENSEX chains live",
        "poll_interval_seconds": 2.0,
        "market_open": True,
        "prefetch_done": True,
        "detail": {},
    }
    monkeypatch.setattr(
        "icici_breeze_backend.app.api.v1.route_dashboard.get_system_health_status",
        lambda: fake_status,
    )
    r = ws_health_client.get("/dashboard/ws-health")
    assert r.status_code == 200
    assert r.json() == fake_status


def test_ws_health_does_not_require_broker_token(ws_health_client, monkeypatch):
    """Unlike /vix*, this route must resolve even without ctx.broker_token --
    the dot needs to show gray/red for a user whose own session hasn't
    touched the broker yet, not 401."""
    monkeypatch.setattr(
        "icici_breeze_backend.app.api.v1.route_dashboard.get_system_health_status",
        lambda: {"status": "gray", "reason": "Market closed (weekend)"},
    )
    r = ws_health_client.get("/dashboard/ws-health")
    assert r.status_code == 200
