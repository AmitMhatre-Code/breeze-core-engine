"""Detecting a group that was closed entirely outside the rule.

Partial foreign fills are already covered: the guard's 60s position refresh feeds
`check_armed_drift`, which diffs the registry against `legs_snapshot` on the normal P&L
tick. The gap is the *empty* case — `_evaluate_rules` skips a group rule with no matching
legs, and `run_pnl_tick` only iterates users present in the position registry, so a user
who closes everything manually drops out of the registry and their SG is never evaluated
again. These pin both halves of that boundary.
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
from icici_breeze_backend.app.services import strategy_group_lifecycle as sg

USER, STOCK, EXPIRY = "VIKRAMMH", "NIFTY", "21-Jul-2026"


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = str(tmp_path / "users_test.sqlite3")
    monkeypatch.setattr(repo, "_db_path", lambda: path)
    monkeypatch.setattr(state_repo, "_db_path", lambda: path)
    ensure_squareoff_rules_table(path)
    ensure_squareoff_protection_table(path)
    return path


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    engine.clear_positions(USER)
    monkeypatch.setattr(sg, "release_subscription", lambda *a, **k: None)
    monkeypatch.setattr(sg, "_release_subscription", lambda *a, **k: None)
    import icici_breeze_backend.app.services.telegram_alerts as tg

    monkeypatch.setattr(tg, "notify_squareoff_reset", lambda *a, **k: None)
    monkeypatch.setattr(tg, "notify_protection_suspended", lambda *a, **k: None)
    monkeypatch.setattr(tg, "notify_protection_resumed", lambda *a, **k: None)
    import icici_breeze_backend.app.services.market_calendar as mc

    monkeypatch.setattr(mc, "is_market_open", lambda *a, **k: True)
    yield
    engine.clear_positions(USER)


def _row(strike=26000, qty=130):
    return {
        "stock_code": STOCK, "exchange_code": "NFO", "expiry_date": EXPIRY,
        "strike_price": str(strike), "right": "call", "quantity": str(qty),
        "average_price": "100", "action": "sell",
    }


def _sync(rows):
    engine.sync_positions_from_response(USER, {"Status": 200, "Success": {"positions": rows}})


def _fake_processor(monkeypatch, rows):
    import icici_breeze_backend.app.services.processor as proc

    class _P:
        def get_positions(self, user_id, **kwargs):
            return {"Status": 200, "Success": {"positions": list(rows)}}

    monkeypatch.setattr(proc, "processor", lambda: _P())


def _arm_with_open_leg():
    """Mirrors the arm route: the DB row AND the in-memory group rule. `_evaluate_rules`
    iterates the in-memory registry, so a DB-only rule is invisible to the drift path."""
    _sync([_row()])
    legs = engine.group_legs_for_user(USER, STOCK, EXPIRY)
    assert legs
    record = repo.arm_rule(
        USER, stock_code=STOCK, expiry_display=EXPIRY, exchange_code="NFO",
        profit_target_pnl=1e9, loss_limit_pnl=1e9,
        target_premium_pct=10, stop_loss_premium_pct=5,
        legs_snapshot={legs[0].scrip_key: 130},
    )
    engine.set_group_rule(
        USER, record.id, stock_code=STOCK, expiry_display=EXPIRY,
        exchange_code="NFO", target_pnl=1e9, stop_loss_pnl=1e9,
    )
    return record


# ------------------------------------------------------------------ the gap being closed


def test_fully_closed_group_is_reset(db_path):
    rule = _arm_with_open_leg()
    _sync([])  # user closed everything from the ICICI app
    assert guard.reconcile_fully_closed_groups(USER) == 1
    assert repo.get_rule(rule.id).status == "reset"


def test_reset_reason_names_what_happened(db_path):
    rule = _arm_with_open_leg()
    _sync([])
    guard.reconcile_fully_closed_groups(USER)
    reason = (repo.get_rule(rule.id).reset_reason or "").lower()
    assert "closed outside this rule" in reason


def test_guard_tick_performs_the_reconcile(db_path, monkeypatch):
    rule = _arm_with_open_leg()
    _fake_processor(monkeypatch, [])  # broker confirms: no open positions
    guard.protection_guard_tick()
    assert repo.get_rule(rule.id).status == "reset"


# ------------------------------------------------------------------ what must NOT reset


def test_open_group_is_left_alone(db_path):
    rule = _arm_with_open_leg()
    assert guard.reconcile_fully_closed_groups(USER) == 0
    assert repo.get_rule(rule.id).status == "armed"


def test_failed_warm_never_resets(db_path, monkeypatch):
    """'No legs' and 'we could not read your positions' look identical from here. Acting
    on the second would tear down live protection over a broker hiccup."""
    rule = _arm_with_open_leg()
    engine.clear_positions(USER)  # registry cold, as after a failed fetch

    import icici_breeze_backend.app.services.processor as proc

    class _Failing:
        def get_positions(self, user_id, **kwargs):
            return {"Status": 400, "Error": "Unable to connect to broker."}

    monkeypatch.setattr(proc, "processor", lambda: _Failing())
    guard.protection_guard_tick()
    assert repo.get_rule(rule.id).status == "armed"


def test_triggered_rule_is_not_reset(db_path):
    """Mid-placement: its own exit orders are not recorded against it yet, so an empty
    group here would race the dispatcher into cancelling its own fire."""
    rule = _arm_with_open_leg()
    repo.mark_triggered(rule.id)
    _sync([])
    assert guard.reconcile_fully_closed_groups(USER) == 0
    assert repo.get_rule(rule.id).status == "triggered"


def test_fired_rule_is_not_reset(db_path):
    """A fired SG's own exits legitimately empty the group — that is Completed, and
    `reconcile_fired_rules_for_user` owns it."""
    rule = _arm_with_open_leg()
    repo.mark_fired(rule.id, [])
    _sync([])
    assert guard.reconcile_fully_closed_groups(USER) == 0
    assert repo.get_rule(rule.id).status == "fired"


def test_rule_without_an_arm_snapshot_is_left_alone(db_path):
    _sync([_row()])
    rule = repo.arm_rule(
        USER, stock_code=STOCK, expiry_display=EXPIRY, exchange_code="NFO",
        profit_target_pnl=1e9, loss_limit_pnl=1e9,
        target_premium_pct=10, stop_loss_premium_pct=5, legs_snapshot={},
    )
    _sync([])
    assert guard.reconcile_fully_closed_groups(USER) == 0
    assert repo.get_rule(rule.id).status == "armed"


def test_partial_foreign_fill_is_caught_by_drift_not_by_this(db_path):
    """The other half of the boundary, pinned deliberately.

    This passes only because the guard now refreshes positions on a timer — before that,
    the registry was warmed solely by a Portfolio page load and `check_armed_drift` was
    comparing against whatever happened to be in memory. If that refresh is ever removed,
    this test fails and says why, instead of the fallback silently going dead again.
    """
    rule = _arm_with_open_leg()
    _sync([_row(qty=65)])  # quantity halved outside the rule
    engine.run_pnl_tick()
    assert repo.get_rule(rule.id).status == "reset"
    assert guard.reconcile_fully_closed_groups(USER) == 0, (
        "the drift path already handled it; this must not double-reset"
    )


def test_other_groups_are_untouched(db_path):
    """Only the emptied group resets; an unrelated live SG must survive."""
    _sync([_row(strike=26000), {**_row(strike=25000), "stock_code": "BANKNIFTY"}])
    legs = engine.group_legs_for_user(USER, STOCK, EXPIRY)
    nifty = repo.arm_rule(
        USER, stock_code=STOCK, expiry_display=EXPIRY, exchange_code="NFO",
        profit_target_pnl=1e9, loss_limit_pnl=1e9,
        target_premium_pct=10, stop_loss_premium_pct=5,
        legs_snapshot={legs[0].scrip_key: 130},
    )
    bank_legs = engine.group_legs_for_user(USER, "BANKNIFTY", EXPIRY)
    bank = repo.arm_rule(
        USER, stock_code="BANKNIFTY", expiry_display=EXPIRY, exchange_code="NFO",
        profit_target_pnl=1e9, loss_limit_pnl=1e9,
        target_premium_pct=10, stop_loss_premium_pct=5,
        legs_snapshot={bank_legs[0].scrip_key: 130},
    )

    _sync([{**_row(strike=25000), "stock_code": "BANKNIFTY"}])  # only NIFTY closed
    assert guard.reconcile_fully_closed_groups(USER) == 1
    assert repo.get_rule(nifty.id).status == "reset"
    assert repo.get_rule(bank.id).status == "armed"
