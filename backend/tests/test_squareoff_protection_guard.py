"""PB/SL protection guard: keeping the position registry warm for live SGs.

The load-bearing case is the restart one. `run_pnl_tick` iterates `_legs_by_user` and
returns early when it is empty, and that registry's only writer used to be a
`GET /portfolio/data` request — so a restarted instance restored its SGs, showed them as
Armed, and evaluated nothing until a human opened the Portfolio page. Everything here
exists to keep that from regressing silently.
"""
from __future__ import annotations

import pytest

from icici_breeze_backend.app.db.squareoff_protection_migrate import (
    ensure_squareoff_protection_table,
)
from icici_breeze_backend.app.db.squareoff_rules_migrate import ensure_squareoff_rules_table
from icici_breeze_backend.app.repositories import squareoff_protection as state_repo
from icici_breeze_backend.app.repositories import squareoff_rules as repo
from icici_breeze_backend.app.services import portfolio_pnl_engine as engine
from icici_breeze_backend.app.services import squareoff_protection_guard as guard

USER = "VIKRAMMH"
STOCK = "NIFTY"
EXPIRY = "21-Jul-2026"

_POSITION_ROW = {
    "stock_code": STOCK,
    "exchange_code": "NFO",
    "expiry_date": EXPIRY,
    "strike_price": "26000",
    "right": "call",
    "quantity": "130",
    "average_price": "120.5",
    "action": "sell",
}


def _positions_ok() -> dict:
    return {"Status": 200, "Success": {"positions": [dict(_POSITION_ROW)]}}


def _positions_error() -> dict:
    return {"Status": 400, "Error": "Unable to connect to broker.", "Success": None}


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = str(tmp_path / "users_test.sqlite3")
    monkeypatch.setattr(repo, "_db_path", lambda: path)
    monkeypatch.setattr(state_repo, "_db_path", lambda: path)
    ensure_squareoff_rules_table(path)
    ensure_squareoff_protection_table(path)
    return path


@pytest.fixture(autouse=True)
def _clean_registry():
    engine.clear_positions(USER)
    yield
    engine.clear_positions(USER)


@pytest.fixture
def alerts(monkeypatch):
    """Capture Telegram sends instead of dispatching them."""
    import icici_breeze_backend.app.services.telegram_alerts as tg

    sent: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        tg,
        "notify_protection_suspended",
        lambda uid, rules, *, first: sent.append(("suspended", {"first": first})),
    )
    monkeypatch.setattr(
        tg, "notify_protection_resumed", lambda uid, rules: sent.append(("resumed", {}))
    )
    return sent


@pytest.fixture(autouse=True)
def _market_open(monkeypatch):
    import icici_breeze_backend.app.services.market_calendar as mc

    monkeypatch.setattr(mc, "is_market_open", lambda *a, **k: True)


def _fake_processor(monkeypatch, response):
    import icici_breeze_backend.app.services.processor as proc

    class _P:
        def get_positions(self, user_id, **kwargs):
            return response() if callable(response) else response

    monkeypatch.setattr(proc, "processor", lambda: _P())


def _arm():
    return repo.arm_rule(
        USER,
        stock_code=STOCK,
        expiry_display=EXPIRY,
        exchange_code="NFO",
        profit_target_pnl=100000.0,
        loss_limit_pnl=20000.0,
        target_premium_pct=10,
        stop_loss_premium_pct=5,
        legs_snapshot={},
    )


# ------------------------------------------------------------------ warm semantics


def test_warm_populates_registry(db_path, monkeypatch):
    _fake_processor(monkeypatch, _positions_ok())
    assert guard.warm_positions_for_user(USER) is True
    assert engine.group_legs_for_user(USER, STOCK, EXPIRY)


def test_warm_reports_failure_on_broker_error(db_path, monkeypatch):
    _fake_processor(monkeypatch, _positions_error())
    assert guard.warm_positions_for_user(USER) is False


def test_broker_error_does_not_wipe_an_already_warm_registry(db_path, monkeypatch):
    """`sync_positions_from_response` clears the registry for anything it cannot read as
    a position list — including error payloads. Handing it a failed fetch would turn one
    transient broker hiccup into the very inert state this module prevents."""
    _fake_processor(monkeypatch, _positions_ok())
    assert guard.warm_positions_for_user(USER) is True
    before = engine.group_legs_for_user(USER, STOCK, EXPIRY)
    assert before

    _fake_processor(monkeypatch, _positions_error())
    assert guard.warm_positions_for_user(USER) is False
    assert engine.group_legs_for_user(USER, STOCK, EXPIRY) == before


