"""Re-centre (#80): with the switch on, a roll that comes due while the tested short is above the
trigger moves both sides to the landing delta around today's spot, wings at the entry width.

Agreed with the user 2026-10-10: off by default; no credit floor; the no-roll window applies;
an unpriced re-centre does nothing (never the one-sided roll); off hashes as absent; every
backtest replays the grid with it off and on."""
from __future__ import annotations

import dataclasses

import pytest
from pydantic import ValidationError

from icici_breeze_backend.app.domain.condor import CondorSettings
from icici_breeze_backend.app.services.condor import backtest_combos
from icici_breeze_backend.app.services.condor.bot import settings_hash
from icici_breeze_backend.app.services.condor.engine import decide
from icici_breeze_backend.app.services.condor.model import CampaignState
from icici_breeze_backend.app.services.condor.orders import net_position, unhedged_rights
from icici_breeze_backend.app.services.condor.pricing import build_greeks_model
from tests.fixtures.condor_chain import CHARGES, EXPIRY, LOT, apply, at, condor, market
from tests.test_condor_backtest import PathSource, _run_to_end, trend_up, whipsaw  # noqa: F401
from tests.test_condor_backtest import _replay as _bt_replay

OFF = CondorSettings(margin_ceiling_inr=60_00_000)
ON = OFF.model_copy(update={"recentre_enabled": True})
# A rally to 25,100 at 25 DTE puts the tested call short near 0.52 delta (an off roll goes to the fly).
DEEP = (25100, 25)
# 24,500 at 30 DTE: a roll is due but the tested call is only near 0.25 delta, under the 0.30 trigger.
SHALLOW = (24500, 30)


@pytest.fixture
def entered():
    m = market(24200, at(45))
    empty = CampaignState(expiry=EXPIRY, lot_size=LOT, legs=(), ledger_cash_inr=0.0, tranches_entered=0)
    d = decide(empty, m, OFF, "sod", CHARGES)
    return condor(m, d.tranche_strikes, lots=2)


def _m(spot_days, hhmm="15:31"):
    spot, days = spot_days
    return market(spot, at(days, hhmm))


def _delta(m, right, strike):
    return build_greeks_model(m.chain, m.spot, EXPIRY, m.now).delta(right, strike)


def _shorts(state, right):
    return {l.strike for l in state.legs if l.is_short and l.right == right}


def test_off_by_default_and_off_keeps_the_one_sided_roll(entered):
    assert CondorSettings(margin_ceiling_inr=1).recentre_enabled is False
    d = decide(entered, _m(DEEP), OFF, "eod", CHARGES)
    assert d.action == "roll_untested"
    assert {o.right for o in d.orders} == {"Put"}


def test_above_the_trigger_both_sides_land_at_the_landing_delta(entered):
    m = _m(DEEP)
    d = decide(entered, m, ON, "eod", CHARGES)
    assert (d.action, d.reason) == ("recentre", "recentre")
    assert d.metrics.tested_side == "Call" and d.metrics.call_short_abs_delta > ON.recentre_tested_delta
    after = apply(entered, d.orders)
    [call], [put] = _shorts(after, "Call"), _shorts(after, "Put")
    assert abs(_delta(m, "Call", call)) == pytest.approx(0.20, abs=0.03)
    assert abs(_delta(m, "Put", put)) == pytest.approx(0.20, abs=0.03)
    assert put < m.spot < call
    # The tested side moved out, away from the money.
    assert call > max(_shorts(entered, "Call"))
    # Wings at the entry width beyond each new short, snapped outward.
    width = m.spot * ON.wing_width_pct / 100
    longs = {l.right: l.strike for l in after.legs if not l.is_short}
    assert width <= longs["Call"] - call < width + 50
    assert width <= put - longs["Put"] < width + 50
    # Quantities kept, every short capped, net delta back near zero.
    for right in ("Call", "Put"):
        assert sum(l.quantity for l in after.legs if l.is_short and l.right == right) == 2 * LOT
    assert not unhedged_rights(net_position(after.legs))
    assert abs(decide(after, m, ON, "eod", CHARGES).metrics.net_delta_per_lot) < ON.net_delta_band_per_lot


def test_wings_are_bought_before_shorts_are_sold(entered):
    d = decide(entered, _m(DEEP), ON, "eod", CHARGES)
    groups = [(o.action, o.opening) for o in d.orders]
    first_sell_open = groups.index(("Sell", True))
    assert all(g != ("Buy", True) for g in groups[first_sell_open:])


def test_below_the_trigger_a_due_roll_stays_one_sided(entered):
    d = decide(entered, _m(SHALLOW), ON, "eod", CHARGES)
    assert d.metrics.call_short_abs_delta < ON.recentre_tested_delta
    assert d.action == "roll_untested"
    assert {o.right for o in d.orders} == {"Put"}


