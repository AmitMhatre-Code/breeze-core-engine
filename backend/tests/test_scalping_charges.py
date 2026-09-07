"""The round-trip cost model (services.bots.scalping.charges, .paper).

Friction is the binding constraint for this strategy (plan section 6.4), so these numbers
matter more than they look. The rates here are CALIBRATED against a real ICICI contract note
(140 F&O fills, 2026-08-03 to 2026-09-03) rather than taken from published summaries, and
`test_model_reproduces_the_real_contract_note` replays actual rows from it. If a statutory
rate changes, that test fails with the row that disagrees.
"""
from __future__ import annotations

import pytest

from icici_breeze_backend.app.db.bots_migrate import ensure_bots_tables
from icici_breeze_backend.app.services.bots import charges as charges_mod
from icici_breeze_backend.app.services.bots.charges import ChargesModel, load_charges
from icici_breeze_backend.app.services.bots.scalping.paper import (
    round_trip_pnl,
    simulate_buy,
    simulate_sell,
)

C = ChargesModel()
QTY = 75


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = str(tmp_path / "users_test.sqlite3")
    monkeypatch.setattr(charges_mod, "_db_path", lambda: path)
    ensure_bots_tables(path)
    return path


# --- charge arithmetic ----------------------------------------------------------------


def test_stt_is_charged_on_the_sell_leg_only():
    buy = C.breakdown(110.0, QTY, is_buy=True)
    sell = C.breakdown(110.0, QTY, is_buy=False)
    assert buy["stt"] == 0.0
    assert sell["stt"] > 0.0


def test_stamp_duty_is_charged_on_the_buy_leg_only():
    assert C.breakdown(110.0, QTY, is_buy=True)["stamp_duty"] > 0.0
    assert C.breakdown(110.0, QTY, is_buy=False)["stamp_duty"] == 0.0


def test_gst_excludes_stt_and_stamp_duty():
    """They are taxes, not services, and are not themselves taxed."""
    b = C.breakdown(110.0, QTY, is_buy=False)
    expected = (b["brokerage"] + b["exchange_txn"] + b["sebi"] + b["ipft"]) * 0.18
    assert b["gst"] == pytest.approx(expected, abs=0.01)


def test_flat_brokerage_does_not_scale_with_premium():
    assert C.brokerage_for(1_000.0) == pytest.approx(20.0)
    assert C.brokerage_for(500_000.0) == pytest.approx(20.0)


def test_percentage_brokerage_can_be_capped():
    model = ChargesModel(brokerage_per_order_inr=0.0, brokerage_pct_of_premium=2.5, brokerage_cap_inr=20.0)
    assert model.brokerage_for(100.0) == pytest.approx(2.5)      # under the cap
    assert model.brokerage_for(100_000.0) == pytest.approx(20.0)  # capped


def test_round_trip_is_the_sum_of_both_legs():
    total = C.round_trip(110.0, 120.0, QTY)
    assert total == pytest.approx(
        C.leg_charges(110.0, QTY, is_buy=True) + C.leg_charges(120.0, QTY, is_buy=False)
    )


def test_round_trip_on_one_lot_is_the_order_of_magnitude_the_plan_assumes():
    """Section 6.4's whole argument rests on this being tens of rupees, not single digits."""
    assert 40.0 < C.round_trip(110.0, 120.0, QTY) < 150.0


def test_breakdown_totals_match_leg_charges():
    """The itemisation exists so a disagreement with a contract note is findable."""
    for is_buy in (True, False):
        b = C.breakdown(97.35, QTY, is_buy=is_buy)
        assert b["total"] == pytest.approx(C.leg_charges(97.35, QTY, is_buy=is_buy), abs=0.01)


# --- persistence ----------------------------------------------------------------------


def test_defaults_are_seeded_and_readable(db_path):
    model = load_charges()
    assert model.brokerage_per_order_inr == 20.0
    assert model.slippage_spread_fraction == 0.5


def test_rates_can_be_changed_without_a_code_edit(db_path):
    """The reason this is a settings row: STT went 0.0625% -> 0.1% by regulation."""
    charges_mod.save_charges(stt_sell_pct=0.125)
    assert load_charges().stt_sell_pct == pytest.approx(0.125)


def test_unknown_charge_fields_are_rejected(db_path):
    with pytest.raises(ValueError):
        charges_mod.save_charges(mystery_levy=1.0)


def test_an_unreadable_settings_row_falls_back_to_defaults(monkeypatch):
    """A missing row must not stop a bot mid-session."""
    monkeypatch.setattr(charges_mod, "_db_path", lambda: "/nonexistent/dir/users.sqlite3")
    assert load_charges().brokerage_per_order_inr == 20.0


