"""Tests for dashboard_day_pnl_live: the WS-fed live Day's P&L baseline.

Cross-checked against dashboard_day_pnl.compute_day_pnl on the same 23-Jul-2026
BSESEN book used by test_dashboard_day_pnl -- with no new fills the live payload
must reproduce the REST computation exactly. Then a WS fill and a reconcile.
"""
from __future__ import annotations

import datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from icici_breeze_backend.app.services import dashboard_day_pnl as day_pnl_mod
from icici_breeze_backend.app.services import dashboard_day_pnl_live as live
from icici_breeze_backend.app.services.dashboard_day_pnl import compute_day_pnl
from icici_breeze_backend.app.services.reference_data.scrip_index import contract_index_key
from icici_breeze_backend.app.core.strike import parse_strike

SESSION_DATE = datetime.date(2026, 7, 23)
TODAY = SESSION_DATE

PREV_CLOSE = {
    ("BFO", "BSESEN", "23-Jul-2026", "Put", 74300.0): 1.50,
    ("BFO", "BSESEN", "23-Jul-2026", "Call", 79800.0): 1.50,
}


def _lookup(exchange, stock, expiry, right, strike):
    return PREV_CLOSE.get((exchange, stock, expiry, right, float(strike)))


def _pos(strike, right, action, qty, ltp):
    return {
        "exchange_code": "BFO", "stock_code": "BSESEN", "expiry_date": "23-Jul-2026",
        "strike_price": strike, "right": right, "action": action,
        "quantity": qty, "ltp": ltp,
    }


def _trade(strike, right, action, qty, cost, trade_date="23-Jul-2026"):
    return {
        "exchange_code": "BFO", "stock_code": "BSESEN", "expiry_date": "23-Jul-2026",
        "strike_price": strike, "right": right, "action": action,
        "quantity": qty, "average_cost": cost, "trade_date": trade_date,
    }


POSITIONS = [
    _pos(74300, "Put", "Sell", 4420, 0.05),
    _pos(79800, "Call", "Sell", 4420, 0.05),
    _pos(76300, "Put", "Buy", 620, 0.05),
    _pos(76500, "Call", "Buy", 620, 0.05),
]
TRADES = [
    _trade(76400, "Call", "Buy", 620, 0.8),
    _trade(76400, "Put", "Buy", 620, 11),
    _trade(76400, "Call", "Sell", 620, 0.5),
    _trade(76500, "Call", "Buy", 620, 0.25),
    _trade(76400, "Put", "Sell", 620, 64.45),
    _trade(76300, "Put", "Buy", 620, 2.65),
    _trade(74300, "Put", "Sell", 4420, 4, trade_date="22-Jul-2026"),
    _trade(79800, "Call", "Sell", 4420, 4, trade_date="22-Jul-2026"),
]

# LTPs keyed the way _fetch_live_ltps returns them (contract_index_key -> ltp),
# matching each open leg's `ltp` in POSITIONS.
LIVE_LTPS = {
    contract_index_key("BFO", "BSESEN", "23-Jul-2026", parse_strike(s), r): 0.05
    for s, r in [(74300, "Put"), (79800, "Call"), (76300, "Put"), (76500, "Call")]
}


@pytest.fixture(autouse=True)
def _clean_state():
    live.reset_state_for_tests()
    yield
    live.reset_state_for_tests()


def _open_session():
    return patch.multiple(
        live,
        _day_pnl_session_state=lambda now=None: "open",
        latest_opened_trading_day=lambda now=None: SESSION_DATE,
        make_prev_close_lookup=lambda now=None: _lookup,
        _today=lambda: TODAY,
        _fetch_live_ltps=lambda keys: dict(LIVE_LTPS),
    )


def _oracle():
    with patch.multiple(
        day_pnl_mod,
        _day_pnl_session_state=lambda now=None: "open",
        latest_opened_trading_day=lambda now=None: SESSION_DATE,
    ):
        return compute_day_pnl(POSITIONS, TRADES, prev_close_lookup=_lookup)


def test_live_payload_matches_rest_computation_with_no_new_fills():
    oracle = _oracle()
    with _open_session():
        live.capture_baseline("u1", POSITIONS, TRADES, trades_source_ok=True)
        live.run_tick()
        got = live.latest("u1")

    assert got is not None
    assert got["source"] == "live"
    assert round(got["total_day_pnl"], 2) == round(oracle["total_day_pnl"], 2) == 44035.0
    assert round(got["realized_day_pnl"], 2) == round(oracle["realized_day_pnl"], 2) == 32953.0
    assert round(got["unrealized_day_pnl"], 2) == round(oracle["unrealized_day_pnl"], 2) == 11082.0
    assert got["contracts_missing_prev_close"] == 0
    assert got["degraded"] is False


def test_no_baseline_returns_none():
    with _open_session():
        live.run_tick()
    assert live.latest("nobody") is None


def _notif(strike, right, action, executed_qty, limit_price, order_id="O1"):
    return SimpleNamespace(
        user_id="u1",
        order_id=order_id,
        status="executed",
        stock_code="BSESEN",
        exchange_code="BFO",
        expiry_display="23-Jul-2026",
        strike=parse_strike(strike),
        right=right,
        action=action,
        limit_price=limit_price,
        executed_quantity=executed_qty,
    )


def test_ws_fill_moves_the_number_at_approx_price():
    with _open_session():
        live.capture_baseline("u1", POSITIONS, TRADES, trades_source_ok=True)
        live.run_tick()
        before = live.latest("u1")["total_day_pnl"]

        # Close the 76300 PE long (Buy 620 @ 2.65 avg) by selling 620 @ 3.00.
        # Realized delta = −(q·entry) contribution flips: total moves by
        # +Σδp change = −(−620·3.00) vs holding at ltp 0.05 ... simplest assertion:
        # the number changes and the leg is now flat.
        live.on_order_notification(_notif(76300, "Put", "Sell", 620, 3.00))
        live.run_tick()
        after = live.latest("u1")

    assert after["total_day_pnl"] != before
    # 76300 PE: q0=0, sum_delta = +620 (baseline buy) −620 (this sell) = 0 -> flat.
    # realized for it = −sum_dp = −(620·2.65 − 620·3.00) = +217
    # was unrealized 620·(0.05 − 2.65) = −1612 before; now realized +217, unrealized 0.
    # net change on that leg: +217 − (−1612) = +1829
    assert round(after["total_day_pnl"] - before, 2) == 1829.0


def test_reconcile_replaces_approx_price_with_trade_book():
    extra_sell = _trade(76300, "Put", "Sell", 620, 3.10)  # real fill 3.10, not the 3.00 approx

    def _fake_get_trades(user_id, frm, to):
        return {"Status": 200, "Success": TRADES + [extra_sell]}

    with _open_session(), patch(
        "icici_breeze_backend.app.services.processor.processor",
        return_value=SimpleNamespace(get_trades=_fake_get_trades),
    ):
        live.capture_baseline("u1", POSITIONS, TRADES, trades_source_ok=True)
        live.on_order_notification(_notif(76300, "Put", "Sell", 620, 3.00))
        # Force the debounce to have elapsed.
        live._state["u1"].reconcile_due = live.time.monotonic() - 1
        live.run_tick()
        after = live.latest("u1")

    # realized for 76300 PE now uses 3.10: −(620·2.65 − 620·3.10) = +279
    # net change vs the pre-fill baseline (−1612 unrealized) = +279 − (−1612) = +1891
    base = _oracle()["total_day_pnl"]
    assert round(after["total_day_pnl"] - base, 2) == 1891.0
    assert live._state["u1"].reconcile_due is None
