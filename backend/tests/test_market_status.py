"""Market status settings API."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from icici_breeze_backend.app.api.v1.route_settings import router
from icici_breeze_backend.app.auth.context import RequestContext, get_request_context


@pytest.fixture
def market_status_client():
    async def _ctx():
        return RequestContext(
            user_id="user1",
            username="user1",
            roles=["trader"],
            is_authenticated=True,
            broker_token="broker-tok",
        )

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_request_context] = _ctx
    with TestClient(app) as client:
        yield client


def test_market_status_open(market_status_client):
    with (
        patch(
            "icici_breeze_backend.app.api.v1.route_settings.is_market_open",
            return_value=True,
        ),
        patch(
            "icici_breeze_backend.app.api.v1.route_settings.market_closed_reason",
            return_value="market open",
        ),
    ):
        res = market_status_client.get("/api/settings/market-status")
    assert res.status_code == 200
    body = res.json()
    assert body == {"is_open": True, "closed_reason": "market open"}


def test_market_status_closed(market_status_client):
    with (
        patch(
            "icici_breeze_backend.app.api.v1.route_settings.is_market_open",
            return_value=False,
        ),
        patch(
            "icici_breeze_backend.app.api.v1.route_settings.market_closed_reason",
            return_value="weekend",
        ),
    ):
        res = market_status_client.get("/api/settings/market-status")
    assert res.status_code == 200
    body = res.json()
    assert body == {"is_open": False, "closed_reason": "weekend"}
