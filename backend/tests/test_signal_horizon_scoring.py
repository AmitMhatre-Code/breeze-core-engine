"""Scoring a series at every horizon, in both directions, against a size-aware cost bar.

The three defects these cover, all of which the old hit-rate-only scorer hid:

* a signal that is reliably WRONG reads as a failure rather than as something to trade backwards;
* a signal's information need not peak at the length of the window that produced it, so scoring
  only at the call's own duration can miss it entirely;
* a hit rate is not money, and a cost bar priced on one lot is not the cost a real position pays.
"""
from __future__ import annotations

import datetime

from icici_breeze_backend.app.core.timezone import IST
from icici_breeze_backend.app.services.index_signal import scoring
from icici_breeze_backend.app.services.index_signal.bars import Bar

DAY = datetime.date(2026, 3, 9)


def _session(day: datetime.date, closes: list[float]) -> list[Bar]:
    """One bar a minute from 09:15, closing at the given prices."""
    start = datetime.datetime.combine(day, datetime.time(9, 15), IST).timestamp()
    return [Bar(ts=start + 60 * i, close=c, volume=1_000.0, oi=None,
                open=c, high=c, low=c) for i, c in enumerate(closes)]


def _rows(bars: list[Bar], call_at: list[int], side: str) -> list[tuple[Bar, dict]]:
    """A reading per bar: a one-minute call on each bar in `call_at`, quiet otherwise."""
    out = []
    for i, bar in enumerate(bars):
        if i in call_at:
            out.append((bar, {"state": side, "reason": None, "signal": 0.9,
                              "call_started_at": bar.close_ts, "held_until": bar.close_ts + 60,
                              "components": {}}))
        else:
            out.append((bar, {"state": "neutral", "reason": "no_expansion", "signal": 0.1,
                              "components": {}}))
    return out


def _reverting_day(seed: int) -> tuple[list[float], list[int]]:
    """Every call is followed by a small move the called way and a bigger one against it: the
    shape the real NIFTY and SENSEX expansion runs have, where following loses and fading pays,
    and where the payoff only shows up well after the call's own minute.

    `seed` varies how strongly a session reverts, so the sessions are not identical. Without that
    there is no day-to-day scatter to measure an average against, and a scorer that reported a
    finding anyway would be reporting one it could not possibly have evidence for."""
    revert = 0.99994 - 0.00002 * ((seed * 7) % 5)
    closes = [1000.0]
    calls = []
    for _ in range(40):
        calls.append(len(closes) - 1)
        closes.append(closes[-1] * 1.0002)          # +2 bps in the called minute
        for _ in range(14):
            closes.append(closes[-1] * revert)      # then drifts back over the next 14
    return closes, calls


def _multi_day_rows(days: int):
    rows: list[tuple[Bar, dict]] = []
    bars: list[Bar] = []
    for k in range(days):
        day = DAY + datetime.timedelta(days=k)
        if day.weekday() >= 5:
            continue
        closes, calls = _reverting_day(k)
        session = _session(day, closes)
        rows.extend(_rows(session, calls, "bullish"))
        bars.extend(session)
    return rows, bars


def test_a_signal_that_is_reliably_wrong_is_reported_as_a_fade_not_a_failure():
    rows, bars = _multi_day_rows(40)
    s = scoring.score_series(rows, duration=1, breakeven_bps=0.5, all_bars=bars).summary
    best = s["best"]
    assert best["direction"] == "fade"
    assert best["net_bps"] > 0
    assert s["tradeable"] is True
    # And following it is reported as losing at the same horizon, not left unsaid.
    same = s["horizons"][str(best["horizon_minutes"])]
    assert same["follow"]["net_bps"] < 0
    assert same["follow"]["verdict"] == "loses"


def test_the_best_horizon_need_not_be_the_calls_own_duration():
    """The whole point: a one-minute call whose information is about the next fifteen minutes."""
    rows, bars = _multi_day_rows(40)
    s = scoring.score_series(rows, duration=1, breakeven_bps=0.5, all_bars=bars).summary
    assert s["best"]["horizon_minutes"] > 1
    assert s["horizons"]["1"]["fade"]["net_bps"] < s["best"]["net_bps"]


def test_follow_and_fade_differ_by_exactly_two_round_trips():
    """A sanity check on the arithmetic: both directions pay the bar, so their net figures sum
    to minus twice it. If this drifts, one of them has stopped paying costs."""
    rows, bars = _multi_day_rows(40)
    bar = 0.5
    s = scoring.score_series(rows, duration=1, breakeven_bps=bar, all_bars=bars).summary
    for both in s["horizons"].values():
        if both["follow"]["net_bps"] is None:
            continue
        assert abs(both["follow"]["net_bps"] + both["fade"]["net_bps"] + 2 * bar) < 0.02


def test_a_big_average_built_on_a_handful_of_sessions_does_not_read_as_a_finding():
    """`stands_out` needs both a positive average and enough sessions; a few lucky days is how a
    backtest talks itself into a strategy."""
    rows, bars = _multi_day_rows(4)
    s = scoring.score_series(rows, duration=1, breakeven_bps=0.5, all_bars=bars).summary
    assert s["tradeable"] is False
    for both in s["horizons"].values():
        for score in both.values():
            assert score["verdict"] == "not_enough_days"


def test_the_days_are_averaged_before_the_average_is_taken():
    """Forty overlapping calls in one session are one day's worth of evidence, not forty."""
    rows, bars = _multi_day_rows(40)
    s = scoring.score_series(rows, duration=1, breakeven_bps=0.5, all_bars=bars).summary
    for both in s["horizons"].values():
        for score in both.values():
            assert score["days"] < score["calls"]