# --- paper fills ----------------------------------------------------------------------


def test_a_buy_fills_at_the_ask_worsened_by_a_share_of_the_spread():
    fill = simulate_buy(bid=100.0, ask=101.0, quantity=QTY, charges=C)
    assert fill.touch_price == 101.0
    assert fill.price == pytest.approx(101.5)  # ask + 0.5 x 1.00 spread
    assert fill.slippage_per_unit == pytest.approx(0.5)


def test_a_sell_fills_at_the_bid_worsened_by_a_share_of_the_spread():
    fill = simulate_sell(bid=100.0, ask=101.0, quantity=QTY, charges=C)
    assert fill.touch_price == 100.0
    assert fill.price == pytest.approx(99.5)


def test_slippage_is_adverse_on_both_legs():
    """A round trip pays the spread share twice; it never helps."""
    buy = simulate_buy(100.0, 101.0, QTY, C)
    sell = simulate_sell(100.0, 101.0, QTY, C)
    assert buy.price > 101.0 and sell.price < 100.0


def test_slippage_can_be_switched_off():
    model = ChargesModel(slippage_spread_fraction=0.0)
    assert simulate_buy(100.0, 101.0, QTY, model).price == pytest.approx(101.0)


def test_a_sell_never_prices_at_or_below_zero():
    """A negative fill would invent profit on the exit of a losing trade."""
    fill = simulate_sell(bid=0.05, ask=5.0, quantity=QTY, charges=C)
    assert fill.price >= 0.05


def test_an_unpriceable_leg_returns_none_rather_than_guessing():
    assert simulate_buy(None, None, QTY, C) is None
    assert simulate_buy(100.0, 0.0, QTY, C) is None
    assert simulate_sell(0.0, 101.0, QTY, C) is None
    assert simulate_buy(100.0, 101.0, 0, C) is None


def test_round_trip_pnl_counts_slippage_once_via_the_fill_prices():
    """Slippage is already inside the prices; adding it to friction would double-count it."""
    entry = simulate_buy(100.0, 101.0, QTY, C)
    exit_ = simulate_sell(110.0, 111.0, QTY, C)
    gross, friction, net = round_trip_pnl(entry, exit_)
    # round_trip_pnl reports in paise, so compare at that resolution.
    assert gross == pytest.approx((exit_.price - entry.price) * QTY, abs=0.01)
    assert friction == pytest.approx(entry.charges + exit_.charges, abs=0.01)
    assert net == pytest.approx(gross - friction, abs=0.01)


def test_a_small_favourable_move_is_still_a_net_loss():
    """The friction argument, made concrete: +1 point on a lot does not pay for the trip."""
    entry = simulate_buy(100.0, 100.1, QTY, C)
    exit_ = simulate_sell(101.0, 101.1, QTY, C)
    _, _, net = round_trip_pnl(entry, exit_)
    assert net < 0


# --- calibration against a real contract note -----------------------------------------

# Verbatim rows from an ICICI F&O contract note, chosen to span both exchanges, both sides,
# and three orders of magnitude of turnover. Columns:
#   (exchange, action, qty, price, value, stt, txn, stamp, sebi, brokerage, gst, total)
_CONTRACT_NOTE = [
    ("BSE", "Sell", 40, 310.0, 12400.0, 18.60, 4.03, 0.0, 0.01, 20.0, 4.32, 46.96),
    ("NSE", "Sell", 1755, 1.35, 2369.25, 3.55, 0.84, 0.0, 0.0, 20.0, 3.74, 28.13),
    ("NSE", "Sell", 1755, 2.25, 3948.75, 5.93, 1.41, 0.0, 0.0, 20.0, 3.86, 31.20),
    ("NSE", "Buy", 130, 20.0, 2600.0, 0.0, 0.92, 0.0, 0.0, 20.0, 3.76, 24.68),
    ("BSE", "Sell", 1000, 6.5, 6500.0, 9.75, 2.11, 0.0, 0.01, 20.0, 3.98, 35.85),
    ("BSE", "Sell", 1000, 2.4, 2400.0, 3.60, 0.78, 0.0, 0.0, 20.0, 3.74, 28.12),
    ("BSE", "Sell", 180, 4.25, 765.0, 1.15, 0.25, 0.0, 0.0, 20.0, 3.64, 25.04),
    ("BSE", "Buy", 40, 140.0, 5600.0, 0.0, 1.82, 0.17, 0.01, 20.0, 3.92, 25.92),
]


