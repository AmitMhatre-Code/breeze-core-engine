"""The order-flow challengers (index_signal.flow): pre-registered 2026-09-13, shadow-only."""
from __future__ import annotations

import pytest

from icici_breeze_backend.app.services.index_signal import breakeven, depth_feed, flow, shadow_log
from icici_breeze_backend.app.services.index_signal.engine import Constituent

# 2027-01-15 13:30:00 IST -- one IST trading day for every tick below.
B = 1_800_000_000.0


# --- order-flow imbalance ---------------------------------------------------------------


def test_unchanged_prices_score_the_change_in_queue_sizes():
    prev = flow.Top(100.0, 500, 100.05, 300)
    assert flow.ofi_step(prev, flow.Top(100.0, 700, 100.05, 250)) == (700 - 500) - (250 - 300)


def test_a_bid_that_steps_up_scores_its_whole_new_queue():
    prev = flow.Top(100.0, 500, 100.05, 300)
    assert flow.ofi_step(prev, flow.Top(100.02, 40, 100.05, 300)) == 40


def test_a_bid_that_drops_scores_minus_the_whole_previous_queue():
    """The case an externally reviewed spec scored as 0: the best bid was eaten or pulled."""
    prev = flow.Top(100.0, 500, 100.05, 300)
    assert flow.ofi_step(prev, flow.Top(99.95, 200, 100.05, 300)) == -500


def test_an_offer_that_steps_down_is_selling_pressure():
    prev = flow.Top(100.0, 500, 100.05, 300)
    assert flow.ofi_step(prev, flow.Top(100.0, 500, 100.03, 80)) == -80


def test_aggressor_side_from_the_last_price_against_the_previous_quote():
    prev = flow.Top(100.0, 1, 100.10, 1)
    assert flow.aggressor_step(prev, 100.10, 65) == 65  # lifted the offer
    assert flow.aggressor_step(prev, 100.00, 65) == -65  # hit the bid
    assert flow.aggressor_step(prev, 100.07, 65) == 65  # above the mid
    assert flow.aggressor_step(prev, 100.05, 65) == 0.0  # at the mid: no side
    assert flow.aggressor_step(prev, 100.10, 0) == 0.0  # nothing traded


def test_futures_quotes_parse_and_an_empty_side_is_no_quote():
    top, last, ttq = flow.parse_futures_quote(
        {"bPrice": "23500.1", "bQty": "650", "sPrice": "23500.6", "sQty": 1300, "last": 23500.6, "ttq": 99}
    )
    assert top == flow.Top(23500.1, 650.0, 23500.6, 1300.0) and last == 23500.6 and ttq == 99.0
    assert flow.parse_futures_quote({"bPrice": 0, "bQty": 0, "sPrice": 10, "sQty": 5})[0] is None


def test_the_flow_ratio_is_bounded_and_a_gap_lets_new_flow_dominate():
    r = flow.FlowRatio(30.0)
    r.add(1000, 0.0)
    r.add(-10, 1.0)
    assert 0.9 < r.value() <= 1.0
    r.add(-50, 3600.0)  # an hour later the old flow has all but decayed away
    assert r.value() < -0.99


# --- the engines ----------------------------------------------------------------------


def _basket():
    return [Constituent("HDFCBANK", "HDFBAN", 50.0), Constituent("ICICIBANK", "ICIBAN", 50.0)]


def _stream_rising_bids(engine, names, start, end, step=5.0):
    """Bids stacking up at an unchanged price: steady buying pressure. Sizes follow the clock,
    so a second call continues the same stream instead of starting a shrinking queue."""
    t = start
    while t <= end:
        for name in names:
            engine.on_top(name, flow.Top(1000.0, 100.0 + 2 * t, 1000.05, 100.0), B + t)
        t += step


def test_constituent_flow_warms_up_then_reads_buying_pressure():
    eng = flow.FlowEngine("sensex", flow.KIND_CONSTITUENTS)
    eng.set_constituents(_basket())
    _stream_rising_bids(eng, ("HDFBAN", "ICIBAN"), 0, 10)
    first = eng.snapshot(B + 10, session_open=True)
    assert (first["state"], first["reason"]) == ("unavailable", "warming_up")
    _stream_rising_bids(eng, ("HDFBAN", "ICIBAN"), 15, 80)
    later = eng.snapshot(B + 80, session_open=True)
    assert later["state"] == "bullish" and later["label"] == "sensex:flow"
    assert later["signal"] == pytest.approx(1.0)


def test_constituent_flow_needs_the_coverage_floor():
    eng = flow.FlowEngine("sensex", flow.KIND_CONSTITUENTS)
    eng.set_constituents(_basket())
    _stream_rising_bids(eng, ("HDFBAN",), 0, 80)  # half the weight
    snap = eng.snapshot(B + 80, session_open=True)
    assert (snap["state"], snap["reason"]) == ("unavailable", "low_coverage")
    assert snap["coverage"] == pytest.approx(0.5)


