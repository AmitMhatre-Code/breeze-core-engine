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


def test_terms_current_unconfigured(terms_client, monkeypatch):
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.portal_terms._portal_configured",
        lambda: False,
    )
    r = terms_client.get("/api/terms/current")
    assert r.status_code == 503


def test_terms_current_proxy(terms_client, monkeypatch):
    async def _fetch():
        return {
            "version": 1,
            "content_markdown": "# Terms v1",
            "effective_date": "2026-06-10",
        }

    monkeypatch.setattr(
        "icici_breeze_backend.app.api.v1.route_terms.fetch_portal_terms_current",
        _fetch,
    )
    r = terms_client.get("/api/terms/current")
    assert r.status_code == 200
    assert r.json()["version"] == 1


def test_terms_status_unconfigured(terms_client, monkeypatch):
    async def _status(*, icici_user_id: str):
        return {
            "needs_acceptance": False,
            "current_version": None,
            "accepted_version": None,
            "accepted_at": None,
            "content_markdown": None,
            "portal_configured": False,
        }

    monkeypatch.setattr(
        "icici_breeze_backend.app.api.v1.route_terms.fetch_portal_terms_status",
        _status,
    )
    r = terms_client.get("/api/terms/status")
    assert r.status_code == 200
    assert r.json()["portal_configured"] is False


def test_terms_status_needs_acceptance(terms_client, monkeypatch):
    async def _status(*, icici_user_id: str):
        assert icici_user_id == "iciciuser1"
        return {
            "needs_acceptance": True,
            "current_version": 2,
            "accepted_version": None,
            "accepted_at": None,
            "content_markdown": "# Terms v2",
            "portal_configured": True,
        }

    async def _current():
        return {
            "version": 2,
            "content_markdown": "# Terms v2",
            "effective_date": "2026-06-14",
        }

    monkeypatch.setattr(
        "icici_breeze_backend.app.api.v1.route_terms.fetch_portal_terms_status",
        _status,
    )
    monkeypatch.setattr(
        "icici_breeze_backend.app.api.v1.route_terms.fetch_portal_terms_current",
        _current,
    )
    r = terms_client.get("/api/terms/status")
    assert r.status_code == 200
    body = r.json()
    assert body["needs_acceptance"] is True
    assert body["effective_date"] == "2026-06-14"


def test_terms_accept_success(terms_client, monkeypatch):
    async def _accept(*, icici_user_id: str, terms_version: int):
        assert icici_user_id == "iciciuser1"
        assert terms_version == 2
        return {"ok": True, "terms_version": 2}

    monkeypatch.setattr(
        "icici_breeze_backend.app.api.v1.route_terms.post_portal_terms_accept",
        _accept,
    )
    r = terms_client.post("/api/terms/accept", json={"terms_version": 2})
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_terms_accept_version_mismatch(terms_client, monkeypatch):
    async def _accept(*, icici_user_id: str, terms_version: int):
        return {
            "ok": False,
            "detail": "Terms version mismatch; refresh and accept the latest version",
            "status_code": 409,
        }

    monkeypatch.setattr(
        "icici_breeze_backend.app.api.v1.route_terms.post_portal_terms_accept",
        _accept,
    )
    r = terms_client.post("/api/terms/accept", json={"terms_version": 1})
    assert r.status_code == 409
