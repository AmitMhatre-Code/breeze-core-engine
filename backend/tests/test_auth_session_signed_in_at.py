"""`/auth/session` reports the sign-in's `signed_in_at` (the access token's `iat`), which
the frontend keys the risk-disclosure ack on so it holds across tabs of one sign-in
and is asked again after the next."""
from __future__ import annotations

import asyncio
import json

from starlette.requests import Request

import icici_breeze_backend.app.core.config as app_cfg
import icici_breeze_backend.core.config as core_cfg
from icici_breeze_backend.app.api.v1.auth import auth_session_status
from icici_breeze_backend.app.auth.context import ACCESS_TOKEN_COOKIE, ICICI_BROKER_TOKEN_COOKIE
from icici_breeze_backend.app.auth.jwt_handler import JWTHandler

SECRET = "test-secret-for-signed-in-at"


def _request(cookies: dict[str, str]) -> Request:
    cookie = "; ".join(f"{k}={v}" for k, v in cookies.items())
    return Request({"type": "http", "method": "GET", "path": "/auth/session", "headers": [(b"cookie", cookie.encode())]})


def _session(cookies: dict[str, str]) -> tuple[int, dict]:
    resp = asyncio.run(auth_session_status(_request(cookies)))
    return resp.status_code, json.loads(resp.body)


def test_session_reports_token_issue_time(monkeypatch):
    monkeypatch.setattr(app_cfg, "JWT_SECRET", SECRET)
    monkeypatch.setattr(core_cfg, "JWT_SECRET", SECRET)
    token = JWTHandler(secret_key=SECRET).create_access_token("abc123", "abc123")
    iat = JWTHandler(secret_key=SECRET).validate_token(token).iat

    status, body = _session({ACCESS_TOKEN_COOKIE: token, ICICI_BROKER_TOKEN_COOKIE: "tok"})

    assert status == 200
    assert body == {"authenticated": True, "user_id": "ABC123", "signed_in_at": iat}


def test_session_unauthenticated_has_no_sign_in(monkeypatch):
    monkeypatch.setattr(app_cfg, "JWT_SECRET", SECRET)
    monkeypatch.setattr(core_cfg, "JWT_SECRET", SECRET)

    status, body = _session({})

    assert status == 401
    assert body == {"authenticated": False}
