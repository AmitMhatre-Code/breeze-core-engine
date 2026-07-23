"""Netted SPAN margin for Strategy Groups + the whole portfolio.

Replaces the old sum-of-naked-legs behaviour: SPAN is a portfolio risk model, so
each Strategy Group gets one multi-leg margin_calculator call and the portfolio
gets one call per exchange (NSE/BSE don't cross-net), summed.
"""

from __future__ import annotations

import uuid

from icici_breeze_backend.app.api.v1.route_portfolio import (
    _normalize_portfolio_success_for_ui,
)
from icici_breeze_backend.app.services.processor import processor


def _uid() -> str:
    """Fresh user id per test so the composition-keyed margin cache (backed by a
    real Redis in CI) is always cold — otherwise a prior run's cached netted SPAN
    masks the broker calls a test asserts on."""
    return "netmargin-test-" + uuid.uuid4().hex


class _FakeBreeze:
    """margin_calculator that reacts to the leg set: sells add margin, opposing
    CE/PE offset (netting), and long legs hedge — so a multi-leg call returns less
    than its legs priced alone. Records each call's leg count + exchange."""

    def __init__(self):
        self.calls: list[tuple[str, int]] = []

    def margin_calculator(self, margin_list, exchange_code: str = "", **kwargs):
        legs = margin_list or []
        self.calls.append((exchange_code, len(legs)))
        sells = [l for l in legs if str(l.get("action", "")).lower().startswith("s")]
        buys = [l for l in legs if str(l.get("action", "")).lower().startswith("b")]
        naked = 1000.0 * sum(abs(float(l["quantity"])) for l in sells)
        rights = {str(l.get("right", "")).lower()[:1] for l in sells}
        net_factor = 0.65 if {"c", "p"} <= rights else 1.0
        hedge = 600.0 * sum(abs(float(l["quantity"])) for l in buys)
        span = max(0.0, naked * net_factor - hedge)
        return {"Status": 200, "Success": {"span_margin_required": span}, "Error": None}


def _leg(stock, exch, expiry, right, action, strike, qty, *, elm=None, carry=0.0):
    return {
        "stock_code": stock,
        "exchange_code": exch,
        "expiry_date": expiry,
        "product_type": "Options",
        "right": right,
        "action": action,
        "strike_price": strike,
        "quantity": qty,
        "elm_margin_required": elm,
        "carry_profit": carry,
    }


def test_group_span_is_netted_not_summed():
    """A short strangle's group SPAN is one netted call, below the naked-leg sum."""
    breeze = _FakeBreeze()
    legs = [
        _leg("BSESEN", "BFO", "23-Jul-2026", "Put", "Sell", "74300", 20, elm=0.0),
        _leg("BSESEN", "BFO", "23-Jul-2026", "Call", "Sell", "79800", 20, elm=0.0),
    ]
    out = processor()._compute_netted_margins(breeze, _uid(), legs)

    assert len(out["groups"]) == 1
    grp = out["groups"][0]
    # Netted: 1000*(20+20) * 0.65 = 26000, strictly less than naked sum 40000.
    assert grp["span_margin_required"] == 26000.0
    assert grp["span_margin_required"] < 40000.0
    assert grp["key"] == "BSESEN|BFO|23-Jul-2026"
    assert grp["elm_margin_required"] == 0.0


