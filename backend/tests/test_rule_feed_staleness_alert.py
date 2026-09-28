"""PB/SL stale-price alert: an armed rule the engine cannot judge for lack of prices.

The load-bearing property is one message pair per incident. A thin strike that ticks
every couple of minutes flips a rule between checkable and not all day, and the futures
feed alert's first-fresh-tick recovery turned exactly that into dozens of messages.
"""
from __future__ import annotations

import pytest

from icici_breeze_backend.app.services import feed_alerts
from icici_breeze_backend.app.services import portfolio_pnl_engine as engine
from icici_breeze_backend.app.services import squareoff_protection_guard as guard

USER = "VIKRAMMH"
STOCK = "NIFTY"
EXPIRY = "21-Jul-2026"
RULE = "rule-1"
T0 = 1_780_000_000.0


def _row(*, evaluable: bool, age: float | None = 200.0) -> dict:
    return {
        "stock_code": STOCK,
        "strike": 22750.0,
        "right": "put",
        "rule_evaluable": evaluable,
        "has_live_quote": age is not None,
        "quote_age_seconds": age,
    }


def _observe(at: float, *, evaluable: bool) -> None:
    engine._note_group_feed(USER, RULE, [_row(evaluable=evaluable)], at)


def _check(at: float, floor: float | None = None) -> None:
    guard.check_rule_feed_staleness(USER, at, floor)


@pytest.fixture(autouse=True)
def _clean():
    engine.set_group_rule(USER, RULE, stock_code=STOCK, expiry_display=EXPIRY)
    guard._rule_incidents.clear()
    with engine._feed_state_lock:
        engine._group_feed.clear()
    yield
    engine.clear_group_rule(USER, STOCK, EXPIRY)
    guard._rule_incidents.clear()
    with engine._feed_state_lock:
        engine._group_feed.clear()


@pytest.fixture
def sent(monkeypatch):
    import icici_breeze_backend.app.services.telegram_alerts as tg

    out: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(
        tg,
        "notify_rule_prices_stale",
        lambda uid, states, **kw: out.append(("stale", [s["rule_id"] for s in states])),
    )
    monkeypatch.setattr(
        tg,
        "notify_rule_prices_restored",
        lambda uid, states, **kw: out.append(("restored", [s["rule_id"] for s in states])),
    )
    return out


def test_no_alert_inside_the_grace_window(sent):
    _observe(T0, evaluable=False)
    _observe(T0 + 20, evaluable=False)
    _check(T0 + 20)
    assert sent == []


def test_alerts_once_after_grace_and_not_again_while_it_lasts(sent):
    _observe(T0, evaluable=False)
    for dt in (31, 90, 150, 600):
        _observe(T0 + dt, evaluable=False)
        _check(T0 + dt)
    assert sent == [("stale", [RULE])]


def test_blind_since_is_not_reset_by_later_blind_ticks():
    _observe(T0, evaluable=False)
    _observe(T0 + 50, evaluable=False)
    assert engine.group_rule_feed_states(USER)[0]["blind_since"] == T0


def test_a_brief_recovery_is_not_announced_and_does_not_reopen_the_incident(sent):
    """The flapping case: one tick makes the rule checkable for a while, then it goes
    blind again. Neither edge is a new message."""
    _observe(T0, evaluable=False)
    _observe(T0 + 40, evaluable=False)
    _check(T0 + 40)
    _observe(T0 + 100, evaluable=True)
    _check(T0 + 160)
    _observe(T0 + 220, evaluable=False)
    _observe(T0 + 300, evaluable=False)
    _check(T0 + 300)
    assert sent == [("stale", [RULE])]


def test_recovery_is_announced_once_prices_hold_fresh(sent):
    _observe(T0, evaluable=False)
    _observe(T0 + 40, evaluable=False)
    _check(T0 + 40)
    _observe(T0 + 100, evaluable=True)
    _observe(T0 + 100 + feed_alerts.RECOVERY_HOLD_SECONDS, evaluable=True)
    _check(T0 + 100 + feed_alerts.RECOVERY_HOLD_SECONDS)
    _check(T0 + 200 + feed_alerts.RECOVERY_HOLD_SECONDS)
    assert sent == [("stale", [RULE]), ("restored", [RULE])]


