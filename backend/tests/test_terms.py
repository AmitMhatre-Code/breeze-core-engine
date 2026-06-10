"""Core-engine terms proxy routes."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from icici_breeze_backend.app.api.deps import get_current_user
from icici_breeze_backend.app.api.v1 import route_terms
from icici_breeze_backend.app.auth.context import RequestContext


@pytest.fixture
def terms_client(monkeypatch):
    async def _user():
        return RequestContext(
            user_id="iciciuser1",
            username="iciciuser1",
            roles=["trader"],
            is_authenticated=True,
        )

    app = FastAPI()
    app.include_router(route_terms.router, prefix="")
    app.dependency_overrides[get_current_user] = _user
    with TestClient(app) as client:
        yield client


def test_terms_status_skipped_when_portal_unconfigured(terms_client, monkeypatch):
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.portal_terms._portal_configured",
        lambda: False,
    )
    r = terms_client.get("/api/terms/status")
    assert r.status_code == 200
    body = r.json()
    assert body["needs_acceptance"] is False
    assert body["portal_configured"] is False
