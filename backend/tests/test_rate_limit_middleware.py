"""RateLimitMiddleware: real-client-IP keying, safe-method exemption, /auth/* + probe rules."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from icici_breeze_backend.app.middleware import rate_limit
from icici_breeze_backend.app.middleware.rate_limit import RateLimitMiddleware


@pytest.fixture(autouse=True)
def _reset_bucket(monkeypatch):
    """The bucket is a module global; clear it and pin a small limit per test."""
    rate_limit._rate_limit.clear()
    monkeypatch.setattr(rate_limit, "RATE_LIMIT_PER_MIN", 3)
    monkeypatch.setattr(rate_limit, "_E2E_BYPASS_SECRET", "")
    yield
    rate_limit._rate_limit.clear()


@pytest.fixture
def client():
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware)

    @app.get("/portfolio/data")
    async def _read():
        return {"ok": True}

    @app.post("/order/place")
    async def _write():
        return {"ok": True}

    @app.get("/auth/icici-redirect")
    async def _auth_get():
        return {"ok": True}

    @app.post("/auth/direct-login")
    async def _auth_post():
        return {"ok": True}

    @app.get("/health")
    async def _health():
        return {"status": "ok"}

    with TestClient(app) as c:
        yield c


def test_safe_gets_are_never_counted(client):
    for _ in range(10):
        assert client.get("/portfolio/data").status_code == 200


def test_mutations_are_limited(client):
    for _ in range(3):
        assert client.post("/order/place").status_code == 200
    r = client.post("/order/place")
    assert r.status_code == 429
    assert r.json() == {"detail": "Too many requests"}


def test_auth_gets_are_counted(client):
    for _ in range(3):
        assert client.get("/auth/icici-redirect").status_code == 200
    assert client.get("/auth/icici-redirect").status_code == 429


def test_auth_posts_are_counted(client):
    for _ in range(3):
        assert client.post("/auth/direct-login").status_code == 200
    assert client.post("/auth/direct-login").status_code == 429


def test_health_probe_is_exempt(client):
    for _ in range(10):
        assert client.get("/health").status_code == 200


def test_x_real_ip_separates_buckets(client):
    for _ in range(3):
        assert client.post("/order/place", headers={"X-Real-IP": "1.1.1.1"}).status_code == 200
    assert client.post("/order/place", headers={"X-Real-IP": "1.1.1.1"}).status_code == 429
    # A different real client behind the same proxy still has its full budget.
    assert client.post("/order/place", headers={"X-Real-IP": "2.2.2.2"}).status_code == 200


def test_x_forwarded_for_first_hop_is_the_key(client):
    hdr = {"X-Forwarded-For": "3.3.3.3, 10.0.0.1, 127.0.0.1"}
    for _ in range(3):
        assert client.post("/order/place", headers=hdr).status_code == 200
    assert client.post("/order/place", headers=hdr).status_code == 429
    # Same first hop, different downstream proxies appended -> same bucket.
    assert (
        client.post(
            "/order/place", headers={"X-Forwarded-For": "3.3.3.3, 172.16.0.9"}
        ).status_code
        == 429
    )


def test_x_real_ip_wins_over_forwarded_for(client):
    hdr = {"X-Real-IP": "4.4.4.4", "X-Forwarded-For": "9.9.9.9"}
    for _ in range(3):
        assert client.post("/order/place", headers=hdr).status_code == 200
    assert client.post("/order/place", headers=hdr).status_code == 429
    assert client.post("/order/place", headers={"X-Real-IP": "9.9.9.9"}).status_code == 200


def test_e2e_bypass_skips_counting(client, monkeypatch):
    monkeypatch.setattr(rate_limit, "_E2E_BYPASS_SECRET", "letmein")
    for _ in range(10):
        assert (
            client.post("/order/place", headers={"X-E2E-Test": "letmein"}).status_code
            == 200
        )
