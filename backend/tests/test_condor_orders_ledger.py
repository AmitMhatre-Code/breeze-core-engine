"""Condor order sequencing, the ledger's maths, and the settings model."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from icici_breeze_backend.app.domain.condor import CondorSettings
from icici_breeze_backend.app.services.condor import ledger
from icici_breeze_backend.app.services.condor.model import Leg, OrderLeg
from icici_breeze_backend.app.services.condor.orders import (
    diff_orders,
    net_position,
    safe_sequence,
    unhedged_rights,
)

Q = 65


def _ic(credit_points: float = 150.0):
    legs = [
        Leg(23200, "Put", "Buy", Q, 10),
        Leg(23700, "Put", "Sell", Q, 90),
        Leg(24700, "Call", "Sell", Q, 90),
        Leg(25200, "Call", "Buy", Q, 20),
    ]
    return legs, credit_points * Q


# ---- orders ------------------------------------------------------------------------------


def test_roll_sequence_never_leaves_a_naked_short():
    current = {(23200.0, "Put"): Q, (23700.0, "Put"): -Q}
    target = {(23800.0, "Put"): Q, (24300.0, "Put"): -Q}
    seq = safe_sequence(diff_orders(current, target), spot=24650)
    assert [(o.action, o.opening, o.strike) for o in seq] == [
        ("Buy", True, 23800),   # new wing first
        ("Buy", False, 23700),  # buy back the old short
        ("Sell", True, 24300),  # then the new short
        ("Sell", False, 23200),  # old wing last
    ]
    # Replay the sequence: at every step, puts held long >= puts held short.
    pos = dict(current)
    for o in seq:
        k = (o.strike, o.right)
        pos[k] = pos.get(k, 0) + (o.quantity if o.action == "Buy" else -o.quantity)
        assert not unhedged_rights({k: v for k, v in pos.items() if v})


def test_diff_splits_a_contract_that_crosses_from_long_to_short():
    orders = diff_orders({(24000.0, "Call"): Q}, {(24000.0, "Call"): -Q})
    assert orders == [
        OrderLeg(24000.0, "Call", "Sell", Q, opening=False),
        OrderLeg(24000.0, "Call", "Sell", Q, opening=True),
    ]


def test_nearest_money_first_within_a_group():
    orders = [OrderLeg(25200, "Call", "Buy", Q, True), OrderLeg(23200, "Put", "Buy", Q, True)]
    assert [o.strike for o in safe_sequence(orders, spot=25000)] == [25200, 23200]


def test_net_position_drops_flat_contracts():
    legs = [Leg(24000, "Call", "Buy", Q, 1), Leg(24000, "Call", "Sell", Q, 1), Leg(23000, "Put", "Sell", Q, 1)]
    assert net_position(legs) == {(23000.0, "Put"): -Q}


def test_unhedged_counts_units_per_right():
    assert unhedged_rights({(23700.0, "Put"): -Q, (23200.0, "Put"): Q}) == []
    assert unhedged_rights({(23700.0, "Put"): -2 * Q, (23200.0, "Put"): Q}) == ["Put"]
    # A long call at any strike caps a short call.
    assert unhedged_rights({(24700.0, "Call"): -Q, (24500.0, "Call"): Q}) == []


# ---- ledger ------------------------------------------------------------------------------


def test_breakevens_of_a_plain_condor_are_shorts_plus_minus_credit():
    legs, cash = _ic(150)
    lower, upper = ledger.breakevens(legs, cash, anchor=24200)
    assert lower == pytest.approx(23550)
    assert upper == pytest.approx(24850)


def test_breakevens_include_past_roll_losses():
    legs, cash = _ic(150)
    lower, upper = ledger.breakevens(legs, cash - 50 * Q, anchor=24200)
    assert (lower, upper) == (pytest.approx(23600), pytest.approx(24800))


def test_worst_loss_is_wing_width_minus_credit():
    legs, cash = _ic(150)
    assert ledger.worst_loss_at_expiry(legs, cash) == pytest.approx(-(500 - 150) * Q)


def test_worst_loss_is_unbounded_without_a_wing():
    legs, cash = _ic(150)
    assert ledger.worst_loss_at_expiry([l for l in legs if l.strike != 25200], cash) is None


def test_no_breakeven_when_the_position_cannot_lose():
    legs, _ = _ic()
    lower, upper = ledger.breakevens(legs, 600 * Q, anchor=24200)
    assert (lower, upper) == (None, None)


# ---- settings ----------------------------------------------------------------------------


def test_settings_defaults_and_stop():
    s = CondorSettings(margin_ceiling_inr=60_00_000)
    assert (s.entry_dte, s.tranche_cutoff_dte, s.exit_dte) == (45, 30, 21)
    assert s.max_loss_limit_inr() == pytest.approx(3_00_000)
    assert CondorSettings(margin_ceiling_inr=60_00_000, max_loss_inr=1_00_000).max_loss_limit_inr() == 1_00_000


@pytest.mark.parametrize(
    "kw",
    [
        {"entry_dte": 20, "tranche_cutoff_dte": 30},
        {"exit_dte": 31},
        {"wing_delta": 0.25},
        {"max_loss_pct_of_ceiling": None},
        {"tranche_cutoff_dte": 21, "tranches": 3},
    ],
)
def test_settings_reject_incoherent_values(kw):
    with pytest.raises(ValidationError):
        CondorSettings(margin_ceiling_inr=1_00_000, **kw)


def test_breakevens_are_anchored_on_the_position_not_spot():
    # Spot has run past the upper break-even; both must still be reported as they are.
    legs, cash = _ic(150)
    lower, upper = ledger.breakevens(legs, cash, anchor=ledger.centre(legs, fallback=25500))
    assert (lower, upper) == (pytest.approx(23550), pytest.approx(24850))
    assert ledger.expiry_pnl(legs, cash, 25500) < 0
