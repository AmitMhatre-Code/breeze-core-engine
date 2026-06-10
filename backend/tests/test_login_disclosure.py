"""Core-engine login disclosure proxy routes."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from icici_breeze_backend.app.api.deps import get_current_user
from icici_breeze_backend.app.api.v1 import route_login_disclosure
from icici_breeze_backend.app.auth.context import RequestContext


@pytest.fixture
def login_disclosure_client(monkeypatch):
    async def _user():
        return RequestContext(
            user_id="iciciuser1",
            username="iciciuser1",
            roles=["trader"],
            is_authenticated=True,
        )

    app = FastAPI()
    app.include_router(route_login_disclosure.router, prefix="")
    app.dependency_overrides[get_current_user] = _user
    with TestClient(app) as client:
        yield client


def test_login_disclosure_current_unconfigured(login_disclosure_client, monkeypatch):
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.portal_login_disclosure.portal_login_disclosure_configured",
        lambda: False,
    )
    r = login_disclosure_client.get("/api/login-disclosure/current")
    assert r.status_code == 503


def test_login_disclosure_current_proxy(login_disclosure_client, monkeypatch):
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.portal_login_disclosure.portal_login_disclosure_configured",
        lambda: True,
    )

    async def _fetch():
        return {
            "version": 1,
            "content_markdown": "### Risk\n\nProceed.",
            "effective_date": "2026-06-10",
        }

    monkeypatch.setattr(
        "icici_breeze_backend.app.services.portal_login_disclosure.fetch_portal_login_disclosure_current",
        _fetch,
    )
    r = login_disclosure_client.get("/api/login-disclosure/current")
    assert r.status_code == 200
    body = r.json()
    assert body["version"] == 1
    assert body["portal_configured"] is True


def test_login_disclosure_accept_proxy(login_disclosure_client, monkeypatch):
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.portal_login_disclosure.post_portal_login_disclosure_accept",
        lambda **_: {"ok": True, "icici_user_id": "ICICIUSER1", "disclosure_version": 1},
    )
    r = login_disclosure_client.post(
        "/api/login-disclosure/accept",
        json={"disclosure_version": 1},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True
