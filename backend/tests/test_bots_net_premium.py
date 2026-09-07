"""Net-of-charges premium on the writer bots (services.bots.net_premium).

Bots 1 and 2 quoted gross premium; the scalpers had always priced net. That was a real gap,
not a cosmetic one — a proposal's premium is the number a user approves a trade on, and Rs 20
brokerage plus GST against a few hundred rupees of premium is not a rounding error.
"""
from __future__ import annotations

import sqlite3

import pytest

from icici_breeze_backend.app.db.bots_migrate import ensure_bots_tables
from icici_breeze_backend.app.domain.bots import ProposalLeg
from icici_breeze_backend.app.services.bots import charges as charges_mod
from icici_breeze_backend.app.services.bots.charges import ChargesModel
from icici_breeze_backend.app.services.bots.net_premium import (
    annotate_leg,
    annotate_legs,
    estimate_leg_charges,
)

C = ChargesModel()


def _leg(premium=4.25, qty=1725, **kw):
    base = dict(
        stock_code="ITC", right="call", expiry_display="24-Sep-2026", strike_price=280.0,
        lots=1, lot_size=qty, quantity=qty,
        premium_per_share=premium, premium_total=round(premium * qty, 2),
    )
    base.update(kw)
    return ProposalLeg(**base)


def test_the_estimate_matches_a_real_contract_note_fill():
    """A NIFTY 25150 CE sale of 1,755 @ Rs 1.35 was billed Rs 28.13 on the note."""
    assert estimate_leg_charges(1.35, 1755, charges=C) == pytest.approx(28.13, abs=0.10)


def test_a_written_leg_is_annotated_with_charges_and_a_net():
    leg = annotate_leg(_leg(), charges=C)
    assert leg.estimated_charges > 0
    assert leg.net_premium_total == pytest.approx(leg.premium_total - leg.estimated_charges, abs=0.01)
    assert leg.net_premium_total < leg.premium_total


def test_charges_are_priced_on_the_sell_side():
    """Writing is selling, so STT applies and stamp duty does not."""
    leg = annotate_leg(_leg(), charges=C)
    sell = C.leg_charges(4.25, 1725, is_buy=False)
    assert leg.estimated_charges == pytest.approx(sell, abs=0.01)
    assert sell > C.leg_charges(4.25, 1725, is_buy=True)


def test_a_bse_leg_uses_the_bse_transaction_rate():
    nfo = annotate_leg(_leg(exchange_code="NFO"), charges=C).estimated_charges
    bfo = annotate_leg(_leg(exchange_code="BFO"), charges=C).estimated_charges
    assert bfo < nfo  # BSE charges less per rupee of turnover


def test_chunking_multiplies_the_per_order_brokerage():
    """A leg above the freeze limit goes out as several orders, each paying brokerage."""
    one = estimate_leg_charges(4.25, 1725, orders=1, charges=C)
    three = estimate_leg_charges(4.25, 1725, orders=3, charges=C)
    extra = C.brokerage_per_order_inr * 2 * (1 + C.gst_pct / 100)
    assert three == pytest.approx(one + extra, abs=0.05)


def test_a_leg_that_cannot_be_priced_is_left_alone_not_raised_on():
    """A cost estimate failing must never cost the user the proposal itself."""
    leg = annotate_leg(_leg(premium=0.0), charges=C)
    assert leg.estimated_charges is None and leg.net_premium_total is None


def test_annotating_a_batch_loads_the_model_once(monkeypatch):
    calls = {"n": 0}

    def counted():
        calls["n"] += 1
        return C

    monkeypatch.setattr("icici_breeze_backend.app.services.bots.net_premium.load_charges", counted)
    annotate_legs([_leg(), _leg(), _leg()])
    assert calls["n"] == 1


def test_small_legs_lose_a_meaningful_share_of_their_premium():
    """Why this exists at all: on a thin leg the flat brokerage dominates."""
    leg = annotate_leg(_leg(premium=0.55, qty=780), charges=C)
    share = leg.estimated_charges / leg.premium_total
    assert share > 0.05  # over 5% of the premium, on a real-shaped Bot 2 leg


# --- the rename ------------------------------------------------------------------------


def test_an_existing_scalping_charges_table_is_renamed_keeping_its_edits(tmp_path, monkeypatch):
    """A create-and-abandon would leave the user's corrected rates in an orphaned table
    while the app read defaults from a new empty one."""
    path = str(tmp_path / "users_test.sqlite3")
    monkeypatch.setattr(charges_mod, "_db_path", lambda: path)
    ensure_bots_tables(path)
    with sqlite3.connect(path) as conn:
        conn.execute("ALTER TABLE trading_charges RENAME TO scalping_charges")
        conn.execute("UPDATE scalping_charges SET brokerage_per_order_inr = 9.99 WHERE id = 1")
        conn.commit()

    ensure_bots_tables(path)

    with sqlite3.connect(path) as conn:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "trading_charges" in names and "scalping_charges" not in names
    assert charges_mod.load_charges().brokerage_per_order_inr == pytest.approx(9.99)


def test_the_rename_is_idempotent(tmp_path, monkeypatch):
    path = str(tmp_path / "users_test.sqlite3")
    monkeypatch.setattr(charges_mod, "_db_path", lambda: path)
    for _ in range(3):
        ensure_bots_tables(path)
    assert charges_mod.load_charges().brokerage_per_order_inr == pytest.approx(20.0)