def test_a_closed_market_or_empty_basket_is_unavailable_never_neutral():
    eng = flow.FlowEngine("sensex", flow.KIND_CONSTITUENTS)
    assert eng.snapshot(B, session_open=True)["reason"] == "no_constituents"
    eng.set_constituents(_basket())
    assert eng.snapshot(B, session_open=False)["reason"] == "market_closed"


def _futures_ticks(engine, start, end, *, sell=False, step=2.0):
    """Queues and the traded counter follow the clock, so consecutive calls are one stream."""
    t = start
    while t <= end:
        grow = 100.0 + 10 * t
        bid_qty, ask_qty = (100.0, grow) if sell else (grow, 100.0)
        top = flow.Top(23500.0, bid_qty, 23500.5, ask_qty)
        last = 23500.0 if sell else 23500.5
        engine.on_futures_quote(top, last, 1000.0 + 30 * t, B + t)
        t += step


def test_futures_pressure_reads_both_queues_and_the_tape():
    eng = flow.FlowEngine("nifty", flow.KIND_FUTURES)
    _futures_ticks(eng, 0, 4)
    eng.snapshot(B + 4, session_open=True)
    _futures_ticks(eng, 6, 80)
    snap = eng.snapshot(B + 80, session_open=True)
    assert snap["state"] == "bullish"
    assert snap["components"]["order_flow"] > 0.9 and snap["components"]["aggressor"] == pytest.approx(1.0)


def test_futures_pressure_selling():
    eng = flow.FlowEngine("nifty", flow.KIND_FUTURES)
    _futures_ticks(eng, 0, 4, sell=True)
    eng.snapshot(B + 4, session_open=True)
    _futures_ticks(eng, 6, 80, sell=True)
    assert eng.snapshot(B + 80, session_open=True)["state"] == "bearish"


def test_futures_pressure_without_a_tape_has_no_reading():
    eng = flow.FlowEngine("nifty", flow.KIND_FUTURES)
    for k in range(40):
        eng.on_futures_quote(flow.Top(23500.0, 100.0 + k, 23500.5, 100.0), None, None, B + 2 * k)
    assert eng.snapshot(B + 80, session_open=True)["reason"] == "low_coverage"


def test_a_stale_feed_drops_the_reading():
    eng = flow.FlowEngine("nifty", flow.KIND_FUTURES)
    _futures_ticks(eng, 0, 80)
    eng.snapshot(B + 5, session_open=True)
    assert eng.snapshot(B + 80 + flow.STALE_SECONDS + 1, session_open=True)["state"] == "unavailable"


# --- plumbing ---------------------------------------------------------------------------


def test_depth_top_reads_level_one_from_either_exchange_layout():
    nse = [{"BestBuyRate-1": "1000.00", "BestBuyQty-1": "10", "BuyNoOfOrders-1": "3",
            "BestSellRate-1": "1000.05", "BestSellQty-1": "7"},
           {"BestBuyRate-2": "999.95", "BestBuyQty-2": "99"}]
    assert depth_feed.depth_top(nse) == (1000.0, 10.0, 1000.05, 7.0)
    assert depth_feed.depth_top([{"BestBuyRate-1": "1", "BestBuyQty-1": "1"}]) is None
    assert depth_feed.depth_top("garbage") is None


def test_the_futures_feed_hands_its_quote_ticks_to_an_observer():
    from icici_breeze_backend.app.services.bots.scalping.futures_feed import NiftyFuturesFeed

    feed = NiftyFuturesFeed()
    feed._token_symbol = "4.1!68407"
    seen = []
    feed.set_quote_observer(lambda payload, ts: seen.append(payload["bPrice"]))
    feed._on_raw_tick({"symbol": "4.1!68407", "last": 23500.5, "ttq": 10, "bPrice": 23500.0})
    feed._on_raw_tick({"symbol": "4.1!99999", "last": 1.0, "bPrice": 1.0})  # not our contract
    assert seen == [23500.0]

    def _boom(payload, ts):
        raise RuntimeError("challenger bug")

    feed.set_quote_observer(_boom)
    feed._on_raw_tick({"symbol": "4.1!68407", "last": 23500.5, "ttq": 11})  # must not raise
    assert feed.builder is not None


def test_challenger_readiness_prices_breakeven_from_its_index(tmp_path, monkeypatch):
    seen = []

    def _be(label, level):
        seen.append(label)
        return {"bps": 1.0}

    monkeypatch.setattr(breakeven, "breakeven", _be)
    shadow_log.reset_state_for_tests()
    out = shadow_log.readiness("nifty:flow", db_path=str(tmp_path / "u.sqlite3"), now=B)
    assert seen == ["nifty"] and out["label"] == "nifty:flow" and out["status"] == "too_early"
    assert ":" not in shadow_log.readings_filename("nifty:flow", 5, now=B)
