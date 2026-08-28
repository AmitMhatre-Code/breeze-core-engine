"""Shared netting primitives for Strategy Builder portfolio-aware margin.

See docs/strategy-builder-portfolio-margin-plan.md (D1-D10) for the design.
These tests cover portfolio_margin_netting.py in isolation -- no engine or
route wiring yet (that lands in later phases).
"""

from __future__ import annotations

import uuid

from icici_breeze_backend.app.services.portfolio_margin_netting import (
    PositionSet,
    existing_span,
    normalize_stock,
    positions_for_underlying,
    positions_to_margin_input,
)
from icici_breeze_backend.app.services.processor import processor


def _uid() -> str:
    return "posnet-test-" + uuid.uuid4().hex


def _row(stock, exch, expiry, right, action, strike, qty):
    return {
        "stock_code": stock,
        "exchange_code": exch,
        "expiry_date": expiry,
        "product_type": "Options",
        "right": right,
        "action": action,
        "strike_price": strike,
        "quantity": qty,
    }


class _FakeBreeze:
    def __init__(self):
        self.calls: list[tuple[str, int]] = []

    def margin_calculator(self, margin_list, exchange_code: str = "", **kwargs):
        legs = margin_list or []
        self.calls.append((exchange_code, len(legs)))
        sells = [l for l in legs if str(l.get("action", "")).lower().startswith("s")]
        buys = [l for l in legs if str(l.get("action", "")).lower().startswith("b")]
        naked = 1000.0 * sum(abs(float(l["quantity"])) for l in sells)
        hedge = 600.0 * sum(abs(float(l["quantity"])) for l in buys)
        span = max(0.0, naked - hedge)
        return {"Status": 200, "Success": {"span_margin_required": span}, "Error": None}


def test_normalize_stock_matches_route_hedge_behaviour():
    assert normalize_stock("reliance") == "RELIANCE"
    assert normalize_stock("  nifty  ") == "NIFTY"
    assert normalize_stock("BANKNIFTY EQ") == "BANKNIFTY"


def test_positions_for_underlying_filters_by_stock_and_exchange(monkeypatch):
    rows = [
        _row("NIFTY", "NFO", "31-Jul-2026T06:00:00.000Z", "Call", "Sell", "24800", "50"),
        _row("NIFTY", "NFO", "28-Aug-2026T06:00:00.000Z", "Put", "Sell", "23500", "25"),
        _row("BANKNIFTY", "NFO", "31-Jul-2026T06:00:00.000Z", "Call", "Sell", "52000", "15"),
        _row("NIFTY", "BFO", "31-Jul-2026T06:00:00.000Z", "Put", "Sell", "24000", "10"),
    ]
    monkeypatch.setattr(
        processor, "get_positions", lambda self, user_id: {"Status": 200, "Success": rows, "Error": None}
    )

    ps = positions_for_underlying(processor(), _uid(), "NIFTY", "NFO")

    assert ps.available is True
    assert ps.error is None
    assert len(ps.rows) == 2
    assert {r["expiry_date"] for r in ps.rows} == {
        "31-Jul-2026T06:00:00.000Z",
        "28-Aug-2026T06:00:00.000Z",
    }
    assert ps.expiries == sorted(ps.expiries)
    assert ps.fingerprint  # non-empty when positions exist


def test_positions_for_underlying_excludes_zero_quantity_rows(monkeypatch):
    rows = [
        _row("NIFTY", "NFO", "31-Jul-2026T06:00:00.000Z", "Call", "Sell", "24800", "0"),
    ]
    monkeypatch.setattr(
        processor, "get_positions", lambda self, user_id: {"Status": 200, "Success": rows, "Error": None}
    )

    ps = positions_for_underlying(processor(), _uid(), "NIFTY", "NFO")

    assert ps.available is True
    assert ps.rows == []
    assert ps.fingerprint == ""


def test_positions_for_underlying_empty_result_is_available_not_a_failure(monkeypatch):
    """No open positions is a valid answer, not a fetch failure -- must not
    trigger the D7 fallback banner."""
    monkeypatch.setattr(
        processor, "get_positions", lambda self, user_id: {"Status": 200, "Success": [], "Error": None}
    )

    ps = positions_for_underlying(processor(), _uid(), "NIFTY", "NFO")

    assert ps.available is True
    assert ps.error is None
    assert ps.rows == []


