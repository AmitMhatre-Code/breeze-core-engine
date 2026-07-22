"""The market-data health dot's system prefetch is triggered from the auth
context helpers actually used across routes (`get_request_context` /
`get_request_context_or_redirect` in app/auth/context.py) -- NOT from
app/api/deps.py, since most routes bypass deps.py and call these directly."""
from __future__ import annotations

import asyncio

from icici_breeze_backend.app.auth import context as auth_context
from icici_breeze_backend.app.auth.context import (
    RequestContext,
    RedirectToLogin,
    get_request_context,
    get_request_context_or_redirect,
)


def _ctx(*, broker_token: str | None) -> RequestContext:
    return RequestContext(
        user_id="user1",
        username="user1",
        roles=["trader"],
        is_authenticated=True,
        broker_token=broker_token,
    )


def test_get_request_context_fires_prefetch_when_broker_token_present(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(auth_context, "extract_user_context", lambda request: _ctx(broker_token="tok"))
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.system_chain_health.maybe_trigger_system_prefetch",
        lambda user_id: calls.append(user_id),
    )
    ctx = asyncio.run(get_request_context(request=None))
    assert ctx.user_id == "user1"
    assert calls == ["user1"]


def test_get_request_context_skips_prefetch_without_broker_token(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(auth_context, "extract_user_context", lambda request: _ctx(broker_token=None))
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.system_chain_health.maybe_trigger_system_prefetch",
        lambda user_id: calls.append(user_id),
    )
    asyncio.run(get_request_context(request=None))
    assert calls == []


def test_get_request_context_or_redirect_fires_prefetch(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(auth_context, "extract_user_context", lambda request: _ctx(broker_token="tok"))
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.system_chain_health.maybe_trigger_system_prefetch",
        lambda user_id: calls.append(user_id),
    )
    ctx = asyncio.run(get_request_context_or_redirect(request=None))
    assert ctx.user_id == "user1"
    assert calls == ["user1"]


def test_get_request_context_or_redirect_raises_before_prefetch_without_token(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(auth_context, "extract_user_context", lambda request: _ctx(broker_token=None))
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.system_chain_health.maybe_trigger_system_prefetch",
        lambda user_id: calls.append(user_id),
    )
    try:
        asyncio.run(get_request_context_or_redirect(request=None))
        assert False, "expected RedirectToLogin"
    except RedirectToLogin:
        pass
    assert calls == []


def test_prefetch_trigger_failure_does_not_break_request(monkeypatch):
    """A broken system-health module must never take down normal auth."""
    monkeypatch.setattr(auth_context, "extract_user_context", lambda request: _ctx(broker_token="tok"))

    def _raise(user_id):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "icici_breeze_backend.app.services.system_chain_health.maybe_trigger_system_prefetch",
        _raise,
    )
    ctx = asyncio.run(get_request_context(request=None))
    assert ctx.user_id == "user1"
