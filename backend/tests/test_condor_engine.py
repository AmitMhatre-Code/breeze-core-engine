"""The Dynamic Iron Condor rule engine: priority order, rolls, cap, tranches.

Scenarios follow the source conversation's NIFTY campaign (entry near 24,200 at 45 DTE, a
rally through the short call) on synthetic Black-Scholes chains, so each decision can be
checked against the rule that should produce it (docs/dynamic-iron-condor-plan.md section 3).
"""
from __future__ import annotations

import pytest

from icici_breeze_backend.app.domain.condor import CondorSettings
from icici_breeze_backend.app.services.condor.engine import decide, entry_orders
from icici_breeze_backend.app.services.condor.model import CampaignState, Leg
from icici_breeze_backend.app.services.condor.orders import net_position, unhedged_rights
from icici_breeze_backend.app.services.condor.pricing import build_greeks_model
from tests.fixtures.condor_chain import CHARGES, EXPIRY, LOT, apply, at, condor, market

SETTINGS = CondorSettings(margin_ceiling_inr=60_00_000)


def _empty(tranches_entered: int = 0) -> CampaignState:
    return CampaignState(expiry=EXPIRY, lot_size=LOT, legs=(), ledger_cash_inr=0.0, tranches_entered=tranches_entered)


@pytest.fixture
def entered():
    m = market(24200, at(45))
    d = decide(_empty(), m, SETTINGS, "sod", CHARGES)
    assert d.action == "enter_tranche"
    return condor(m, d.tranche_strikes)


def _delta(m, right, strike):
    return build_greeks_model(m.chain, m.spot, EXPIRY, m.now).delta(right, strike)


# ---- entry -------------------------------------------------------------------------------


def test_entry_picks_shorts_and_wings_by_delta():
    m = market(24200, at(45))
    d = decide(_empty(), m, SETTINGS, "sod", CHARGES)
    s = d.tranche_strikes
    assert abs(_delta(m, "Call", s["short_call"])) == pytest.approx(0.20, abs=0.02)
    assert abs(_delta(m, "Put", s["short_put"])) == pytest.approx(0.20, abs=0.02)
    # Wings snap outward: never more delta than configured.
    assert abs(_delta(m, "Call", s["long_call"])) <= 0.05
    assert abs(_delta(m, "Put", s["long_put"])) <= 0.05
    assert s["long_put"] < s["short_put"] < 24200 < s["short_call"] < s["long_call"]


def test_entry_orders_buy_wings_before_selling():
    m = market(24200, at(45))
    s = decide(_empty(), m, SETTINGS, "sod", CHARGES).tranche_strikes
    orders = entry_orders(s, 2 * LOT, m)
    assert [o.action for o in orders] == ["Buy", "Buy", "Sell", "Sell"]
    assert all(o.price and o.quantity == 2 * LOT for o in orders)


def test_tranche_waits_for_the_entry_check():
    m = market(24200, at(45, "15:31"))
    assert decide(_empty(), m, SETTINGS, "eod", CHARGES).action == "no_action"
    eod = SETTINGS.model_copy(update={"entry_check": "eod"})
    assert decide(_empty(), m, eod, "eod", CHARGES).action == "enter_tranche"


def test_tranches_are_spaced_between_entry_and_cutoff():
    # Three tranches over 45 -> 30 DTE fall due at 45, 40 and 35 -- never on the cut-off itself.
    assert decide(_empty(1), market(24200, at(41)), SETTINGS, "sod", CHARGES).action == "no_action"
    assert decide(_empty(1), market(24200, at(40)), SETTINGS, "sod", CHARGES).action == "enter_tranche"
    assert decide(_empty(2), market(24200, at(35)), SETTINGS, "sod", CHARGES).action == "enter_tranche"
    # A late tranche still goes in up to the cut-off, never after it.
    assert decide(_empty(2), market(24200, at(30)), SETTINGS, "sod", CHARGES).action == "enter_tranche"
    assert decide(_empty(3), market(24200, at(30)), SETTINGS, "sod", CHARGES).action == "no_action"
    assert decide(_empty(2), market(24200, at(29)), SETTINGS, "sod", CHARGES).action == "no_action"


def test_no_tranche_before_entry_dte():
    assert decide(_empty(), market(24200, at(50)), SETTINGS, "sod", CHARGES).action == "no_action"


# ---- P0 ----------------------------------------------------------------------------------


def test_stand_in_spot_is_never_decided_on(entered):
    d = decide(entered, market(24200, at(40), spot_live=False), SETTINGS, "eod", CHARGES)
    assert (d.action, d.reason) == ("unavailable", "spot_not_live")


