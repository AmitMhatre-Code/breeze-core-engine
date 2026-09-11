"""Tests for the index signal's pure W-OBI engine (app/services/index_signal/engine.py)."""
from __future__ import annotations

import math

import pytest

from icici_breeze_backend.app.services.index_signal.engine import (
    Book,
    Constituent,
    IndexSignalEngine,
    SignalParams,
    ewma_alpha,
    next_state,
    order_book_imbalance,
    weighted_obi,
)

A = Constituent("HDFCBANK", "HDFBAN", 60.0)
B = Constituent("RELIANCE", "RELIND", 40.0)


def _engine(constituents=(A, B), **overrides) -> IndexSignalEngine:
    params = SignalParams(
        **{
            "tau_seconds": 3.0,
            "warmup_seconds": 6.0,
            "min_coverage": 0.7,
            "book_stale_seconds": 30.0,
            **overrides,
        }
    )
    eng = IndexSignalEngine("nifty", "NSE", params)
    eng.set_constituents(list(constituents))
    return eng


def _book(eng: IndexSignalEngine, short_name: str, obi: float, ts: float) -> None:
    """Feed a book whose imbalance is exactly `obi`."""
    eng.on_book(short_name, 1000.0 * (1 + obi), 1000.0 * (1 - obi), ts)


class TestPrimitives:
    def test_order_book_imbalance(self):
        assert order_book_imbalance(300, 100) == pytest.approx(0.5)
        assert order_book_imbalance(0, 100) == pytest.approx(-1.0)

    def test_empty_book_is_no_reading_not_zero(self):
        assert order_book_imbalance(0, 0) is None

    def test_weighted_obi_renormalises_over_live_books(self):
        books = {"HDFBAN": Book(300, 100, 0.0), "RELIND": Book(100, 300, -100.0)}
        wobi, coverage, rows = weighted_obi([A, B], books, now=0.0, stale_seconds=30.0)
        # RELIND's book is 100s old: excluded, and HDFBAN alone sets the value.
        assert wobi == pytest.approx(0.5)
        assert coverage == pytest.approx(0.6)
        assert [r["obi"] for r in rows] == [pytest.approx(0.5), None]

    def test_weighted_obi_weights_by_index_weight(self):
        books = {"HDFBAN": Book(300, 100, 0.0), "RELIND": Book(100, 300, 0.0)}
        wobi, coverage, _rows = weighted_obi([A, B], books, now=0.0, stale_seconds=30.0)
        assert wobi == pytest.approx(0.6 * 0.5 + 0.4 * -0.5)
        assert coverage == pytest.approx(1.0)

    def test_empty_book_does_not_count_towards_coverage(self):
        books = {"HDFBAN": Book(0, 0, 0.0), "RELIND": Book(300, 100, 0.0)}
        wobi, coverage, _rows = weighted_obi([A, B], books, now=0.0, stale_seconds=30.0)
        assert wobi == pytest.approx(0.5)
        assert coverage == pytest.approx(0.4)

    def test_ewma_alpha(self):
        assert ewma_alpha(3.0, 3.0) == pytest.approx(1 - math.exp(-1))
        assert ewma_alpha(0.0, 3.0) == 0.0
        assert ewma_alpha(300.0, 3.0) > 0.999
        assert ewma_alpha(1.0, 0.0) == 1.0

    @pytest.mark.parametrize(
        "prev,value,expected",
        [
            ("neutral", 0.31, "bullish"),
            ("neutral", 0.29, "neutral"),
            ("neutral", -0.31, "bearish"),
            ("bullish", 0.25, "bullish"),  # inside the band: holds
            ("bullish", 0.20, "neutral"),  # at exit: releases
            ("bullish", -0.31, "bearish"),  # straight across
            ("bearish", -0.25, "bearish"),
            ("bearish", -0.19, "neutral"),
        ],
    )
    def test_hysteresis(self, prev, value, expected):
        assert next_state(prev, value, enter_threshold=0.30, exit_threshold=0.20) == expected

    def test_params_reject_exit_above_enter(self):
        with pytest.raises(ValueError):
            SignalParams(enter_threshold=0.2, exit_threshold=0.3)