def test_warm_succeeds_with_zero_open_positions(db_path, monkeypatch):
    """Success is judged on the broker response, not the leg count — a user can hold no
    positions for a moment, and calling that 'suspended' would alarm them for nothing."""
    _fake_processor(monkeypatch, {"Status": 200, "Success": {"positions": []}})
    assert guard.warm_positions_for_user(USER) is True


# ------------------------------------------------------------------ the restart case


def test_hydration_warms_positions_so_rules_evaluate_without_a_portfolio_fetch(
    db_path, monkeypatch
):
    """The regression this whole module exists for: after a restart the engine must
    evaluate with no `GET /portfolio/data` having happened."""
    from icici_breeze_backend.app.services import squareoff_dispatcher as dispatcher

    _arm()
    _fake_processor(monkeypatch, _positions_ok())
    monkeypatch.setattr(dispatcher, "processor", lambda: object())
    import icici_breeze_backend.app.services.breeze_websocket_manager as ws
    import icici_breeze_backend.app.services.strategy_group_lifecycle as sg

    monkeypatch.setattr(ws, "ensure_order_feed", lambda *a, **k: True)
    monkeypatch.setattr(sg, "pin_subscription", lambda *a, **k: None)

    assert engine.group_legs_for_user(USER, STOCK, EXPIRY) == []
    dispatcher.hydrate_group_rules_on_startup()
    assert engine.group_legs_for_user(USER, STOCK, EXPIRY), (
        "hydration restored the rule but not its positions — run_pnl_tick would "
        "early-return and the SG would never fire"
    )


# ------------------------------------------------------------------ reminder cadence


def test_first_reminder_is_immediate(db_path, monkeypatch, alerts):
    _arm()
    _fake_processor(monkeypatch, _positions_error())
    guard.protection_guard_tick()
    assert alerts == [("suspended", {"first": True})]
    assert state_repo.get_state(USER) is not None


def test_second_reminder_suppressed_inside_the_interval(db_path, monkeypatch, alerts):
    _arm()
    _fake_processor(monkeypatch, _positions_error())
    guard.protection_guard_tick()
    guard.protection_guard_tick()
    guard.protection_guard_tick()
    assert len(alerts) == 1, "a 60s guard tick must not send a reminder every minute"


def test_reminder_repeats_after_the_interval(db_path, monkeypatch, alerts):
    _arm()
    _fake_processor(monkeypatch, _positions_error())
    guard.protection_guard_tick()
    monkeypatch.setattr(guard, "_reminder_interval_seconds", lambda: 60.0)

    from datetime import timedelta

    real_now = guard.now_ist()
    monkeypatch.setattr(guard, "now_ist", lambda: real_now + timedelta(minutes=5))
    guard.protection_guard_tick()

    assert [k for k, _ in alerts] == ["suspended", "suspended"]
    assert alerts[1][1]["first"] is False, "a repeat must not read as a fresh incident"


def test_suspended_since_is_not_reset_by_later_ticks(db_path, monkeypatch, alerts):
    """Otherwise 'unprotected since' walks forward on every tick and the user can never
    tell how long they have actually been exposed."""
    _arm()
    _fake_processor(monkeypatch, _positions_error())
    guard.protection_guard_tick()
    first = state_repo.get_state(USER)["suspended_since"]
    guard.protection_guard_tick()
    assert state_repo.get_state(USER)["suspended_since"] == first


# ------------------------------------------------------------------ recovery


def test_recovery_clears_suspension_and_notifies_once(db_path, monkeypatch, alerts):
    _arm()
    _fake_processor(monkeypatch, _positions_error())
    guard.protection_guard_tick()
    assert state_repo.get_state(USER) is not None

    _fake_processor(monkeypatch, _positions_ok())
    guard.protection_guard_tick()
    guard.protection_guard_tick()

    assert [k for k, _ in alerts] == ["suspended", "resumed"]
    assert state_repo.get_state(USER) is None


def test_healthy_user_is_never_told_monitoring_resumed(db_path, monkeypatch, alerts):
    _arm()
    _fake_processor(monkeypatch, _positions_ok())
    guard.protection_guard_tick()
    assert alerts == []


# ------------------------------------------------------------------ market-hours gate


def test_tick_is_a_noop_while_the_market_is_closed(db_path, monkeypatch, alerts):
    """One gate does double duty: no overnight broker spend, and no 2am reminder the
    user cannot act on."""
    import icici_breeze_backend.app.services.market_calendar as mc

    monkeypatch.setattr(mc, "is_market_open", lambda *a, **k: False)
    _arm()

    called: list[str] = []
    monkeypatch.setattr(
        guard, "warm_positions_for_user", lambda uid: called.append(uid) or False
    )
    guard.protection_guard_tick()

    assert called == []
    assert alerts == []