def test_feed_down_is_unavailable_not_nothing_to_do(entered):
    d = decide(entered, market(24200, at(40), feeds_ok=False), SETTINGS, "eod", CHARGES)
    assert (d.action, d.reason) == ("unavailable", "feed_down")


def test_short_without_an_ask_is_unavailable(entered):
    short_call = next(l for l in entered.legs if l.is_short and l.right == "Call")
    m = market(24200, at(40), drop=frozenset({(int(short_call.strike), "Call")}))
    d = decide(entered, m, SETTINGS, "eod", CHARGES)
    assert (d.action, d.reason) == ("unavailable", "leg_unpriced")


# ---- P1 / P2 -----------------------------------------------------------------------------


def test_max_loss_closes_everything_shorts_first(entered):
    tight = SETTINGS.model_copy(update={"margin_ceiling_inr": 1_00_000})  # 5% -> Rs 5,000
    d = decide(entered, market(25100, at(25, "15:31")), tight, "eod", CHARGES)
    assert (d.action, d.reason) == ("close_all", "max_loss")
    assert d.metrics.campaign_pnl_inr <= -5_000
    assert [o.action for o in d.orders] == ["Buy", "Buy", "Sell", "Sell"]
    assert not any(o.opening for o in d.orders)


def test_max_loss_outranks_a_due_roll(entered):
    tight = SETTINGS.model_copy(update={"margin_ceiling_inr": 1_00_000})
    assert decide(entered, market(25100, at(25, "15:31")), SETTINGS, "eod", CHARGES).action == "roll_untested"
    assert decide(entered, market(25100, at(25, "15:31")), tight, "eod", CHARGES).action == "close_all"


def test_exit_dte_offers_exit_or_time_roll(entered):
    d = decide(entered, market(24200, at(21)), SETTINGS, "sod", CHARGES)
    assert (d.action, d.reason) == ("exit_or_roll", "exit_dte")
    assert len(d.orders) == 4 and not any(o.opening for o in d.orders)


# ---- P4: rolls ---------------------------------------------------------------------------


def test_quiet_market_does_nothing(entered):
    d = decide(entered, market(24200, at(44, "15:31")), SETTINGS, "eod", CHARGES)
    assert (d.action, d.reason) == ("no_action", "nothing_due")


def test_rally_rolls_the_put_up_to_the_calls_delta(entered):
    m = market(24650, at(27, "15:31"))
    d = decide(entered, m, SETTINGS, "eod", CHARGES)
    assert (d.action, d.reason) == ("roll_untested", "leg_rule")
    assert d.metrics.tested_side == "Call"
    new_short = next(o for o in d.orders if o.action == "Sell" and o.opening)
    assert new_short.right == "Put"
    assert abs(_delta(m, "Put", new_short.strike)) == pytest.approx(d.metrics.call_short_abs_delta, abs=0.03)
    assert d.roll_credit_points > SETTINGS.min_roll_credit_points


def test_a_roll_never_touches_the_tested_side(entered):
    d = decide(entered, market(24650, at(27, "15:31")), SETTINGS, "eod", CHARGES)
    assert {o.right for o in d.orders} == {"Put"}


def test_rolled_wing_keeps_the_tested_sides_width(entered):
    d = decide(entered, market(24650, at(27, "15:31")), SETTINGS, "eod", CHARGES)
    call_short = next(l.strike for l in entered.legs if l.is_short and l.right == "Call")
    call_wing = next(l.strike for l in entered.legs if not l.is_short and l.right == "Call")
    new_short = next(o.strike for o in d.orders if o.action == "Sell" and o.opening)
    new_wing = next(o.strike for o in d.orders if o.action == "Buy" and o.opening)
    assert new_short - new_wing >= call_wing - call_short


def test_roll_is_capped_at_the_tested_strike(entered):
    d = decide(entered, market(25100, at(25, "15:31")), SETTINGS, "eod", CHARGES)
    call_short = next(l.strike for l in entered.legs if l.is_short and l.right == "Call")
    new_short = next(o for o in d.orders if o.action == "Sell" and o.opening)
    assert new_short.strike == call_short  # an iron fly, never an inversion


def test_after_the_cap_no_further_roll(entered):
    m = market(25100, at(25, "15:31"))
    fly = apply(entered, decide(entered, m, SETTINGS, "eod", CHARGES).orders)
    d = decide(fly, market(25300, at(24)), SETTINGS, "sod", CHARGES)
    assert d.metrics.at_cap
    assert d.action == "no_action"


