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


# --- measured from the first trade after the call ---------------------------------------


def _bounce_day(day: datetime.date, spike_bps: float) -> tuple[list[Bar], list[int]]:
    """Twenty-five calls, each fired on a bar that closes at the top of a spike and followed by a
    bar that OPENS back at the level before it, then nothing: the bid-ask bounce. From the firing
    close a fade collects the whole spike; from the next trade there is nothing to collect. The
    twelve-minute cycle keeps every scored horizon (1, 5, 15, 30) off the next spike."""
    start = datetime.datetime.combine(day, datetime.time(9, 15), IST).timestamp()
    bars: list[Bar] = []
    calls: list[int] = []
    level = 1000.0
    for _ in range(25):
        spike = level * (1 + spike_bps / 1e4)
        bars.append(Bar(ts=start + 60 * len(bars), close=spike, volume=1_000.0,
                        open=level, high=spike, low=level))
        calls.append(len(bars) - 1)
        for _ in range(11):
            bars.append(Bar(ts=start + 60 * len(bars), close=level, volume=1_000.0,
                            open=level, high=level, low=level))
    return bars, calls


def test_a_bounce_off_the_firing_close_is_not_scored_as_a_fade_anyone_could_trade():
    rows: list[tuple[Bar, dict]] = []
    bars: list[Bar] = []
    for k in range(40):
        day = DAY + datetime.timedelta(days=k)
        if day.weekday() >= 5:
            continue
        session, calls = _bounce_day(day, spike_bps=3.0 + (k % 5) * 0.5)
        rows.extend(_rows(session, calls, "bullish"))
        bars.extend(session)
    s = scoring.score_series(rows, duration=1, breakeven_bps=0.5, all_bars=bars).summary

    # From the price that fired it, fading looks like a finding...
    at_close = s["at_signal_close"]["best"]
    assert at_close["direction"] == "fade" and at_close["stands_out"] is True
    # ...but from the first trade after it there is nothing left, and the gap says where it went.
    assert s["entry_basis"] == "next_trade"
    assert s["tradeable"] is False
    assert s["horizons"]["15"]["fade"]["net_bps"] < 0
    assert s["mean_entry_gap_bps"] < -2.9


def test_the_entry_passes_over_minutes_with_no_trade():
    """A bar with no trade repeats the last price -- the firing close -- so it is not an entry."""
    start = datetime.datetime.combine(DAY, datetime.time(9, 15), IST).timestamp()
    bars = [
        Bar(ts=start, close=1001.0, volume=1_000.0, open=1000.0, high=1001.0, low=1000.0),
        Bar(ts=start + 60, close=1001.0, volume=0.0, open=1001.0, high=1001.0, low=1001.0),
        Bar(ts=start + 120, close=1000.5, volume=500.0, open=1000.2, high=1000.6, low=1000.1),
    ] + [Bar(ts=start + 60 * i, close=1000.5, volume=500.0, open=1000.5, high=1000.5, low=1000.5)
         for i in range(3, 40)]
    scored = scoring.score_series(_rows(bars, [0], "bullish"), duration=1, breakeven_bps=0.5,
                                  all_bars=bars)
    call = scored.calls[0]
    assert call["entry_level"] == 1000.2
    assert call["entry_time"] == "09:17:00"
    assert call["entry_gap_bps"] < 0
    # The one-minute horizon closed (09:17) before the entry bar did (09:18): no one was in it.
    assert call["move_1m_bps"] is None
    assert call["move_5m_bps"] is not None


def test_a_call_with_no_trade_in_time_is_not_scored():
    start = datetime.datetime.combine(DAY, datetime.time(9, 15), IST).timestamp()
    bars = [Bar(ts=start, close=1001.0, volume=1_000.0, open=1000.0, high=1001.0, low=1000.0)] + [
        Bar(ts=start + 60 * i, close=1001.0, volume=0.0, open=1001.0, high=1001.0, low=1001.0)
        for i in range(1, 40)
    ]
    scored = scoring.score_series(_rows(bars, [0], "bullish"), duration=1, breakeven_bps=0.5,
                                  all_bars=bars)
    assert scored.calls[0]["entry_level"] is None
    assert scored.summary["calls_without_entry"] == 1
    assert all(both["follow"]["calls"] == 0 for both in scored.summary["horizons"].values())
    # Still visible the old way, so nothing silently disappears from the comparison.
    assert scored.summary["at_signal_close"]["horizons"]["5"]["follow"]["calls"] == 1


# --- the holdout, the t >= 3 bar, and the volatility score (2026-10-09) -------------------


def _days(n):
    out, d = [], DAY
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += datetime.timedelta(days=1)
    return out


def test_a_finding_that_fades_in_the_last_third_does_not_stand_out():
    days = _days(30)
    held_from = scoring.holdout_start(days)
    assert held_from == days[20]
    # Strongly positive for twenty sessions, negative for the last ten.
    pairs = [(d, (3.0 + (i % 3) * 0.1) if i < 20 else -1.0) for i, d in enumerate(days)]
    score = scoring._horizon_score(pairs, breakeven_bps=0.5, fade=False, holdout_from=held_from)
    assert score["net_bps"] > 0 and score["t"] >= 3
    assert score["holdout_net_bps"] < 0
    assert score["stands_out"] is False and score["short_of"] == "holdout"


def test_two_standard_errors_is_no_longer_enough():
    days = _days(30)
    # A small positive edge with heavy day-to-day scatter: t between 2 and 3.
    pairs = [(d, 0.95 + (1.0 if i % 2 else -1.0)) for i, d in enumerate(days)]
    score = scoring._horizon_score(pairs, breakeven_bps=0.5, fade=False,
                                   holdout_from=scoring.holdout_start(days))
    assert 2.0 <= score["t"] < 3.0
    assert score["stands_out"] is False and score["short_of"] == "noise"


def test_calls_followed_by_big_moves_score_as_volatility_expanding():
    """Direction aside: every call is followed by a 15-minute swing far bigger than the usual
    minute of the session, so the call forecasts movement even though it says nothing of sign."""
    rows, bars = [], []
    for k, day in enumerate(_days(30)):
        start = datetime.datetime.combine(day, datetime.time(9, 15), IST).timestamp()
        # Each day starts its blocks at a different minute, so the usual move at any minute of
        # the session is mostly quiet minutes, not the calls themselves.
        closes, calls, level = [1000.0] * ((k * 7) % 55), [], 1000.0
        for block in range(6):
            for m in range(40):
                closes.append(level)
            calls.append(len(closes) - 1)
            sign = 1 if (block + k) % 2 else -1  # no direction to it
            for m in range(15):
                level *= 1 + sign * 0.0003
                closes.append(level)
        session = [Bar(ts=start + 60 * i, close=c, volume=1_000.0, oi=None, open=c, high=c, low=c)
                   for i, c in enumerate(closes)]
        rows.extend(_rows(session, calls, "bullish"))
        bars.extend(session)
    s = scoring.score_series(rows, duration=1, breakeven_bps=0.5, all_bars=bars).summary
    vol = s["volatility"]["15"]
    assert vol["mean_ratio"] > 1.5 and vol["verdict"] == "expands"
    assert s["comparisons"] == 8 and s["holdout_from"] is not None