def test_positions_for_underlying_marks_unavailable_on_non_200(monkeypatch):
    monkeypatch.setattr(
        processor,
        "get_positions",
        lambda self, user_id: {"Status": 400, "Error": "Unable to connect to broker.", "Success": None},
    )

    ps = positions_for_underlying(processor(), _uid(), "NIFTY", "NFO")

    assert ps.available is False
    assert ps.error == "Unable to connect to broker."
    assert ps.rows == []


def test_positions_for_underlying_marks_unavailable_on_exception(monkeypatch):
    def _raise(self, user_id):
        raise RuntimeError("session dropped")

    monkeypatch.setattr(processor, "get_positions", _raise)

    ps = positions_for_underlying(processor(), _uid(), "NIFTY", "NFO")

    assert ps.available is False
    assert "session dropped" in ps.error


def test_fingerprint_changes_when_composition_changes(monkeypatch):
    rows_a = [_row("NIFTY", "NFO", "31-Jul-2026T06:00:00.000Z", "Call", "Sell", "24800", "50")]
    rows_b = [_row("NIFTY", "NFO", "31-Jul-2026T06:00:00.000Z", "Call", "Sell", "24800", "75")]

    monkeypatch.setattr(
        processor, "get_positions", lambda self, user_id: {"Status": 200, "Success": rows_a, "Error": None}
    )
    ps_a = positions_for_underlying(processor(), _uid(), "NIFTY", "NFO")

    monkeypatch.setattr(
        processor, "get_positions", lambda self, user_id: {"Status": 200, "Success": rows_b, "Error": None}
    )
    ps_b = positions_for_underlying(processor(), _uid(), "NIFTY", "NFO")

    assert ps_a.fingerprint != ps_b.fingerprint


def test_positions_to_margin_input_preserves_per_row_expiry():
    rows = [
        _row("NIFTY", "NFO", "31-Jul-2026T06:00:00.000Z", "Call", "Sell", "24800", "50"),
        _row("NIFTY", "NFO", "28-Aug-2026T06:00:00.000Z", "Put", "Sell", "23500", "25"),
    ]
    margin_input = positions_to_margin_input(rows)

    assert len(margin_input) == 2
    assert margin_input[0]["expiry_date"] == "31-Jul-2026T06:00:00.000Z"
    assert margin_input[1]["expiry_date"] == "28-Aug-2026T06:00:00.000Z"
    for leg in margin_input:
        assert leg["cover_order_flow"] == "N"
        assert leg["open_quantity"] == "0"


def test_existing_span_zero_when_no_positions_no_api_call():
    breeze = _FakeBreeze()
    ps = PositionSet(rows=[], fingerprint="", expiries=[], available=True)

    span = existing_span(processor(), breeze, _uid(), "NFO", ps)

    assert span == 0.0
    assert breeze.calls == []


def test_existing_span_nets_via_netted_span_for_legs():
    breeze = _FakeBreeze()
    rows = [
        _row("NIFTY", "NFO", "31-Jul-2026T06:00:00.000Z", "Call", "Sell", "24800", "50"),
        _row("NIFTY", "NFO", "28-Aug-2026T06:00:00.000Z", "Put", "Buy", "23500", "25"),
    ]
    ps = PositionSet(rows=rows, fingerprint="fp1", expiries=[], available=True)

    span = existing_span(processor(), breeze, _uid(), "NFO", ps)

    # naked = 1000*50 = 50000, hedge = 600*25 = 15000 -> 35000, one netted call.
    assert span == 35000.0
    assert len(breeze.calls) == 1
    assert breeze.calls[0] == ("NFO", 2)


def test_existing_span_none_on_broker_failure():
    class _FailingBreeze:
        def margin_calculator(self, margin_list, exchange_code="", **kwargs):
            raise RuntimeError("rate limited")

    rows = [_row("NIFTY", "NFO", "31-Jul-2026T06:00:00.000Z", "Call", "Sell", "24800", "50")]
    ps = PositionSet(rows=rows, fingerprint="fp1", expiries=[], available=True)

    span = existing_span(processor(), _FailingBreeze(), _uid(), "NFO", ps)

    assert span is None
