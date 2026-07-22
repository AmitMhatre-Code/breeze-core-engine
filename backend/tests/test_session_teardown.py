"""Logout must distinguish "I'm leaving" from "your app session lapsed".

The bug this pins: the frontend's automatic 401 sign-out used to clear the persisted
broker session token, which is the only thing letting `squareoff_dispatcher` place exit
orders with no HTTP request in scope. A background tab going stale therefore disarmed
live PB/SL, and the failure only surfaced at the breach.
"""
from __future__ import annotations

import pytest

from icici_breeze_backend.app.db.squareoff_rules_migrate import ensure_squareoff_rules_table
from icici_breeze_backend.app.repositories import squareoff_rules as repo
from icici_breeze_backend.app.services import session_teardown


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = str(tmp_path / "users_test.sqlite3")
    monkeypatch.setattr(repo, "_db_path", lambda: path)
    ensure_squareoff_rules_table(path)
    return path


@pytest.fixture
def spy(monkeypatch):
    """Record what teardown did, without touching the real DB/caches/Telegram."""
    calls: dict[str, list] = {"cleared": [], "expired_alert": [], "logout_alert": []}

    monkeypatch.setattr(
        "icici_breeze_backend.app.repositories.broker_session.clear_broker_session_token",
        lambda user_id: calls["cleared"].append(user_id),
    )
    for module in (
        "icici_breeze_backend.app.services.breeze_session_cache",
        "icici_breeze_backend.app.services.broker_snapshot_cache",
        "icici_breeze_backend.app.services.customer_details_cache",
    ):
        monkeypatch.setattr(f"{module}.evict", lambda *a, **k: None)

    monkeypatch.setattr(
        session_teardown.telegram_alerts,
        "notify_session_expired_with_live_rules",
        lambda user_id, rules: calls["expired_alert"].append((user_id, rules)),
    )
    monkeypatch.setattr(
        session_teardown.telegram_alerts,
        "notify_logout_stopped_monitoring",
        lambda user_id, rules: calls["logout_alert"].append((user_id, rules)),
    )
    # Per-user alert cooldown is process-global; start each test from a clean slate.
    session_teardown._expiry_alert_sent_at.clear()
    return calls


def _arm(user_id="u1", stock_code="NIFTY", expiry_display="30-Jun-2026"):
    return repo.arm_rule(
        user_id,
        stock_code=stock_code,
        expiry_display=expiry_display,
        exchange_code="NFO",
        profit_target_pnl=100000.0,
        loss_limit_pnl=20000.0,
        target_premium_pct=10,
        stop_loss_premium_pct=5,
    )


class TestSessionExpiry:
    def test_keeps_the_broker_session_so_armed_rules_keep_firing(self, db_path, spy):
        _arm()
        session_teardown.teardown_session("u1", "tok", deliberate=False)
        assert spy["cleared"] == []

    def test_alerts_that_monitoring_is_still_running(self, db_path, spy):
        _arm()
        session_teardown.teardown_session("u1", "tok", deliberate=False)

        assert len(spy["expired_alert"]) == 1
        user_id, rules = spy["expired_alert"][0]
        assert user_id == "u1"
        assert [r.stock_code for r in rules] == ["NIFTY"]
        assert spy["logout_alert"] == []

    def test_stays_quiet_when_nothing_is_armed(self, db_path, spy):
        session_teardown.teardown_session("u1", "tok", deliberate=False)
        assert spy["expired_alert"] == []

    def test_dedupes_across_tabs(self, db_path, spy):
        """Every open tab's 401 handler POSTs its own logout — one alert, not five."""
        _arm()
        for _ in range(5):
            session_teardown.teardown_session("u1", "tok", deliberate=False)
        assert len(spy["expired_alert"]) == 1

    def test_dedupe_is_per_user(self, db_path, spy):
        _arm(user_id="u1")
        _arm(user_id="u2")
        session_teardown.teardown_session("u1", "tok", deliberate=False)
        session_teardown.teardown_session("u2", "tok", deliberate=False)
        assert len(spy["expired_alert"]) == 2


class TestDeliberateLogout:
    def test_clears_the_broker_session(self, db_path, spy):
        _arm()
        session_teardown.teardown_session("u1", "tok", deliberate=True)
        assert spy["cleared"] == ["u1"]

    def test_alerts_that_monitoring_has_stopped(self, db_path, spy):
        _arm()
        session_teardown.teardown_session("u1", "tok", deliberate=True)

        assert len(spy["logout_alert"]) == 1
        assert spy["expired_alert"] == []

    def test_is_never_deduped(self, db_path, spy):
        """Unlike expiry, each logout is a deliberate act and gets its own record."""
        _arm()
        session_teardown.teardown_session("u1", "tok", deliberate=True)
        session_teardown.teardown_session("u1", "tok", deliberate=True)
        assert len(spy["logout_alert"]) == 2

    def test_stays_quiet_when_nothing_is_armed(self, db_path, spy):
        session_teardown.teardown_session("u1", "tok", deliberate=True)
        assert spy["logout_alert"] == []
        assert spy["cleared"] == ["u1"]


class TestMonitoringRuleSelection:
    def test_reset_rules_are_not_counted(self, db_path, spy):
        """A Reset SG has already stopped monitoring; logging out takes nothing more
        away, so warning about it would be noise."""
        rule = _arm()
        repo.mark_reset(rule.id, "composition changed")

        session_teardown.teardown_session("u1", "tok", deliberate=True)
        assert spy["logout_alert"] == []

    def test_fired_rules_are_counted(self, db_path, spy):
        """`fired` still needs the order feed to reach Completed."""
        rule = _arm()
        repo.mark_triggered(rule.id)
        repo.mark_fired(rule.id, [])

        session_teardown.teardown_session("u1", "tok", deliberate=True)
        assert len(spy["logout_alert"]) == 1


def test_a_failure_anywhere_never_blocks_logging_out(db_path, spy, monkeypatch):
    monkeypatch.setattr(
        repo, "list_monitoring_rules", lambda user_id: (_ for _ in ()).throw(RuntimeError("db down"))
    )
    session_teardown.teardown_session("u1", "tok", deliberate=True)
    assert spy["cleared"] == ["u1"]
