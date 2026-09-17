"""The netted-margin cache is scoped to one IST trading day.

Its key used to carry no date and lived a rolling 24h, so a book carried overnight kept
yesterday's `span_margin_required`. On 2026-09-17 (SENSEX expiry) Portfolio showed Rs 3.63Cr
while ICICI blocked Rs 5.15Cr: the cached figure predated the expiry-day ELM, and the
Portfolio ELM overlay is zeroed on expiry day because today's ICICI figure already includes it.
Every consumer of `_netted_span_for_legs` read the same stale value -- Portfolio group and
portfolio totals, Strategy Builder's portfolio-aware M(existing), and the shorts scans.
"""
from __future__ import annotations

import datetime
import uuid

import icici_breeze_backend.app.services.processor as proc_mod
from icici_breeze_backend.app.core.timezone import IST
from icici_breeze_backend.app.services.portfolio_margin_netting import PositionSet, existing_span
from icici_breeze_backend.app.services.processor import processor


class _Breeze:
    """Returns a different SPAN on each call, so a cache hit is visible."""

    def __init__(self, spans):
        self.spans = list(spans)
        self.calls = 0

    def margin_calculator(self, margin_list, exchange_code="", **kwargs):
        span = self.spans[self.calls]
        self.calls += 1
        return {"Status": 200, "Success": {"span_margin_required": span}, "Error": None}


def _legs():
    return [
        {
            "stock_code": "BSESEN",
            "exchange_code": "BFO",
            "expiry_date": "17-Sep-2026",
            "product_type": "Options",
            "right": "Put",
            "action": "Sell",
            "strike_price": "70700",
            "quantity": "5300",
        },
        {
            "stock_code": "BSESEN",
            "exchange_code": "BFO",
            "expiry_date": "17-Sep-2026",
            "product_type": "Options",
            "right": "Call",
            "action": "Sell",
            "strike_price": "79900",
            "quantity": "5300",
        },
    ]


def _on(monkeypatch, day: datetime.date) -> None:
    monkeypatch.setattr(proc_mod, "today_ist_date", lambda: day)


def test_unchanged_book_reuses_the_figure_within_the_day(monkeypatch):
    _on(monkeypatch, datetime.date(2026, 9, 16))
    user = "daycache-" + uuid.uuid4().hex
    breeze = _Breeze([36_300_000.0, 99.0])
    p = processor()

    assert p._netted_span_for_legs(breeze, user, "BFO", _legs()) == 36_300_000.0
    assert p._netted_span_for_legs(breeze, user, "BFO", _legs()) == 36_300_000.0
    assert breeze.calls == 1


def test_next_trading_day_recomputes_the_same_book(monkeypatch):
    """The regression: the expiry-day figure must replace yesterday's."""
    user = "daycache-" + uuid.uuid4().hex
    breeze = _Breeze([36_300_000.0, 51_540_520.64])
    p = processor()

    _on(monkeypatch, datetime.date(2026, 9, 16))
    assert p._netted_span_for_legs(breeze, user, "BFO", _legs()) == 36_300_000.0

    _on(monkeypatch, datetime.date(2026, 9, 17))
    assert p._netted_span_for_legs(breeze, user, "BFO", _legs()) == 51_540_520.64
    assert breeze.calls == 2


def test_strategy_builder_existing_span_is_day_scoped_too(monkeypatch):
    """M(existing) goes through the same cache; a stale one skews incremental margin."""
    user = "daycache-" + uuid.uuid4().hex
    breeze = _Breeze([36_300_000.0, 51_540_520.64])
    ps = PositionSet(rows=_legs(), fingerprint="fp", expiries=[], available=True)

    _on(monkeypatch, datetime.date(2026, 9, 16))
    assert existing_span(processor(), breeze, user, "BFO", ps) == 36_300_000.0
    _on(monkeypatch, datetime.date(2026, 9, 17))
    assert existing_span(processor(), breeze, user, "BFO", ps) == 51_540_520.64


def test_cache_key_carries_the_ist_date(monkeypatch):
    _on(monkeypatch, datetime.date(2026, 9, 17))
    key = proc_mod._portfolio_netted_cache_key("u1", "BFO", _legs())
    assert ":2026-09-17:" in key


def test_entry_expires_at_ist_midnight(monkeypatch):
    monkeypatch.setattr(
        proc_mod, "now_ist", lambda: datetime.datetime(2026, 9, 16, 23, 59, 30, tzinfo=IST)
    )
    assert proc_mod._portfolio_margin_cache_ttl_seconds() == 30

    monkeypatch.setattr(
        proc_mod, "now_ist", lambda: datetime.datetime(2026, 9, 17, 0, 0, 0, tzinfo=IST)
    )
    assert proc_mod._portfolio_margin_cache_ttl_seconds() == 24 * 60 * 60
