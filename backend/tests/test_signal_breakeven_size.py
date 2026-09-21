"""The bar a call has to clear: priced on the size actually traded, spread included.

Two errors used to run in opposite directions and were both invisible on the page. The bar was
priced on ONE lot, though roughly three-quarters of a one-lot round trip is flat brokerage that
amortises away with size -- so the bar was far too high for a real position. And it left out the
bid-ask spread entirely, which does not amortise -- so it was too low at exactly the sizes where
brokerage stops mattering.
"""
from __future__ import annotations

import pytest

from icici_breeze_backend.app.services.index_signal import breakeven as be
from icici_breeze_backend.app.services.index_signal import settings as signal_settings

LEVEL = 23_448.0


@pytest.fixture(autouse=True)
def _priced(monkeypatch):
    monkeypatch.setattr(be, "lot_size", lambda label, today=None: 65)
    monkeypatch.setattr(be, "atm_premium", lambda label, today=None: 90.3)


def test_the_bar_falls_steeply_with_size():
    one = be.breakeven("nifty", LEVEL, lots=1)
    ten = be.breakeven("nifty", LEVEL, lots=10)
    assert one["bps"] > ten["bps"]
    # The flat part of the round trip is most of a one-lot bar, so ten lots is a large drop --
    # large enough that scoring at one lot fails signals a real position would clear.
    assert ten["bps"] < one["bps"] * 0.6
    assert ten["quantity"] == 650 and ten["lots"] == 10


def test_the_charges_half_amortises_and_the_spread_half_does_not():
    one = be.breakeven("nifty", LEVEL, lots=1)
    ten = be.breakeven("nifty", LEVEL, lots=10)
    assert ten["charges_bps"] < one["charges_bps"] * 0.6      # brokerage spread over more lots
    assert ten["spread_bps"] == pytest.approx(one["spread_bps"], rel=1e-6)   # per unit, always


def test_the_two_halves_add_up_to_the_bar():
    """If these ever drift apart, the page is showing a breakdown of a different number."""
    for lots in (1, 5, 20):
        b = be.breakeven("nifty", LEVEL, lots=lots)
        assert b["charges_bps"] + b["spread_bps"] == pytest.approx(b["bps"], abs=0.002)


def test_the_spread_is_in_the_bar_and_says_where_it_came_from():
    b = be.breakeven("nifty", LEVEL, lots=1)
    assert b["spread_rupees"] > 0
    assert b["cost_rupees"] > b["charges_rupees"]
    # An uncalibrated default must never be mistaken for an observed spread.
    assert b["spread_source"] in ("observed", "default", "unavailable")


def test_an_unsized_caller_gets_the_conservative_bar():
    assert be.breakeven("nifty", LEVEL)["lots"] == 1
    assert be.breakeven("nifty", LEVEL, lots=0)["lots"] == 1


def test_the_size_setting_round_trips_and_is_bounded(tmp_path):
    db = str(tmp_path / "users.sqlite3")
    signal_settings.reset_cache_for_tests()
    assert signal_settings.cost_lots(db_path=db) == 1
    assert signal_settings.set_cost_lots(10, db_path=db) == 10
    signal_settings.reset_cache_for_tests()
    assert signal_settings.cost_lots(db_path=db) == 10
    for bad in (0, -1, signal_settings.MAX_COST_LOTS + 1, "ten"):
        with pytest.raises(ValueError):
            signal_settings.set_cost_lots(bad, db_path=db)
    signal_settings.reset_cache_for_tests()


def test_the_size_setting_does_not_disturb_the_navbar_choice(tmp_path):
    """They share one row; writing one must not blank the other."""
    db = str(tmp_path / "users.sqlite3")
    signal_settings.reset_cache_for_tests()
    signal_settings.set_navbar_mechanism("momentum", db_path=db)
    signal_settings.set_cost_lots(5, db_path=db)
    signal_settings.reset_cache_for_tests()
    assert signal_settings.navbar_mechanism(db_path=db) == "momentum"
    assert signal_settings.cost_lots(db_path=db) == 5
    signal_settings.reset_cache_for_tests()