def test_portfolio_span_is_per_underlying_summed():
    """Netting is capped at one underlying: each underlying is netted alone and the
    portfolio is their sum -- distinct underlyings never offset, even same-exchange."""
    breeze = _FakeBreeze()
    legs = [
        # NIFTY strangle (nets internally) + BANKNIFTY leg, both on NFO...
        _leg("NIFTY", "NFO", "31-Jul-2026", "Call", "Sell", "24800", 50),
        _leg("NIFTY", "NFO", "31-Jul-2026", "Put", "Sell", "23500", 50),
        _leg("CNXBAN", "NFO", "31-Jul-2026", "Put", "Sell", "52000", 15),
        # ...plus a BSESEN leg on BFO.
        _leg("BSESEN", "BFO", "23-Jul-2026", "Put", "Sell", "74300", 20),
    ]
    out = processor()._compute_netted_margins(breeze, _uid(), legs)

    # Per underlying: NIFTY 1000*100*0.65 = 65000 ; CNXBAN 1000*15 = 15000 ;
    # BSESEN 1000*20 = 20000 ; sum = 100000. Note NIFTY and CNXBAN share NFO but
    # are NOT netted together (a per-exchange call would have offset them).
    assert out["portfolio"]["span_margin_required"] == 100000.0
    # Each underlying is priced in its own call — 3 distinct underlyings.
    underlying_call_leg_counts = sorted(n for _exch, n in breeze.calls if n)
    # NIFTY portfolio call has 2 legs; CNXBAN 1; BSESEN 1 (group calls dedupe via cache).
    assert 2 in underlying_call_leg_counts


def test_buy_legs_reduce_group_span():
    """Hedge (Buy) legs are included in the netted call and lower the margin."""
    breeze = _FakeBreeze()
    legs = [
        _leg("NIFTY", "NFO", "31-Jul-2026", "Call", "Sell", "24800", 50),
        _leg("NIFTY", "NFO", "31-Jul-2026", "Put", "Buy", "23500", 25),
    ]
    out = processor()._compute_netted_margins(breeze, _uid(), legs)
    # 1000*50 (only one sell, so no CE/PE offset) - 600*25 = 50000 - 15000 = 35000.
    assert out["groups"][0]["span_margin_required"] == 35000.0


def test_elm_is_additive_and_carry_return_uses_netted_margin():
    breeze = _FakeBreeze()
    legs = [
        _leg("BSESEN", "BFO", "23-Jul-2026", "Put", "Sell", "74300", 20, elm=1000.0, carry=5000.0),
        _leg("BSESEN", "BFO", "23-Jul-2026", "Call", "Sell", "79800", 20, elm=1500.0, carry=3000.0),
    ]
    out = processor()._compute_netted_margins(breeze, _uid(), legs)
    grp = out["groups"][0]
    assert grp["elm_margin_required"] == 2500.0  # additive, not netted
    assert out["portfolio"]["elm_margin_required"] == 2500.0
    # carry-return is a finite % computed on netted (SPAN + ELM), carry summed.
    assert grp["carry_margin_returns"] is not None
    assert out["portfolio"]["carry_margin_returns"] is not None


def test_failed_margin_call_yields_none_not_naked_sum():
    class _Failing:
        def margin_calculator(self, *a, **k):
            raise RuntimeError("boom")

    # A composition/user never priced elsewhere, so no cache masks the failure.
    legs = [_leg("XYZFAIL", "BFO", "23-Jul-2026", "Put", "Sell", "11100", 7)]
    out = processor()._compute_netted_margins(_Failing(), _uid(), legs)
    assert out["groups"][0]["span_margin_required"] is None
    assert out["portfolio"]["span_margin_required"] is None


def test_normalize_injects_groups_and_portfolio_when_present():
    raw = {
        "Status": 200,
        "Success": [
            {"stock_code": "BSESEN", "quantity": "20", "pnl": "0"},
        ],
        "groups": [{"key": "BSESEN|BFO|23-Jul-2026", "span_margin_required": 26000.0}],
        "portfolio": {"span_margin_required": 26000.0, "elm_margin_required": 0.0},
    }
    out = _normalize_portfolio_success_for_ui(raw)
    assert out["Success"]["groups"] == raw["groups"]
    assert out["Success"]["portfolio"] == raw["portfolio"]
    # Sibling keys don't leak to the top level.
    assert "groups" not in out
    assert "portfolio" not in out


def test_normalize_empty_portfolio_stays_bare():
    """Empty portfolios keep the exact {"positions": []} shape (no groups/portfolio)."""
    out = _normalize_portfolio_success_for_ui({"Status": 200, "Success": [], "Error": "x"})
    assert out["Success"] == {"positions": []}
