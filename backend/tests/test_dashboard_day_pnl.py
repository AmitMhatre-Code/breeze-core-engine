"""Tests for the mark-to-market Day's P&L (dashboard_day_pnl.compute_day_pnl).

The primary oracle is the real 23-Jul-2026 BSESEN book from the production incident:
four open legs plus two intraday round-trips. The old unrealized-only tile showed
garbage (+₹58cr); the identity must reproduce every leg exactly and surface the
+₹32,953 of realized round-trip P&L the old tile dropped.
"""
from __future__ import annotations

import datetime
from unittest.mock import patch

from icici_breeze_backend.app.services import dashboard_day_pnl as mod
from icici_breeze_backend.app.services.dashboard_day_pnl import compute_day_pnl

SESSION_DATE = datetime.date(2026, 7, 23)

# Previous-session (22-Jul) closes for the two carried deep-OTM shorts.
PREV_CLOSE = {
    ("BFO", "BSESEN", "23-Jul-2026", "Put", 74300.0): 1.50,
    ("BFO", "BSESEN", "23-Jul-2026", "Call", 79800.0): 1.50,
}


def _lookup(exchange, stock, expiry, right, strike):
    return PREV_CLOSE.get((exchange, stock, expiry, right, float(strike)))


def _pos(strike, right, action, qty, ltp):
    return {
        "exchange_code": "BFO",
        "stock_code": "BSESEN",
        "expiry_date": "23-Jul-2026",
        "strike_price": strike,
        "right": right,
        "action": action,
        "quantity": qty,
        "ltp": ltp,
    }


def _trade(strike, right, action, qty, cost, trade_date="23-Jul-2026"):
    return {
        "exchange_code": "BFO",
        "stock_code": "BSESEN",
        "expiry_date": "23-Jul-2026",
        "strike_price": strike,
        "right": right,
        "action": action,
        "quantity": qty,
        "average_cost": cost,
        "trade_date": trade_date,
    }


# Open positions (the four legs still held at snapshot time).
POSITIONS = [
    _pos(74300, "Put", "Sell", 4420, 0.05),   # A carried short
    _pos(79800, "Call", "Sell", 4420, 0.05),  # B carried short
    _pos(76300, "Put", "Buy", 620, 0.05),     # C opened today
    _pos(76500, "Call", "Buy", 620, 0.05),    # D opened today
]

# Today's fills (23-Jul) + some prior-day fills that must be filtered out by date.
TRADES = [
    _trade(76400, "Call", "Buy", 620, 0.8),    # round-trip 1
    _trade(76400, "Put", "Buy", 620, 11),      # round-trip 2
    _trade(76400, "Call", "Sell", 620, 0.5),   # round-trip 1
    _trade(76500, "Call", "Buy", 620, 0.25),   # opens D
    _trade(76400, "Put", "Sell", 620, 64.45),  # round-trip 2
    _trade(76300, "Put", "Buy", 620, 2.65),    # opens C
    # 22-Jul entries for the carried shorts -- MUST be ignored (wrong trade_date):
    _trade(74300, "Put", "Sell", 4420, 4, trade_date="22-Jul-2026"),
    _trade(79800, "Call", "Sell", 4420, 4, trade_date="22-Jul-2026"),
]


def _open_session():
    return patch.multiple(
        mod,
        _day_pnl_session_state=lambda now=None: "open",
        latest_opened_trading_day=lambda now=None: SESSION_DATE,
    )


def test_real_book_reproduces_every_leg_and_realized():
    with _open_session():
        r = compute_day_pnl(POSITIONS, TRADES, prev_close_lookup=_lookup)

    assert r["market_session_state"] == "open"
    assert r["contracts_missing_prev_close"] == 0
    assert r["contracts_priced"] == 6  # 4 open + 2 round-trip contracts

    # Realized = the two intraday round-trips the old tile ignored entirely:
    #   76400 CE: −(620·0.8 − 620·0.5) = −186
    #   76400 PE: −(620·11 − 620·64.45) = +33,139
    assert round(r["realized_day_pnl"], 2) == 32953.0

    # Unrealized (open MTM): carried shorts from 22-Jul close 1.50 -> 0.05, plus the
    # two same-day entries marked from their entry price:
    #   A,B: −4420·(0.05 − 1.50) = +6409 each
    #   C:   620·(0.05 − 2.65) = −1612 ; D: 620·(0.05 − 0.25) = −124
    assert round(r["unrealized_day_pnl"], 2) == 11082.0
    assert round(r["total_day_pnl"], 2) == 44035.0
    assert r["is_gross"] is True


