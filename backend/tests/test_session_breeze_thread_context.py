"""A long-lived background thread must not keep yesterday's broker session.

`get_session_breeze` parks the session in a ContextVar meant to last one request. A plain
thread (bot-scheduler, exit arming, the scalper and CAS loops) never gets a fresh context,
so before the ownership check its first session was reused forever: after the midnight
token rollover Bot 2's margin calls failed on the stale session all expiry morning while the
same call from a request succeeded.
"""
from __future__ import annotations

import contextvars

import pytest

import icici_breeze_backend.app.services.processor as proc_mod
from icici_breeze_backend.app.services import breeze_session_cache


@pytest.fixture
def proc(monkeypatch):
    monkeypatch.setattr(proc_mod.cfg, "ICICI_BROKER_MODE", "mock")
    p = proc_mod.processor.__new__(proc_mod.processor)
    tokens = {"u1": "token-day1", "u2": "token-u2"}
    p._tokens = tokens
    monkeypatch.setattr(p, "_resolve_broker_token", lambda user_id: tokens.get(user_id) or "")
    yield p
    for user_id, token in list(tokens.items()) + [("u1", "token-day1"), ("u1", "token-day2")]:
        breeze_session_cache.evict(user_id, token)


def test_same_token_reuses_the_context_session(proc):
    ctx = contextvars.Context()
    first = ctx.run(proc.get_session_breeze, "u1")
    assert first is not None
    assert ctx.run(proc.get_session_breeze, "u1") is first


def test_token_rollover_replaces_the_thread_session(proc):
    ctx = contextvars.Context()  # one long-lived thread's context, reused across "days"
    day1 = ctx.run(proc.get_session_breeze, "u1")

    proc._tokens["u1"] = "token-day2"  # the user logged in again after midnight
    day2 = ctx.run(proc.get_session_breeze, "u1")

    assert day2 is not None
    assert day2 is not day1, "yesterday's session must not survive the token change"
    assert ctx.run(proc.get_session_breeze, "u1") is day2


def test_expired_token_reports_no_session(proc):
    ctx = contextvars.Context()
    assert ctx.run(proc.get_session_breeze, "u1") is not None

    del proc._tokens["u1"]  # token expired at midnight, nobody has logged in yet
    assert ctx.run(proc.get_session_breeze, "u1") is None, (
        "a stale session made the login nag believe the user was logged in"
    )


def test_one_thread_serving_two_users_does_not_share_a_session(proc):
    ctx = contextvars.Context()
    a = ctx.run(proc.get_session_breeze, "u1")
    b = ctx.run(proc.get_session_breeze, "u2")
    assert a is not b


def test_unstamped_context_session_is_still_honoured(proc):
    """Sessions put in the context by something other than get_session_breeze (tests,
    fakes) carry no owner; they keep the old reuse behaviour."""
    from icici_breeze_backend.app.auth.context import set_breeze_session_for_request

    sentinel = object()

    def _run():
        set_breeze_session_for_request(sentinel)
        return proc.get_session_breeze("u1")

    assert contextvars.Context().run(_run) is sentinel
