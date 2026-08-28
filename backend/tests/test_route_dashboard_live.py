"""Tests for GET /dashboard/live (WS-fed Open P&L + Day's P&L tile values)."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from icici_breeze_backend.app.api.v1.route_dashboard import router
from icici_breeze_backend.app.auth.context import RequestContext, get_request_context


@pytest.fixture
def live_client():
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


def test_live_returns_nulls_when_nothing_warm(live_client, monkeypatch):
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.portfolio_pnl_engine.latest_snapshot",
        lambda uid: None,
    )
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.portfolio_pnl_engine.is_tick_stream_stale",
        lambda: True,
    )
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.dashboard_day_pnl_live.latest",
        lambda uid: None,
    )
    r = live_client.get("/dashboard/live")
    assert r.status_code == 200
    assert r.json() == {"open_pnl": None, "day_pnl": None, "tick_stale": True}


def test_live_passes_through_engine_and_day_pnl(live_client, monkeypatch):
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.portfolio_pnl_engine.latest_snapshot",
        lambda uid: {
            "total_pnl": 1234.5,
            "legs": [{"scrip_key": "a"}, {"scrip_key": "b"}],
            "stream_stale": False,
            "computed_at": 111.0,
        },
    )
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.portfolio_pnl_engine.is_tick_stream_stale",
        lambda: False,
    )
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.dashboard_day_pnl_live.latest",
        lambda uid: {"total_day_pnl": -50.0, "source": "live"},
    )
    r = live_client.get("/dashboard/live")
    assert r.status_code == 200
    body = r.json()
    assert body["open_pnl"] == {
        "total_pnl": 1234.5,
        "leg_count": 2,
        "stream_stale": False,
        "computed_at": 111.0,
    }
    assert body["day_pnl"]["total_day_pnl"] == -50.0
    assert body["tick_stale"] is False


def test_live_does_not_require_broker_token(live_client, monkeypatch):
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.portfolio_pnl_engine.latest_snapshot",
        lambda uid: None,
    )
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.dashboard_day_pnl_live.latest",
        lambda uid: None,
    )
    r = live_client.get("/dashboard/live")
    assert r.status_code == 200