def test_beyond_breakeven_at_the_cap_exits_at_end_of_day_only(entered):
    fly = apply(entered, decide(entered, market(25100, at(25, "15:31")), SETTINGS, "eod", CHARGES).orders)
    upper = decide(fly, market(25150, at(24)), SETTINGS, "sod", CHARGES).metrics.upper_breakeven
    beyond = upper + 100
    assert decide(fly, market(beyond, at(23)), SETTINGS, "sod", CHARGES).action == "no_action"
    d = decide(fly, market(beyond, at(23, "15:31")), SETTINGS, "eod", CHARGES)
    assert (d.action, d.reason) == ("exit_or_roll", "beyond_breakeven")


def test_a_roll_below_the_minimum_credit_is_skipped_but_shown(entered):
    greedy = SETTINGS.model_copy(update={"min_roll_credit_points": 500})
    d = decide(entered, market(24650, at(27, "15:31")), greedy, "eod", CHARGES)
    assert (d.action, d.reason) == ("no_action", "roll_credit_below_min")
    assert d.orders and d.roll_credit_points < 500


def test_block_rule_fires_without_the_leg_rule(entered):
    only_block = SETTINGS.model_copy(update={"leg_rule_delta_floor": 0.001, "leg_rule_decay_pct": 100})
    d = decide(entered, market(24650, at(27, "15:31")), only_block, "eod", CHARGES)
    assert (d.action, d.reason) == ("roll_untested", "block_rule")
    assert abs(d.metrics.net_delta_per_lot) > only_block.net_delta_band_per_lot


def test_a_fall_rolls_the_call_down(entered):
    d = decide(entered, market(23500, at(38, "15:31")), SETTINGS, "eod", CHARGES)
    assert d.action == "roll_untested"
    assert d.metrics.tested_side == "Put"
    assert {o.right for o in d.orders} == {"Call"}


def test_a_side_left_without_its_short_is_resold(entered):
    # A roll whose sell step failed: the put wing is there, the put short is not.
    broken = CampaignState(
        EXPIRY, LOT, tuple(l for l in entered.legs if not (l.is_short and l.right == "Put")),
        entered.ledger_cash_inr, 1,
    )
    d = decide(broken, market(24200, at(40, "15:31")), SETTINGS, "eod", CHARGES)
    assert d.action == "roll_untested"
    assert any(o.action == "Sell" and o.opening and o.right == "Put" for o in d.orders)


def test_every_decision_keeps_every_short_capped(entered):
    """Whatever the engine proposes, the position after it has a wing for every short."""
    state = entered
    for spot, days in [(24650, 27), (25100, 25), (24400, 24), (23600, 23)]:
        d = decide(state, market(spot, at(days, "15:31")), SETTINGS, "eod", CHARGES)
        if d.action == "roll_untested":
            state = apply(state, d.orders)
        assert not unhedged_rights(net_position(state.legs))


def test_on_demand_evaluates_but_reports_the_same_rules(entered):
    m = market(24650, at(27, "13:00"))
    assert decide(entered, m, SETTINGS, "on_demand", CHARGES).action == "roll_untested"


def test_ledger_loss_moves_the_breakevens_inward(entered):
    m = market(24200, at(40))
    base = decide(entered, m, SETTINGS, "sod", CHARGES).metrics
    poorer = CampaignState(EXPIRY, LOT, entered.legs, entered.ledger_cash_inr - 50 * LOT, 1)
    worse = decide(poorer, m, SETTINGS, "sod", CHARGES).metrics
    assert worse.lower_breakeven == pytest.approx(base.lower_breakeven + 50)
    assert worse.upper_breakeven == pytest.approx(base.upper_breakeven - 50)
    assert worse.campaign_pnl_inr == pytest.approx(base.campaign_pnl_inr - 50 * LOT)


def test_leg_side_and_quantity_feed_net_delta():
    leg = Leg(24000, "Call", "Sell", LOT, 100)
    assert leg.signed_qty == -LOT and leg.is_short


def test_a_roll_near_the_exit_is_reported_not_done_when_switched_on(entered):
    """Flat market, both shorts decayed, a day before the 21-DTE exit (found in the replay)."""
    late = market(24200, at(22, "15:31"))
    # Minimum credit off, so the near-exit window is the only thing that can stop the roll.
    rolling = SETTINGS.model_copy(update={"min_roll_credit_points": 0})
    assert decide(entered, late, rolling, "eod", CHARGES).action == "roll_untested"
    blackout = rolling.model_copy(update={"no_roll_within_days_of_exit": 3})
    d = decide(entered, late, blackout, "eod", CHARGES)
    assert (d.action, d.reason) == ("no_action", "roll_near_exit")
    assert d.orders and d.roll_credit_points is not None
    # Outside the window the same rule still rolls.
    assert decide(entered, market(24650, at(27, "15:31")), blackout, "eod", CHARGES).action == "roll_untested"
