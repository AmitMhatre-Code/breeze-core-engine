"""Bot-only `target_option_price` on an SG rule (docs/bots-mvp-plan.md section 4).

"Book at 10 paise" is an option PRICE, not a rupee P&L. The two look interchangeable and
are not: the engine derives P&L from the BROKER's `average_price`, so converting a price
target into rupees drifts from the price the caller asked for whenever the broker's average
differs from the intended fill. These tests pin the price semantics.
"""
from __future__ import annotations

import pytest

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.db.squareoff_rules_migrate import ensure_squareoff_rules_table
from icici_breeze_backend.app.domain.squareoff_rule import ArmSquareOffRuleRequest
from icici_breeze_backend.app.repositories import squareoff_rules as repo
from icici_breeze_backend.app.services import portfolio_pnl_engine as engine


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = str(tmp_path / "users_test.sqlite3")
    monkeypatch.setattr(repo, "_db_path", lambda: path)
    ensure_squareoff_rules_table(path)
    return path


# --- persistence ---------------------------------------------------------------------


def test_price_target_round_trips(db_path):
    rule = repo.arm_rule(
        "u1", stock_code="NIFTY", expiry_display="30-Sep-2026", exchange_code="NFO",
        profit_target_pnl=1.0, loss_limit_pnl=9000.0,
        target_premium_pct=5, stop_loss_premium_pct=5, target_option_price=0.10,
    )
    assert repo.get_rule(rule.id).target_option_price == 0.10


def test_manual_arms_leave_it_null(db_path):
    """The manual PB/SL screen must behave exactly as before."""
    rule = repo.arm_rule(
        "u1", stock_code="NIFTY", expiry_display="30-Sep-2026", exchange_code="NFO",
        profit_target_pnl=5000.0, loss_limit_pnl=9000.0,
        target_premium_pct=5, stop_loss_premium_pct=5,
    )
    assert repo.get_rule(rule.id).target_option_price is None


def test_it_is_not_settable_through_the_arm_request_model():
    """Bot-only by construction: there is no field for the manual API to populate."""
    assert "target_option_price" not in ArmSquareOffRuleRequest.model_fields


def test_editing_thresholds_clears_a_stale_price_target(db_path):
    """An edit that omits it means "no price target" — keeping the old one would book at a
    price the caller no longer intends."""
    first = repo.arm_rule(
        "u1", stock_code="NIFTY", expiry_display="30-Sep-2026", exchange_code="NFO",
        profit_target_pnl=1.0, loss_limit_pnl=9000.0,
        target_premium_pct=5, stop_loss_premium_pct=5, target_option_price=0.10,
    )
    again = repo.arm_rule(
        "u1", stock_code="NIFTY", expiry_display="30-Sep-2026", exchange_code="NFO",
        profit_target_pnl=5000.0, loss_limit_pnl=9000.0,
        target_premium_pct=5, stop_loss_premium_pct=5,
    )
    assert again.id == first.id
    assert repo.get_rule(first.id).target_option_price is None


def test_live_rules_carry_it_for_startup_hydration(db_path):
    """A restart must not silently drop the bot's exit."""
    repo.arm_rule(
        "u1", stock_code="NIFTY", expiry_display="30-Sep-2026", exchange_code="NFO",
        profit_target_pnl=1.0, loss_limit_pnl=9000.0,
        target_premium_pct=5, stop_loss_premium_pct=5, target_option_price=0.05,
    )
    assert repo.list_all_live_rules()[0]["target_option_price"] == 0.05


# --- evaluation ----------------------------------------------------------------------


def leg(scrip_key="k1", action=cfg.SELL, right="put"):
    return engine.PositionLeg(
        user_id="u1", stock_code="NIFTY", exchange_code="NFO",
        expiry_display="30-Sep-2026", strike=24000, right=right,
        quantity=75, average_price=120.0, action=action,
    )


def rule(target_option_price):
    return engine.GroupRule(
        rule_id="r1", user_id="u1", stock_code="NIFTY", expiry_display="30-Sep-2026",
        exchange_code="NFO", target_pnl=None, stop_loss_pnl=None,
        target_option_price=target_option_price,
        target_premium_pct=5, stop_loss_premium_pct=5,
    )


def row(scrip_key="k1", ltp=0.10):
    return {"scrip_key": scrip_key, "ltp": ltp, "pnl": 0.0,
            "stock_code": "NIFTY", "expiry_display": "30-Sep-2026"}


def test_fires_when_the_option_reaches_the_target():
    legs = {"k1": leg()}
    assert engine._price_target_reached(rule(0.10), [row(ltp=0.10)], legs) is True


def test_fires_below_the_target_too():
    legs = {"k1": leg()}
    assert engine._price_target_reached(rule(0.10), [row(ltp=0.05)], legs) is True


def test_does_not_fire_above_the_target():
    legs = {"k1": leg()}
    assert engine._price_target_reached(rule(0.10), [row(ltp=0.15)], legs) is False


def test_no_target_never_fires():
    legs = {"k1": leg()}
    assert engine._price_target_reached(rule(None), [row(ltp=0.01)], legs) is False


def test_a_missing_ltp_does_not_fire():
    """No quote is not the same as a cheap quote."""
    legs = {"k1": leg()}
    assert engine._price_target_reached(rule(0.10), [row(ltp=None)], legs) is False


def test_long_legs_are_ignored():
    """The target means "buy this back cheaply" — meaningless for a leg we are long."""
    legs = {"k1": leg(action=cfg.BUY)}
    assert engine._price_target_reached(rule(0.10), [row(ltp=0.01)], legs) is False


def test_every_short_leg_must_reach_the_target():
    """Booking a two-leg group because one side collapsed would leave the other naked."""
    legs = {"k1": leg("k1", right="put"), "k2": leg("k2", right="call")}
    matching = [row("k1", ltp=0.05), row("k2", ltp=8.00)]
    assert engine._price_target_reached(rule(0.10), matching, legs) is False

    both_cheap = [row("k1", ltp=0.05), row("k2", ltp=0.10)]
    assert engine._price_target_reached(rule(0.10), both_cheap, legs) is True


def test_a_long_hedge_does_not_block_the_short_legs():
    legs = {"k1": leg("k1"), "k2": leg("k2", action=cfg.BUY)}
    matching = [row("k1", ltp=0.05), row("k2", ltp=50.0)]
    assert engine._price_target_reached(rule(0.10), matching, legs) is True


def test_a_group_with_no_short_legs_never_fires():
    legs = {"k1": leg(action=cfg.BUY)}
    assert engine._price_target_reached(rule(0.10), [row(ltp=0.01)], legs) is False


def test_set_group_rule_carries_the_price_target():
    engine.set_group_rule(
        "u1", "r1", stock_code="NIFTY", expiry_display="30-Sep-2026",
        target_option_price=0.10,
    )
    try:
        stored = engine._group_rules["u1"][engine._group_key("NIFTY", "30-Sep-2026")]
        assert stored.target_option_price == 0.10
    finally:
        engine.clear_group_rule("u1", "NIFTY", "30-Sep-2026")


def test_price_target_is_independent_of_the_broker_average_price():
    """The whole reason this is not a converted rupee target: the engine's P&L uses the
    broker's average_price, but the price trigger does not consult it at all."""
    cheap_fill = {"k1": leg()}
    object.__setattr__(cheap_fill["k1"], "average_price", 5.0)
    assert engine._price_target_reached(rule(0.10), [row(ltp=0.05)], cheap_fill) is True
