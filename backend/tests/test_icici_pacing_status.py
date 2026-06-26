"""Tests for GET /api/icici/pacing-status."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from icici_breeze_backend.app.api.v1 import route_icici
from icici_breeze_backend.app.auth.context import RequestContext, get_request_context
from icici_breeze_backend.app.services.icici_api_pacing import BackoffSnapshot, GlobalIciciApiPacer


@pytest.fixture
def pacing_client():
    async def _ctx():
        return RequestContext(
            user_id="iciciuser1",
            username="iciciuser1",
            roles=["trader"],
            is_authenticated=True,
            broker_token="broker-tok",
        )

    app = FastAPI()
    app.include_router(route_icici.router, prefix="")
    app.dependency_overrides[get_request_context] = _ctx
    with TestClient(app) as client:
        yield client


def test_pacing_status_idle(pacing_client, monkeypatch):
    monkeypatch.setattr(
        GlobalIciciApiPacer,
        "get_backoff_snapshot",
        classmethod(lambda cls, user_id: None),
    )
    r = pacing_client.get("/api/icici/pacing-status")
    assert r.status_code == 200
    body = r.json()
    assert body["throttling_active"] is False
    assert body["backing_off"] is False
    assert body["seconds_remaining"] == 0


def test_pacing_status_active_backoff(pacing_client, monkeypatch):
    monkeypatch.setattr(
        GlobalIciciApiPacer,
        "get_backoff_snapshot",
        classmethod(
            lambda cls, user_id: BackoffSnapshot(
                active=True,
                reason="ICICI returned HTTP 429 (Too Many Requests)",
                seconds_remaining=3,
                endpoint="quotes",
                throttling_active=True,
            )
        ),
    )
    r = pacing_client.get("/api/icici/pacing-status")
    assert r.status_code == 200
    body = r.json()
    assert body["throttling_active"] is True
    assert body["backing_off"] is True
    assert body["seconds_remaining"] == 3
    assert "429" in (body["reason"] or "")


def test_pacing_status_requires_broker_token():
    async def _ctx():
        return RequestContext(
            user_id="iciciuser1",
            username="iciciuser1",
            roles=["trader"],
            is_authenticated=True,
            broker_token=None,
        )

    app = FastAPI()
    app.include_router(route_icici.router, prefix="")
    app.dependency_overrides[get_request_context] = _ctx
    with TestClient(app) as client:
        r = client.get("/api/icici/pacing-status")
    assert r.status_code == 401