def test_a_new_incident_after_recovery_alerts_again(sent):
    _observe(T0, evaluable=False)
    _observe(T0 + 40, evaluable=False)
    _check(T0 + 40)
    t = T0 + 100
    _observe(t, evaluable=True)
    t += feed_alerts.RECOVERY_HOLD_SECONDS
    _observe(t, evaluable=True)
    _check(t)
    _observe(t + 10, evaluable=False)
    _observe(t + 50, evaluable=False)
    _check(t + 50)
    assert [k for k, _ in sent] == ["stale", "restored", "stale"]


def test_healthy_rule_is_never_told_it_recovered(sent):
    _observe(T0, evaluable=True)
    _observe(T0 + 1000, evaluable=True)
    _check(T0 + 1000)
    assert sent == []


def test_blindness_counts_only_from_the_open_floor(sent):
    """Quotes left over from yesterday are stale at the bell by definition."""
    _observe(T0, evaluable=False)
    floor = T0 + 600
    _observe(floor + 10, evaluable=False)
    _check(floor + 10, floor)
    assert sent == []
    _observe(floor + 40, evaluable=False)
    _check(floor + 40, floor)
    assert sent == [("stale", [RULE])]


def test_no_alert_when_the_engine_has_stopped_observing(sent):
    """A cold registry freezes the last observation; that case belongs to the suspended
    alert, and a frozen 'blind' must not be read as a live one."""
    _observe(T0, evaluable=False)
    _check(T0 + guard._FEED_OBSERVATION_MAX_AGE_SECONDS + 60)
    assert sent == []


def test_a_rule_that_left_the_engine_is_dropped_silently(sent):
    _observe(T0, evaluable=False)
    _observe(T0 + 40, evaluable=False)
    _check(T0 + 40)
    engine.clear_group_rule(USER, STOCK, EXPIRY)
    _check(T0 + 1000)
    assert sent == [("stale", [RULE])]
    assert guard._rule_incidents.keys() == []
    assert engine.group_rule_feed_states(USER) == []


def test_market_close_ends_the_incident_without_a_recovery_message(sent, monkeypatch):
    import icici_breeze_backend.app.services.market_calendar as mc

    _observe(T0, evaluable=False)
    _observe(T0 + 40, evaluable=False)
    _check(T0 + 40)
    monkeypatch.setattr(mc, "is_market_open", lambda *a, **k: False)
    guard.protection_guard_tick()
    assert guard._rule_incidents.keys() == []
    assert sent == [("stale", [RULE])]


def test_engine_records_the_stale_leg_it_skipped_the_rule_for(monkeypatch):
    """End-to-end through `_evaluate_rules`: the gate that stands the rule down is the one
    that records why."""
    monkeypatch.setattr(engine, "_check_group_drift", lambda *a, **k: False)
    leg = engine.PositionLeg(
        user_id=USER,
        stock_code=STOCK,
        exchange_code="NFO",
        expiry_display=EXPIRY,
        strike=22750.0,
        right="put",
        quantity=130,
        average_price=1.0,
        action="sell",
    )
    stale_ts = T0 - 400
    snapshot = engine._evaluate_user_pnl(
        USER,
        [leg],
        {leg.scrip_key: {"ltp": "1.5", "timestamp": str(stale_ts)}},
        stream_stale=False,
    )
    engine._evaluate_rules(snapshot, {leg.scrip_key: leg})

    [state] = engine.group_rule_feed_states(USER)
    assert state["blind_since"] == snapshot["computed_at"]
    [stale] = state["stale_legs"]
    assert stale["label"] == "NIFTY 22750 PE"
    assert stale["last_quote_at"] == pytest.approx(stale_ts, abs=1.0)


def test_messages_are_the_simple_unmonitored_pair():
    import icici_breeze_backend.app.services.telegram_alerts as tg

    state = {"rule_id": RULE, "stock_code": STOCK, "expiry_display": EXPIRY}
    down = tg._format_rule_prices_stale_message([state], hold_minutes=5)
    assert "Feed from ICICI is stopped" in down and "currently unmonitored" in down
    assert f"• {STOCK} · {EXPIRY}" in down
    assert "re-arm" not in down, "the rule resumes by itself; a re-arm prompt would mislead"
    back = tg._format_rule_prices_restored_message([state], hold_minutes=5)
    assert "worked for 5 min" in back and "monitored again" in back
