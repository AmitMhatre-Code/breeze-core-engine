"""Tests for the Dashboard "Day's P&L" tile computation."""
from __future__ import annotations

from unittest.mock import patch

from icici_breeze_backend.app.services.dashboard_days_pnl import compute_days_pnl

MODULE = "icici_breeze_backend.app.services.dashboard_days_pnl"


def _position(**overrides):
    base = {
        "stock_code": "NIFTY",
        "exchange_code": "NFO",
        "expiry_date": "30-Jul-2026",
        "strike_price": "24000",
        "right": "Call",
        "action": "Buy",
        "quantity": 75,
        "average_price": 100.0,
        "ltp": 120.0,
    }
    base.update(overrides)
    return base


def _open_market_mocks():
    return patch.multiple(
        MODULE,
        is_india_market_open=lambda now=None: True,
        is_india_trading_day=lambda now=None: True,
        market_closed_reason=lambda now=None: "market open",
    )


def _pre_open_mocks():
    return patch.multiple(
        MODULE,
        is_india_market_open=lambda now=None: False,
        is_india_trading_day=lambda now=None: True,
        market_closed_reason=lambda now=None: "before market open (9:15 AM IST)",
    )


def _weekend_mocks():
    return patch.multiple(
        MODULE,
        is_india_market_open=lambda now=None: False,
        is_india_trading_day=lambda now=None: False,
        market_closed_reason=lambda now=None: "weekend",
    )


def test_buy_position_uses_bhavcopy_ltp_not_previous_close():
    """Regression test: must reference the bhavcopy row's `ltp`, not `previous_close`,
    since during today's session the freshest loaded bhavcopy is yesterday's file and
    that file's own `previous_close` column is two sessions stale."""
    pos = _position(action="Buy", quantity=75, average_price=100.0, ltp=120.0)
    with _open_market_mocks():
        with patch(
            f"{MODULE}._lookup_bhav_row",
            return_value={"ltp": "110.0", "previous_close": "90.0"},
        ):
            result = compute_days_pnl([pos])
    assert result["market_session_state"] == "open"
    # (ltp - reference) * qty = (120 - 110) * 75 = 750, NOT (120-90)*75=2250
    assert result["unrealized_day_pnl"] == 750.0
    assert result["positions_priced"] == 1
    assert result["positions_missing_reference"] == 0


def test_sell_position_sign_convention():
    pos = _position(action="Sell", quantity=75, average_price=100.0, ltp=120.0)
    with _open_market_mocks():
        with patch(f"{MODULE}._lookup_bhav_row", return_value={"ltp": "110.0"}):
            result = compute_days_pnl([pos])
    # SELL: (reference - ltp) * qty = (110 - 120) * 75 = -750
    assert result["unrealized_day_pnl"] == -750.0


def test_falls_back_to_average_price_when_no_bhavcopy_row():
    pos = _position(action="Buy", quantity=10, average_price=100.0, ltp=115.0)
    with _open_market_mocks():
        with patch(f"{MODULE}._lookup_bhav_row", return_value=None):
            result = compute_days_pnl([pos])
    # (ltp - average_price) * qty = (115 - 100) * 10 = 150
    assert result["unrealized_day_pnl"] == 150.0
    assert result["positions_missing_reference"] == 1


def test_sums_across_multiple_positions():
    positions = [
        _position(action="Buy", quantity=10, average_price=100.0, ltp=110.0),
        _position(action="Sell", quantity=5, average_price=50.0, ltp=40.0),
    ]
    with _open_market_mocks():
        with patch(f"{MODULE}._lookup_bhav_row", return_value=None):
            result = compute_days_pnl(positions)
    # Buy leg: (110-100)*10=100 ; Sell leg: (50-40)*5=50 ; total=150
    assert result["unrealized_day_pnl"] == 150.0
    assert result["positions_priced"] == 2


def test_pre_market_forces_zero():
    pos = _position()
    with _pre_open_mocks():
        with patch(f"{MODULE}._lookup_bhav_row", return_value={"ltp": "110.0"}) as mock_lookup:
            result = compute_days_pnl([pos])
    assert result["market_session_state"] == "pre_open"
    assert result["unrealized_day_pnl"] == 0.0
    mock_lookup.assert_not_called()


def test_non_trading_day_forces_zero():
    pos = _position()
    with _weekend_mocks():
        result = compute_days_pnl([pos])
    assert result["market_session_state"] == "closed_non_trading_day"
    assert result["unrealized_day_pnl"] == 0.0


def test_post_close_same_day_computes_normally():
    with patch.multiple(
        MODULE,
        is_india_market_open=lambda now=None: False,
        is_india_trading_day=lambda now=None: True,
        market_closed_reason=lambda now=None: "after market close (3:30 PM IST)",
    ):
        with patch(f"{MODULE}._lookup_bhav_row", return_value={"ltp": "110.0"}):
            result = compute_days_pnl([_position(action="Buy", quantity=75, average_price=100.0, ltp=120.0)])
    assert result["market_session_state"] == "post_close"
    assert result["unrealized_day_pnl"] == 750.0


def test_empty_positions_returns_zero():
    with _open_market_mocks():
        result = compute_days_pnl([])
    assert result["unrealized_day_pnl"] == 0.0
    assert result["total_day_pnl"] == 0.0


def test_zero_quantity_position_skipped():
    pos = _position(quantity=0)
    with _open_market_mocks():
        with patch(f"{MODULE}._lookup_bhav_row", return_value={"ltp": "110.0"}):
            result = compute_days_pnl([pos])
    assert result["unrealized_day_pnl"] == 0.0
    assert result["positions_priced"] == 0