class TestSmoothing:
    def test_time_constant_is_independent_of_tick_spacing(self):
        """A constant reading after a jump reaches the same value whether it arrives as one
        3s step or three 1s steps -- tau is a real time constant, not a sample count."""
        one_step = _engine(constituents=(A,), min_coverage=0.5)
        _book(one_step, "HDFBAN", 1.0, 0.0)
        _book(one_step, "HDFBAN", -1.0, 3.0)

        three_steps = _engine(constituents=(A,), min_coverage=0.5)
        _book(three_steps, "HDFBAN", 1.0, 0.0)
        for t in (1.0, 2.0, 3.0):
            _book(three_steps, "HDFBAN", -1.0, t)

        expected = -1.0 + 2.0 * math.exp(-1.0)
        assert one_step.snapshot(3.0, session_open=True)["signal"] == pytest.approx(expected, abs=1e-4)
        assert three_steps.snapshot(3.0, session_open=True)["signal"] == pytest.approx(expected, abs=1e-4)

    def test_partial_picture_is_not_folded_in(self):
        eng = _engine()
        _book(eng, "RELIND", 1.0, 0.0)  # 40% of weight: below the 70% floor
        snap = eng.snapshot(0.0, session_open=True)
        assert snap["signal"] is None
        assert snap["reason"] == "low_coverage"


class TestStates:
    def test_no_constituents(self):
        eng = IndexSignalEngine("nifty", "NSE", SignalParams())
        assert eng.snapshot(0.0, session_open=True)["reason"] == "no_constituents"

    def test_market_closed(self):
        eng = _engine()
        _book(eng, "HDFBAN", 0.5, 0.0)
        _book(eng, "RELIND", 0.5, 0.0)
        snap = eng.snapshot(1.0, session_open=False)
        assert snap["state"] == "unavailable"
        assert snap["reason"] == "market_closed"

    def test_warms_up_then_takes_a_side(self):
        eng = _engine()
        _book(eng, "HDFBAN", 0.5, 0.0)
        _book(eng, "RELIND", 0.5, 0.0)
        first = eng.snapshot(1.0, session_open=True)
        assert (first["state"], first["reason"]) == ("unavailable", "warming_up")
        later = eng.snapshot(7.5, session_open=True)
        assert later["state"] == "bullish"
        assert later["reason"] is None
        assert later["signal"] == pytest.approx(0.5)

    def test_losing_coverage_is_unavailable_never_neutral_and_rearms_warmup(self):
        eng = _engine()
        _book(eng, "HDFBAN", -0.5, 0.0)
        _book(eng, "RELIND", -0.5, 0.0)
        eng.snapshot(1.0, session_open=True)
        assert eng.snapshot(7.5, session_open=True)["state"] == "bearish"

        gone = eng.snapshot(40.0, session_open=True)  # both books now older than 30s
        assert (gone["state"], gone["reason"]) == ("unavailable", "low_coverage")

        _book(eng, "HDFBAN", -0.5, 41.0)
        _book(eng, "RELIND", -0.5, 41.0)
        back = eng.snapshot(41.0, session_open=True)
        assert (back["state"], back["reason"]) == ("unavailable", "warming_up")

    def test_weight_change_keeps_the_smoother_but_a_name_change_resets_it(self):
        eng = _engine()
        _book(eng, "HDFBAN", 0.5, 0.0)
        _book(eng, "RELIND", 0.5, 0.0)
        eng.snapshot(1.0, session_open=True)
        assert eng.snapshot(7.5, session_open=True)["state"] == "bullish"

        reweighted = (Constituent("HDFCBANK", "HDFBAN", 70.0), Constituent("RELIANCE", "RELIND", 30.0))
        assert eng.set_constituents(reweighted) is False
        assert eng.snapshot(8.0, session_open=True)["state"] == "bullish"

        assert eng.set_constituents([A]) is True
        snap = eng.snapshot(8.5, session_open=True)
        assert (snap["state"], snap["reason"]) == ("unavailable", "warming_up")

    def test_untracked_books_are_ignored(self):
        eng = _engine()
        eng.on_book("TCS", 900, 100, 0.0)
        snap = eng.snapshot(0.0, session_open=True)
        assert snap["reason"] == "low_coverage"
        assert all(row["bid_qty"] is None for row in snap["constituents"])