def test_no_roll_due_means_no_recentre_however_deep(entered):
    # Band and leg rule both switched off: nothing is due, so the trigger alone moves nothing.
    idle = ON.model_copy(update={"leg_rule_delta_floor": 0.001, "leg_rule_decay_pct": 100, "net_delta_band_per_lot": 1.0})
    assert decide(entered, _m(DEEP), idle, "eod", CHARGES).action == "no_action"


def test_a_recentre_at_a_debit_still_goes_ahead(entered):
    greedy = ON.model_copy(update={"min_roll_credit_points": 500})
    d = decide(entered, _m(DEEP), greedy, "eod", CHARGES)
    assert d.action == "recentre"
    assert d.roll_credit_points < 0


def test_the_no_roll_window_reports_a_recentre_and_does_nothing(entered):
    blackout = ON.model_copy(update={"no_roll_within_days_of_exit": 5})  # exit 21: blocked below 26 DTE
    d = decide(entered, _m(DEEP), blackout, "eod", CHARGES)
    assert (d.action, d.reason) == ("no_action", "recentre_near_exit")
    assert d.orders and d.roll_credit_points is not None


def test_an_unpriced_recentre_does_nothing_and_never_falls_back(entered):
    m = _m(DEEP)
    target = next(o for o in decide(entered, m, ON, "eod", CHARGES).orders if o.action == "Sell" and o.opening and o.right == "Call")
    # The new short call is listed and priced for delta, but nobody bids for it.
    holed = dataclasses.replace(m, chain=tuple(
        dataclasses.replace(r, call=dataclasses.replace(r.call, bid=0.0)) if r.strike == target.strike else r
        for r in m.chain
    ))
    d = decide(entered, holed, ON, "eod", CHARGES)
    assert d.action == "no_action" and d.reason.startswith("recentre_")
    assert "Nothing is moved" in d.text


def test_landing_must_sit_below_the_trigger_only_when_switched_on():
    with pytest.raises(ValidationError):
        CondorSettings(margin_ceiling_inr=1, recentre_enabled=True, recentre_tested_delta=0.25, recentre_short_delta=0.25)
    CondorSettings(margin_ceiling_inr=1, recentre_enabled=False, recentre_tested_delta=0.25, recentre_short_delta=0.25)


def test_off_hashes_as_absent_so_no_evidence_is_voided():
    off = OFF.model_dump(mode="json")
    before = {k: v for k, v in off.items() if not k.startswith("recentre_")}
    edited_while_off = {**off, "recentre_tested_delta": 0.4, "recentre_short_delta": 0.15}
    assert settings_hash(off, "time_roll", 2) == settings_hash(before, "time_roll", 2)
    assert settings_hash(edited_while_off, "time_roll", 2) == settings_hash(before, "time_roll", 2)
    on = ON.model_dump(mode="json")
    assert settings_hash(on, "time_roll", 2) != settings_hash(before, "time_roll", 2)
    assert settings_hash(on, "time_roll", 2) != settings_hash({**on, "recentre_short_delta": 0.15}, "time_roll", 2)


def test_the_grid_crosses_off_and_on_with_saved_or_default_deltas():
    edited = OFF.model_copy(update={"recentre_tested_delta": 0.4, "recentre_short_delta": 0.1})
    rows = [c for c in backtest_combos.combos_for(edited, "time_roll") if not c.id.startswith("premium-")]
    on = [c for c in rows if c.settings.recentre_enabled]
    # Saved switch off: "on" rows take the defaults, not values edited while it was off.
    assert {(c.settings.recentre_tested_delta, c.settings.recentre_short_delta) for c in on} == {(0.30, 0.20)}
    assert {c.varied["recentre"] for c in rows} == {"off", "0.30Δ to 0.20Δ"}
    [mine] = [c for c in rows if c.is_saved]
    assert not mine.settings.recentre_enabled and "-recentre" not in mine.id

    saved_on = ON.model_copy(update={"recentre_tested_delta": 0.35, "recentre_short_delta": 0.15})
    rows = [c for c in backtest_combos.combos_for(saved_on, "close") if not c.id.startswith("premium-")]
    [mine] = [c for c in rows if c.is_saved]
    assert mine.settings == saved_on and mine.id.endswith("-recentre")
    assert {c.varied["recentre"] for c in rows} == {"off", "0.35Δ to 0.15Δ"}


def test_a_replay_with_recentre_on_counts_its_recentres_and_never_leaves_a_naked_short():
    replay, source = _bt_replay(whipsaw, settings=ON)
    original_fill = replay._fill

    def checked_fill(cycle, orders, **kw):
        out = original_fill(cycle, orders, **kw)
        assert not unhedged_rights({k: int(v[0]) for k, v in cycle.legs.items()})
        return out

    replay._fill = checked_fill
    _run_to_end(replay, source)
    s = replay.summary()
    assert s["complete"]
    assert s["recentres"] >= 1
    assert s["rolls"] >= s["recentres"]
    off, src = _bt_replay(whipsaw, settings=OFF)
    _run_to_end(off, src)
    assert off.summary()["recentres"] == 0
