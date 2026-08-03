"""Patient, status-gated retry for square-off exit legs.

Two things make this safe to do at all, and both are load-bearing:

  * only an explicit ICICI *throttle* is retried — a throttle is a refusal, so re-sending
    cannot duplicate an order, whereas a timeout carries no such guarantee;
  * the rule's status is re-read before every retry, so a user who acts on the position
    during the wait (from this app, ICICI web, or the mobile app) causes us to stand down
    rather than race them into a contra position.

Without the second, a longer retry would be strictly more dangerous than the fail-fast it
replaces.
"""
from __future__ import annotations

import pytest

from icici_breeze_backend.app.db.squareoff_rules_migrate import ensure_squareoff_rules_table
from icici_breeze_backend.app.repositories import squareoff_rules as repo
from icici_breeze_backend.app.services import squareoff_dispatcher as d

USER = "VIKRAMMH"
EXPIRY = "21-Jul-2026"

LEG = {
    "scrip_key": f"NFO|NIFTY|{EXPIRY}|26000|call",
    "stock_code": "NIFTY",
    "strike_price": "26000",
    "right": "Call",
    "quantity": 130,
    "action": "Buy",
    "product_type": "options",
    "expiry_display": EXPIRY,
    "exchange_code": "NFO",
    "ltp": 1.75,
}

THROTTLED = {
    "Status": 429,
    "Error": "You have been throttled by ICICI. Please try again in a minute.",
    "icici_throttled": True,
}
DAILY_DONE = {
    "Status": 429,
    "Error": "You have crossed the daily limit of 5000 API calls.",
    "icici_throttled": True,
    "daily_limit_exhausted": True,
}
REJECTED = {"Status": 400, "Error": "Insufficient margin"}
OK = {"Status": 200, "Success": {"order_id": "ORD-1"}}


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = str(tmp_path / "users_test.sqlite3")
    monkeypatch.setattr(repo, "_db_path", lambda: path)
    ensure_squareoff_rules_table(path)
    return path


@pytest.fixture(autouse=True)
def _no_sleeping(monkeypatch):
    """Keep the retry schedule's shape (4 attempts) without the ~50s wall clock."""
    monkeypatch.setattr(d.time, "sleep", lambda *_: None)


class FakeBreeze:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def place_order(self, **kwargs):
        self.calls += 1
        if self._responses:
            return self._responses.pop(0)
        return OK


def _triggered_rule():
    rule = repo.arm_rule(
        USER,
        stock_code="NIFTY",
        expiry_display=EXPIRY,
        exchange_code="NFO",
        profit_target_pnl=100000.0,
        loss_limit_pnl=20000.0,
        target_premium_pct=10,
        stop_loss_premium_pct=5,
        legs_snapshot={},
    )
    repo.mark_triggered(rule.id)
    return rule


def _place(breeze, rule_id, on_first_retry=lambda: None):
    return d._place_chunk_with_retry(
        breeze,
        user_id=USER,
        rule_id=rule_id,
        leg=LEG,
        chunk_qty=130,
        limit_price=1.75,
        on_first_retry=on_first_retry,
    )


# ------------------------------------------------------------------ what is retryable


def test_throttle_is_retryable():
    assert d._is_retryable_throttle(THROTTLED) is True


def test_plain_rejection_is_not_retryable():
    """A margin rejection will still be a margin rejection in 50 seconds."""
    assert d._is_retryable_throttle(REJECTED) is False


def test_daily_limit_is_not_retryable():
    """It will not clear before midnight IST — waiting only delays the bad news."""
    assert d._is_retryable_throttle(DAILY_DONE) is False


# ------------------------------------------------------------------ retry behaviour


def test_retries_through_a_throttle_and_succeeds(db_path):
    rule = _triggered_rule()
    breeze = FakeBreeze([THROTTLED, THROTTLED, OK])
    order_id, error = _place(breeze, rule.id)
    assert (order_id, error) == ("ORD-1", None)
    assert breeze.calls == 3


def test_gives_up_after_the_full_schedule(db_path):
    rule = _triggered_rule()
    breeze = FakeBreeze([THROTTLED] * 10)
    order_id, error = _place(breeze, rule.id)
    assert order_id is None
    assert breeze.calls == len(d._RETRY_DELAYS_SEC) + 1
    assert "throttled" in (error or "").lower()


def test_non_throttle_failure_is_not_retried(db_path):
    rule = _triggered_rule()
    breeze = FakeBreeze([REJECTED, OK])
    order_id, error = _place(breeze, rule.id)
    assert order_id is None
    assert error == "Insufficient margin"
    assert breeze.calls == 1, "a rejection must not be re-sent"


def test_success_first_time_makes_one_call(db_path):
    rule = _triggered_rule()
    breeze = FakeBreeze([OK])
    assert _place(breeze, rule.id) == ("ORD-1", None)
    assert breeze.calls == 1


# ------------------------------------------------------------------ the stand-down


def test_retry_aborts_when_the_rule_is_reset_mid_wait(db_path):
    """The user acted on the position while we were waiting out a throttle. Continuing
    would race their fill and could open a contra position."""
    rule = _triggered_rule()

    class ResettingBreeze(FakeBreeze):
        def place_order(self, **kwargs):
            self.calls += 1
            repo.mark_reset(rule.id, "user squared off manually")
            return THROTTLED

    breeze = ResettingBreeze([])
    order_id, error = _place(breeze, rule.id)
    assert order_id is None
    assert "reset" in (error or "").lower()
    assert breeze.calls == 1, "must not place again once the rule stopped being ours"


def test_still_firing_is_true_only_while_triggered(db_path):
    rule = _triggered_rule()
    assert d._still_firing(rule.id) is True
    repo.mark_reset(rule.id, "whatever")
    assert d._still_firing(rule.id) is False


def test_missing_rule_is_not_still_firing(db_path):
    assert d._still_firing("no-such-rule") is False


# ------------------------------------------------------------------ user is told


def test_user_is_told_once_when_retrying_starts(db_path):
    """Announced at the first retry, not after the last — the whole point is to reach the
    user *during* the wait, so they don't go place it manually themselves."""
    rule = _triggered_rule()
    breeze = FakeBreeze([THROTTLED, THROTTLED, OK])
    announcements = []
    _place(breeze, rule.id, on_first_retry=lambda: announcements.append(1))
    assert len(announcements) == 1


def test_no_announcement_when_nothing_is_retried(db_path):
    rule = _triggered_rule()
    announcements = []
    _place(FakeBreeze([OK]), rule.id, on_first_retry=lambda: announcements.append(1))
    assert announcements == []


def test_retry_message_promises_the_stand_down():
    from icici_breeze_backend.app.services.telegram_alerts import _format_retrying_message

    msg = _format_retrying_message(
        "group_target_hit", {"stock_code": "NIFTY", "expiry_display": EXPIRY}, 50
    )
    assert "stop retrying" in msg
    assert "duplicate" in msg


def test_failed_message_no_longer_just_says_check_the_app():
    from icici_breeze_backend.app.services.telegram_alerts import _format_message

    msg = _format_message(
        "group_target_hit",
        {"stock_code": "NIFTY", "expiry_display": EXPIRY},
        [{"status": "failed", "action": "Buy", "right": "Call", "strike_price": "26000",
          "quantity": "130", "error": "throttled"}],
        failed=True,
    )
    assert "check the app" not in msg.lower()
    assert "before placing anything yourself" in msg