@pytest.mark.parametrize("row", _CONTRACT_NOTE, ids=lambda r: f"{r[0]}-{r[1]}-{r[4]:.0f}")
def test_model_reproduces_the_real_contract_note(row):
    """The strongest check available: predict what the broker actually billed.

    Tolerance is a rupee because the broker rounds each component to the paisa and stamp
    duty is levied on a day's aggregate buy value rather than per order -- so a single fill's
    stamp line can be zero where the model expects a few paise. Everything else has to land.
    """
    exchange, action, qty, price, value, stt, txn, stamp, sebi, brok, gst, total = row
    predicted = C.leg_charges(price, qty, is_buy=(action == "Buy"), exchange_code=exchange)
    assert predicted == pytest.approx(total, abs=1.0), (
        f"{exchange} {action} {qty}@{price}: predicted {predicted:.2f} vs billed {total:.2f}"
    )


@pytest.mark.parametrize("row", _CONTRACT_NOTE, ids=lambda r: f"{r[0]}-{r[1]}-{r[4]:.0f}")
def test_each_component_matches_the_note(row):
    """Component-level, so a compensating pair of errors cannot pass as a correct total."""
    exchange, action, qty, price, value, stt, txn, stamp, sebi, brok, gst, total = row
    b = C.breakdown(price, qty, is_buy=(action == "Buy"), exchange_code=exchange)
    assert b["turnover"] == pytest.approx(value, abs=1.0)
    assert b["brokerage"] == pytest.approx(brok, abs=0.01)
    assert b["stt"] == pytest.approx(stt, abs=0.35)
    assert b["exchange_txn"] == pytest.approx(txn, abs=0.05)
    assert b["gst"] == pytest.approx(gst, abs=0.05)


def test_stt_is_fifteen_basis_points_on_the_sell_side():
    """0.15%, not the 0.10% published summaries give. Calibrated: 12,400 x 0.0015 = 18.60."""
    assert C.stt_sell_pct == pytest.approx(0.15)
    assert C.breakdown(310.0, 40, is_buy=False)["stt"] == pytest.approx(18.60, abs=0.01)


def test_exchange_transaction_charges_differ_by_exchange():
    """One rate could only ever be right for one exchange."""
    assert C.txn_pct_for("NFO") == pytest.approx(0.03545)
    assert C.txn_pct_for("BFO") == pytest.approx(0.0325)
    assert C.txn_pct_for("BSE") == pytest.approx(0.0325)
    assert C.txn_pct_for("NSE") == pytest.approx(0.03545)


def test_ipft_is_not_billed_separately():
    """The note has no IPFT line -- it is inside the exchange charge. Billing it again was a
    real double-count in the first version of this model."""
    assert C.ipft_pct == 0.0


# --- the one-time correction ----------------------------------------------------------


def test_a_deployment_carrying_the_old_defaults_is_corrected(tmp_path, monkeypatch):
    """A CREATE TABLE default cannot reach a row that already exists.

    Without this, a deployment that booted before the calibration would keep rates that were
    never right — silently, in the number that decides whether the strategy is viable.
    """
    import sqlite3

    path = str(tmp_path / "users_test.sqlite3")
    monkeypatch.setattr(charges_mod, "_db_path", lambda: path)
    ensure_bots_tables(path)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE trading_charges SET stt_sell_pct = 0.10, exchange_txn_pct = 0.0495, "
            "ipft_pct = 0.0005 WHERE id = 1"
        )
        conn.commit()

    ensure_bots_tables(path)

    model = load_charges()
    assert model.stt_sell_pct == pytest.approx(0.15)
    assert model.exchange_txn_pct == pytest.approx(0.03545)
    assert model.ipft_pct == 0.0


def test_a_rate_the_user_chose_is_never_overwritten(tmp_path, monkeypatch):
    """Only the exact superseded values are replaced. Anything else was deliberate."""
    import sqlite3

    path = str(tmp_path / "users_test.sqlite3")
    monkeypatch.setattr(charges_mod, "_db_path", lambda: path)
    ensure_bots_tables(path)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE trading_charges SET stt_sell_pct = 0.125, brokerage_per_order_inr = 9.99 "
            "WHERE id = 1"
        )
        conn.commit()

    ensure_bots_tables(path)

    model = load_charges()
    assert model.stt_sell_pct == pytest.approx(0.125)
    assert model.brokerage_per_order_inr == pytest.approx(9.99)


def test_the_correction_is_idempotent(tmp_path, monkeypatch):
    path = str(tmp_path / "users_test.sqlite3")
    monkeypatch.setattr(charges_mod, "_db_path", lambda: path)
    for _ in range(3):
        ensure_bots_tables(path)
    assert load_charges().stt_sell_pct == pytest.approx(0.15)
