"""Deployment-side half of portal-routed Telegram linking.

Covers the two hops this side owns — registering a token so the portal knows
where to route it, and claiming what the portal routed back — plus the claim
loop's duty cycle, which must send no traffic at all when nobody is linking.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from icici_breeze_backend.app.services import telegram_link_portal as lp

_BASE = "https://portal.example.com"


class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"ok": True}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=None)

    def json(self):
        return self._payload


class _FakeAsyncClient:
    """Stands in for httpx.AsyncClient; records posts and replays scripted responses."""

    calls: list[tuple[str, dict]] = []
    responses: list = []
    raises: Exception | None = None

    def __init__(self, *_args, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def post(self, url, json=None):
        type(self).calls.append((url, json or {}))
        if type(self).raises is not None:
            raise type(self).raises
        if type(self).responses:
            return type(self).responses.pop(0)
        return _Resp()


@pytest.fixture
def portal(monkeypatch):
    monkeypatch.setattr(lp.cfg, "PORTAL_API_BASE_URL", _BASE)
    monkeypatch.setattr(lp.cfg, "DEPLOYMENT_LICENSE_KEY", "test-license-key")
    monkeypatch.setattr(lp, "_public_ip_from_origin", lambda: "203.0.113.10")
    monkeypatch.setattr(lp, "portal_host_allowed", lambda _base: True)
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.responses = []
    _FakeAsyncClient.raises = None
    monkeypatch.setattr(lp.httpx, "AsyncClient", _FakeAsyncClient)
    return _FakeAsyncClient


class TestRegister:
    def test_posts_identity_and_token(self, portal):
        asyncio.run(lp.register_link_token("tok-1"))
        url, body = portal.calls[0]
        assert url == f"{_BASE}/api/public/telegram/link-register"
        assert body == {
            "public_ip": "203.0.113.10",
            "license_key": "test-license-key",
            "token": "tok-1",
        }

    def test_omits_license_key_when_unlicensed(self, portal, monkeypatch):
        monkeypatch.setattr(lp.cfg, "DEPLOYMENT_LICENSE_KEY", "")
        asyncio.run(lp.register_link_token("tok-1"))
        assert "license_key" not in portal.calls[0][1]

    def test_raises_when_portal_unreachable(self, portal):
        portal.raises = httpx.ConnectError("down")
        with pytest.raises(lp.PortalLinkUnavailable):
            asyncio.run(lp.register_link_token("tok-1"))

    def test_raises_when_portal_declines_routing(self, portal):
        """403 is the portal refusing to route for a revoked deployment."""
        portal.responses = [_Resp(status_code=403)]
        with pytest.raises(lp.PortalLinkUnavailable):
            asyncio.run(lp.register_link_token("tok-1"))

    def test_raises_when_portal_not_configured(self, portal, monkeypatch):
        monkeypatch.setattr(lp.cfg, "PORTAL_API_BASE_URL", "")
        with pytest.raises(lp.PortalLinkUnavailable):
            asyncio.run(lp.register_link_token("tok-1"))

    def test_raises_when_portal_host_not_allowlisted(self, portal, monkeypatch):
        monkeypatch.setattr(lp, "portal_host_allowed", lambda _base: False)
        with pytest.raises(lp.PortalLinkUnavailable):
            asyncio.run(lp.register_link_token("tok-1"))

    def test_success_wakes_the_claim_loop(self, portal):
        lp._link_pending.clear()
        asyncio.run(lp.register_link_token("tok-1"))
        assert lp._link_pending.is_set()


class TestClaim:
    def test_returns_events(self, portal):
        portal.responses = [_Resp(payload={"events": [{"token": "t", "chat_id": "9"}]})]
        events = asyncio.run(lp.claim_link_events())
        assert events == [{"token": "t", "chat_id": "9"}]
        assert portal.calls[0][0] == f"{_BASE}/api/public/telegram/link-claim"

    def test_empty_list_is_not_a_failure(self, portal):
        portal.responses = [_Resp(payload={"events": []})]
        assert asyncio.run(lp.claim_link_events()) == []

    def test_failure_reports_none_so_caller_can_back_off(self, portal):
        """None vs [] is the distinction that keeps a persistent failure from
        becoming a hot retry loop — a failed claim returns immediately."""
        portal.raises = httpx.ConnectError("down")
        assert asyncio.run(lp.claim_link_events()) is None

    def test_malformed_body_reports_failure(self, portal):
        portal.responses = [_Resp(payload=["not", "a", "dict"])]
        assert asyncio.run(lp.claim_link_events()) is None


class TestClaimLoopDutyCycle:
    """The loop must be silent when nobody is linking: an always-on 2s poll
    against the portal would be the same waste the old Telegram poller had."""

    def _run_loop_once(self, monkeypatch, *, outstanding, claim_result=None):
        claims = []
        sleeps = []

        async def fake_claim():
            claims.append(1)
            if len(claims) > 1:
                raise asyncio.CancelledError
            return claim_result

        async def fake_sleep(seconds):
            sleeps.append(seconds)

        monkeypatch.setattr(lp, "claim_link_events", fake_claim)
        monkeypatch.setattr(lp.asyncio, "sleep", fake_sleep)
        monkeypatch.setattr(lp, "has_outstanding_link_token", lambda: outstanding)
        lp._link_pending.set()

        async def _run():
            with pytest.raises(asyncio.CancelledError):
                await lp.run_link_claim_loop()

        asyncio.run(_run())
        return claims, sleeps

    def test_no_claim_when_no_token_is_outstanding(self, monkeypatch):
        claims = []

        async def fake_claim():
            claims.append(1)
            return []

        monkeypatch.setattr(lp, "claim_link_events", fake_claim)
        monkeypatch.setattr(lp, "has_outstanding_link_token", lambda: False)
        lp._link_pending.set()

        async def _run():
            # One pass: clears the event, then blocks on wait() forever.
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(lp.run_link_claim_loop(), timeout=0.05)

        asyncio.run(_run())
        assert claims == []
        assert not lp._link_pending.is_set()

    def test_successful_claim_polls_at_the_short_interval(self, monkeypatch):
        _claims, sleeps = self._run_loop_once(monkeypatch, outstanding=True, claim_result=[])
        assert sleeps == [lp._CLAIM_INTERVAL_SEC]

    def test_failed_claim_backs_off(self, monkeypatch):
        _claims, sleeps = self._run_loop_once(monkeypatch, outstanding=True, claim_result=None)
        assert sleeps == [lp._BACKOFF_INITIAL_SEC]


class TestEnabled:
    def test_disabled_without_portal_base_url(self, portal, monkeypatch):
        monkeypatch.setattr(lp.cfg, "PORTAL_API_BASE_URL", "")
        assert lp.portal_linking_enabled() is False

    def test_disabled_without_resolvable_public_ip(self, portal, monkeypatch):
        monkeypatch.setattr(lp, "_public_ip_from_origin", lambda: "")
        assert lp.portal_linking_enabled() is False

    def test_enabled_when_both_present(self, portal):
        assert lp.portal_linking_enabled() is True
