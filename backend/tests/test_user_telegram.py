"""Tests for Telegram alert linking: repo (app.repositories.user_telegram),
routes (route_settings_telegram), message formatting + dispatch
(telegram_alerts), the bot API wrapper (telegram_client), and the long-poll
handshake handler (telegram_bot_poller).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from icici_breeze_backend.app.db.user_telegram_migrate import ensure_user_telegram_table
from icici_breeze_backend.app.repositories import user_telegram as repo
from icici_breeze_backend.app.services import telegram_alerts, telegram_bot_poller, telegram_client


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = str(tmp_path / "users_test.sqlite3")
    monkeypatch.setattr(repo, "_db_path", lambda: path)
    ensure_user_telegram_table(path)
    return path


class TestRepository:
    def test_get_status_defaults_when_no_row(self, db_path):
        status = repo.get_status("u1")
        assert status["connected"] is False
        assert status["alerts_enabled"] is True
        assert status["onboarding_dismissed"] is False

    def test_generate_and_consume_link_token(self, db_path):
        token, expires_at = repo.generate_link_token("u1")
        assert token
        assert repo.consume_link_token(token) == "u1"

    def test_consume_is_single_use(self, db_path):
        token, _ = repo.generate_link_token("u1")
        assert repo.consume_link_token(token) == "u1"
        assert repo.consume_link_token(token) is None

    def test_consume_unknown_token_returns_none(self, db_path):
        assert repo.consume_link_token("does-not-exist") is None

    def test_consume_expired_token_returns_none(self, db_path):
        token, _ = repo.generate_link_token("u1")
        with repo._connect() as conn:
            past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
            conn.execute(
                "UPDATE user_telegram SET link_token_expires_at = ? WHERE user_id = ?",
                (past, "u1"),
            )
            conn.commit()
        assert repo.consume_link_token(token) is None

    def test_regenerating_token_invalidates_the_old_one(self, db_path):
        first, _ = repo.generate_link_token("u1")
        second, _ = repo.generate_link_token("u1")
        assert repo.consume_link_token(first) is None
        assert repo.consume_link_token(second) == "u1"

    def test_link_chat_marks_connected(self, db_path):
        repo.link_chat("u1", "12345", "someuser")
        status = repo.get_status("u1")
        assert status["connected"] is True
        assert status["telegram_chat_id"] == "12345"
        assert status["telegram_username"] == "someuser"

    def test_unlink_clears_chat(self, db_path):
        repo.link_chat("u1", "12345", "someuser")
        repo.unlink("u1")
        status = repo.get_status("u1")
        assert status["connected"] is False
        assert status["telegram_chat_id"] is None

    def test_set_onboarding_dismissed_persists(self, db_path):
        repo.set_onboarding_dismissed("u1", True)
        assert repo.get_status("u1")["onboarding_dismissed"] is True

    def test_set_alerts_enabled_persists(self, db_path):
        repo.set_alerts_enabled("u1", False)
        assert repo.get_status("u1")["alerts_enabled"] is False

    def test_status_is_scoped_per_user(self, db_path):
        repo.link_chat("u1", "111", "a")
        status = repo.get_status("u2")
        assert status["connected"] is False


def _ctx(user_id="u1"):
    from icici_breeze_backend.app.auth.context import RequestContext

    return RequestContext(user_id=user_id, username=user_id, roles=["trader"], is_authenticated=True)


class TestRoutes:
    def test_status_reports_bot_username_and_configured_flag(self, db_path, monkeypatch):
        from icici_breeze_backend.app.api.v1 import route_settings_telegram as route

        monkeypatch.setattr(route.cfg, "TELEGRAM_BOT_USERNAME", "BreezeAlertsBot")
        resp = asyncio.run(route.get_status(_ctx()))
        assert resp.bot_username == "BreezeAlertsBot"
        assert resp.bot_configured is True
        assert resp.connected is False

    def test_create_link_token_route_persists(self, db_path):
        from icici_breeze_backend.app.api.v1 import route_settings_telegram as route

        resp = asyncio.run(route.create_link_token(_ctx()))
        assert resp.link_token
        assert repo.consume_link_token(resp.link_token) == "u1"

    def test_unlink_route(self, db_path):
        from icici_breeze_backend.app.api.v1 import route_settings_telegram as route

        repo.link_chat("u1", "12345", "someuser")
        asyncio.run(route.unlink(_ctx()))
        assert repo.get_status("u1")["connected"] is False

    def test_set_onboarding_dismissed_route(self, db_path):
        from icici_breeze_backend.app.api.v1 import route_settings_telegram as route
        from icici_breeze_backend.app.domain.telegram_alerts import SetOnboardingDismissedRequest

        resp = asyncio.run(route.set_onboarding_dismissed(SetOnboardingDismissedRequest(dismissed=True), _ctx()))
        assert resp.onboarding_dismissed is True

    def test_set_alerts_enabled_route(self, db_path):
        from icici_breeze_backend.app.api.v1 import route_settings_telegram as route
        from icici_breeze_backend.app.domain.telegram_alerts import SetAlertsEnabledRequest

        resp = asyncio.run(route.set_alerts_enabled(SetAlertsEnabledRequest(enabled=False), _ctx()))
        assert resp.alerts_enabled is False

    def test_status_is_scoped_to_requesting_user(self, db_path):
        from icici_breeze_backend.app.api.v1 import route_settings_telegram as route

        repo.link_chat("u1", "12345", "someuser")
        resp = asyncio.run(route.get_status(_ctx("u2")))
        assert resp.connected is False


class TestMessageFormatting:
    def _leg(self, **overrides):
        base = {
            "stock_code": "NIFTY",
            "strike_price": "25000.0",
            "right": "Call",
            "quantity": "50",
            "action": "Sell",
            "status": "success",
            "price": "142.5",
            "error": None,
        }
        base.update(overrides)
        return base

    def _payload(self, **overrides):
        base = {
            "stock_code": "NIFTY",
            "expiry_display": "30-Jun-2026",
            "total_pnl": 18420.0,
        }
        base.update(overrides)
        return base

    def test_target_hit_uses_profit_booking_label(self):
        text = telegram_alerts._format_message(
            "group_target_hit", self._payload(), [self._leg()], failed=False
        )
        assert "Profit Booking Triggered" in text
        assert "NIFTY" in text
        assert "Filled" in text
        assert "18,420" in text

    def test_stop_loss_hit_uses_stop_loss_label(self):
        text = telegram_alerts._format_message(
            "group_stop_loss_hit", self._payload(), [self._leg()], failed=False
        )
        assert "Stop-Loss Triggered" in text

    def test_failed_leg_shows_error_not_price(self):
        leg = self._leg(status="failed", error="RMS:Margin Exceeds", price=None)
        text = telegram_alerts._format_message(
            "group_target_hit", self._payload(), [leg], failed=True
        )
        assert "RMS:Margin Exceeds" in text
        assert "did not go through" in text
        # The banner must not read as "go fix this yourself" — a manual fill placed against
        # a partially-executed exit is how a contra position gets opened.
        assert "check the app" not in text.lower()


class TestNotifyDispatch:
    def test_skips_when_not_connected(self, db_path, monkeypatch):
        sent = []
        monkeypatch.setattr(telegram_alerts, "send_message_sync", lambda chat_id, text: sent.append((chat_id, text)))
        telegram_alerts.notify_squareoff_fired(
            "u1", reason="group_target_hit", payload={"stock_code": "NIFTY", "expiry_display": "x"}, leg_results=[]
        )
        import time

        time.sleep(0.05)
        assert sent == []

    def test_skips_when_alerts_disabled(self, db_path, monkeypatch):
        repo.link_chat("u1", "12345", "someuser")
        repo.set_alerts_enabled("u1", False)
        sent = []
        monkeypatch.setattr(telegram_alerts, "send_message_sync", lambda chat_id, text: sent.append((chat_id, text)))
        telegram_alerts.notify_squareoff_fired(
            "u1", reason="group_target_hit", payload={"stock_code": "NIFTY", "expiry_display": "x"}, leg_results=[]
        )
        import time

        time.sleep(0.05)
        assert sent == []

    def test_sends_via_background_thread_when_connected(self, db_path, monkeypatch):
        repo.link_chat("u1", "12345", "someuser")
        sent = []
        monkeypatch.setattr(telegram_alerts, "send_message_sync", lambda chat_id, text: sent.append((chat_id, text)))
        telegram_alerts.notify_squareoff_fired(
            "u1",
            reason="group_target_hit",
            payload={"stock_code": "NIFTY", "expiry_display": "30-Jun-2026", "total_pnl": 100.0},
            leg_results=[],
        )
        import time

        for _ in range(20):
            if sent:
                break
            time.sleep(0.02)
        assert len(sent) == 1
        assert sent[0][0] == "12345"


class TestTelegramClient:
    def test_get_updates_returns_result_list(self):
        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"ok": True, "result": [{"update_id": 1}]}

        class FakeClient:
            async def get(self, url, params, timeout):
                return FakeResponse()

        async def _run():
            result = await telegram_client.get_updates(FakeClient(), None, 25)
            assert result == [{"update_id": 1}]

        asyncio.run(_run())

    def test_get_updates_swallows_http_errors_but_reports_failure(self):
        """None, not [] — the caller backs off on failure and must be able to
        tell a failed poll apart from a poll that simply saw no messages."""
        class FakeClient:
            async def get(self, url, params, timeout):
                raise telegram_client.httpx.HTTPError("boom")

        async def _run():
            result = await telegram_client.get_updates(FakeClient(), None, 25)
            assert result is None

        asyncio.run(_run())

    def test_get_updates_reports_failure_on_non_ok_body(self):
        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"ok": False, "description": "Conflict"}

        class FakeClient:
            async def get(self, url, params, timeout):
                return FakeResponse()

        async def _run():
            assert await telegram_client.get_updates(FakeClient(), None, 25) is None

        asyncio.run(_run())

    def test_get_updates_reuses_the_passed_client_not_a_fresh_one(self):
        """The whole point of taking `client` as a param instead of opening a
        new AsyncClient per call: verify no new client is constructed here."""
        calls = []

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"ok": True, "result": []}

        class FakeClient:
            async def get(self, url, params, timeout):
                calls.append(1)
                return FakeResponse()

        async def _run():
            client = FakeClient()
            await telegram_client.get_updates(client, None, 25)
            await telegram_client.get_updates(client, 5, 25)

        with patch.object(telegram_client.httpx, "AsyncClient", side_effect=AssertionError("must not construct a new client")):
            asyncio.run(_run())
        assert len(calls) == 2

    def test_send_message_sync_posts_markdown(self):
        captured = {}

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"ok": True}

        class FakeClient:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return None

            def post(self, url, json):
                captured.update(json)
                return FakeResponse()

        with patch.object(telegram_client.httpx, "Client", FakeClient):
            ok = telegram_client.send_message_sync("12345", "hello")
        assert ok is True
        assert captured["chat_id"] == "12345"
        assert captured["parse_mode"] == "Markdown"

    def test_send_message_sync_returns_false_on_error(self):
        class FakeClient:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return None

            def post(self, url, json):
                raise telegram_client.httpx.HTTPError("boom")

        with patch.object(telegram_client.httpx, "Client", FakeClient):
            ok = telegram_client.send_message_sync("12345", "hello")
        assert ok is False


class TestBotPoller:
    def test_valid_start_token_links_chat(self, db_path, monkeypatch):
        token, _ = repo.generate_link_token("u1")
        sent = []
        monkeypatch.setattr(telegram_bot_poller, "send_message_sync", lambda chat_id, text: sent.append((chat_id, text)))

        telegram_bot_poller._handle_message(
            {"text": f"/start {token}", "chat": {"id": 999}, "from": {"username": "trader1"}}
        )

        status = repo.get_status("u1")
        assert status["connected"] is True
        assert status["telegram_chat_id"] == "999"
        assert status["telegram_username"] == "trader1"
        assert len(sent) == 1
        assert "Connected" in sent[0][1]

    def test_expired_or_unknown_token_does_not_link(self, db_path, monkeypatch):
        sent = []
        monkeypatch.setattr(telegram_bot_poller, "send_message_sync", lambda chat_id, text: sent.append((chat_id, text)))

        telegram_bot_poller._handle_message(
            {"text": "/start not-a-real-token", "chat": {"id": 999}, "from": {}}
        )

        assert len(sent) == 1
        assert "expired" in sent[0][1].lower()

    def test_non_start_message_is_ignored(self, db_path, monkeypatch):
        sent = []
        monkeypatch.setattr(telegram_bot_poller, "send_message_sync", lambda chat_id, text: sent.append((chat_id, text)))

        telegram_bot_poller._handle_message({"text": "hello", "chat": {"id": 999}, "from": {}})

        assert sent == []


class TestPollLoopBackoff:
    """A failed `getUpdates` returns immediately instead of blocking for the
    long-poll timeout, so without backoff a persistent failure (Telegram's 409
    when another poller holds the same bot token) becomes a hot retry loop."""

    def _run_loop(self, results, monkeypatch):
        """Drives the real loop over a scripted sequence of get_updates results,
        recording every sleep, then cancels it once the script is exhausted."""
        sleeps: list[float] = []
        pending = list(results)

        async def fake_get_updates(client, offset, timeout):
            if not pending:
                raise asyncio.CancelledError
            return pending.pop(0)

        async def fake_sleep(seconds):
            sleeps.append(seconds)

        monkeypatch.setattr(telegram_bot_poller, "get_updates", fake_get_updates)
        monkeypatch.setattr(telegram_bot_poller.asyncio, "sleep", fake_sleep)
        monkeypatch.setattr(telegram_bot_poller, "_offset", None)

        async def _run():
            with pytest.raises(asyncio.CancelledError):
                await telegram_bot_poller.run_telegram_poll_loop()

        asyncio.run(_run())
        return sleeps

    def test_failures_back_off_exponentially_up_to_the_cap(self, monkeypatch):
        sleeps = self._run_loop([None] * 6, monkeypatch)
        assert sleeps == [5.0, 10.0, 20.0, 40.0, 60.0, 60.0]

    def test_successful_poll_does_not_sleep(self, monkeypatch):
        assert self._run_loop([[], []], monkeypatch) == []

    def test_backoff_resets_after_recovery(self, monkeypatch):
        sleeps = self._run_loop([None, None, [], None], monkeypatch)
        assert sleeps == [5.0, 10.0, 5.0]