def test_opened_today_leg_matches_since_entry():
    """A leg opened today is pure unrealized, marked from entry (not any prior close)."""
    with _open_session():
        r = compute_day_pnl(
            [_pos(76300, "Put", "Buy", 620, 0.05)],
            [_trade(76300, "Put", "Buy", 620, 2.65)],
            prev_close_lookup=_lookup,
        )
    assert round(r["total_day_pnl"], 2) == -1612.0
    assert round(r["realized_day_pnl"], 2) == 0.0
    assert round(r["unrealized_day_pnl"], 2) == -1612.0


def test_flat_round_trip_is_pure_realized():
    with _open_session():
        r = compute_day_pnl(
            [],
            [
                _trade(76400, "Put", "Buy", 620, 11),
                _trade(76400, "Put", "Sell", 620, 64.45),
            ],
            prev_close_lookup=_lookup,
        )
    assert r["contracts_priced"] == 1
    assert round(r["realized_day_pnl"], 2) == 33139.0
    assert round(r["unrealized_day_pnl"], 2) == 0.0
    assert round(r["total_day_pnl"], 2) == 33139.0


def test_carried_short_untraded_marks_from_prev_close():
    with _open_session():
        r = compute_day_pnl(
            [_pos(74300, "Put", "Sell", 4420, 0.05)], [], prev_close_lookup=_lookup
        )
    # −4420·(0.05 − 1.50) = +6409, all unrealized
    assert round(r["total_day_pnl"], 2) == 6409.0
    assert round(r["unrealized_day_pnl"], 2) == 6409.0
    assert round(r["realized_day_pnl"], 2) == 0.0


def test_partial_squareoff_of_carry_splits_realized_and_unrealized():
    """Carried short 4420 @ prev close 1.50; buy back 1000 today @ 0.30; 3420 still open."""
    with _open_session():
        r = compute_day_pnl(
            [_pos(74300, "Put", "Sell", 3420, 0.05)],
            [_trade(74300, "Put", "Buy", 1000, 0.30)],
            prev_close_lookup=_lookup,
        )
    # realized on the 1000 covered: 1000·(prev_close − fill) = 1000·(1.50 − 0.30) = 1200
    # unrealized on 3420 still open: −3420·(0.05 − 1.50) = 4959
    assert round(r["realized_day_pnl"], 2) == 1200.0
    assert round(r["unrealized_day_pnl"], 2) == 4959.0
    assert round(r["total_day_pnl"], 2) == 6159.0


def test_carried_leg_without_prev_close_is_flagged_not_valued():
    """No valid previous-session close for a carried leg -> excluded + flagged, not guessed."""
    with _open_session():
        r = compute_day_pnl(
            [_pos(74300, "Put", "Sell", 4420, 0.05)],
            [],
            prev_close_lookup=lambda *a: None,
        )
    assert r["contracts_priced"] == 0
    assert r["contracts_missing_prev_close"] == 1
    assert r["degraded"] is True
    assert r["total_day_pnl"] == 0.0


def test_pre_open_forces_zero():
    with patch.multiple(mod, _day_pnl_session_state=lambda now=None: "pre_open"):
        r = compute_day_pnl(POSITIONS, TRADES, prev_close_lookup=_lookup)
    assert r["total_day_pnl"] == 0.0
    assert r["realized_day_pnl"] == 0.0


def test_non_trading_day_forces_zero():
    with patch.multiple(
        mod, _day_pnl_session_state=lambda now=None: "closed_non_trading_day"
    ):
        r = compute_day_pnl(POSITIONS, TRADES, prev_close_lookup=_lookup)
    assert r["total_day_pnl"] == 0.0
