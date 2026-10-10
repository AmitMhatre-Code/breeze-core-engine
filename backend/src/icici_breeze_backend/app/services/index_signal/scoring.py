"""Scoring a replayed series: what each reading and each call was followed by (plan section 4).

Pure: it takes one series' replayed readings -- (bar, snapshot as the bar closed) in order -- and
the breakeven move, and returns rows for the audit files plus a summary. The level is the futures
close the readings came from; forward moves are measured on that same series, so basis drift is
the only difference from the cash index, and it is irrelevant over minutes.

The safeguards the old shadow report was built on still apply, because they are properties of the
data, not of where it came from (#33):

* **A call must pay for a trade.** Right means the index went the called way by at least the
  breakeven move (`breakeven.py`, the Trading Costs model); a smaller move is flat, not a hit.
* **Overlapping calls are one bet.** The 95% range counts only calls at least a horizon apart.
* **Raw hit rates flatter whichever way the market went.** Each side is judged against how often
  the index moved that way after *any* reading -- the trend share -- not against 50%.
* **Correlation is per day, never pooled.** Pooling sessions manufactures correlation.
* **A signal that rarely speaks cannot be judged.** Fewer than 30 separate calls a side is
  "too few calls", whatever the hit rate says.

Two things the hit-rate test cannot see, so they are scored separately (`horizons`):

* **Hit rate cannot tell "knows nothing" from "knows something, pointing the wrong way".** Both
  come back as a poor hit rate, but only one of them is worth trading -- backwards. So every
  horizon is scored in both directions: `follow` as published and `fade` against it.
* **Hit rate is not money.** A signal right 46% of the time whose wins are twice its losses pays;
  one right 54% of the time whose wins are half its losses does not. So each horizon also carries
  the average move net of the breakeven, and how big that average is against the day-to-day
  scatter (`t`) -- per day, never pooled, for the same reason the correlation is.

And one the *duration* cannot see: a call's information does not have to peak at the length of
the window that produced it. Scoring only at +d and +2d hides that, so every call is scored at
every horizon in FORWARD_MINUTES and `best` names the one that paid most.

Measured from the first trade after the call, not from the price that fired it
---------------------------------------------------------------------------------
A call is known only once its bar has closed, so nothing can be dealt at that bar's close: the
earliest price anyone acting on the call can get is the next trade. For a reversal that matters a
great deal. The close that fires an expansion call is the extreme print of a spike -- often the
ask on the way up or the bid on the way down -- and the next print usually lands back across the
spread. Measured from the firing close, that bounce reads as the market "reverting", and a fade
collects it; no bot can. The 117-session run (2026-04-01 to 09-18) put about a third of the
1-minute expansion's fifteen-minute reversal inside the first minute.

So every call is measured from the open of the first bar after it that traded (`entry_after`),
within ENTRY_WAIT_SECONDS; a call with no trade in that time is not scored. The figures measured
from the firing close are still reported beside them (`at_signal_close`), so how much of a
finding was the bounce stays visible. Readings -- the per-minute base rate and correlation -- are
still measured from each bar's own close: they are every minute, not the extremes a call picks,
so the bounce averages out of them.
"""
from __future__ import annotations

import datetime
import math
from bisect import bisect_left
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from icici_breeze_backend.app.services.index_signal import bars as bars_mod
from icici_breeze_backend.app.services.index_signal.bars import Bar
from icici_breeze_backend.app.services.index_signal.states import DIRECTIONAL

FORWARD_MINUTES: tuple[int, ...] = (1, 5, 15, 30)
MIN_SEPARATE_CALLS = 30
#: Sessions a horizon needs before its average is reported as anything but noise.
MIN_DAYS_FOR_HORIZON = 20
#: How many times its own daily scatter a horizon's net average must be to count as standing out.
#: Three, not the usual two (2026-10-09, the user's choice): a run scores about 170 cells and keeps
#: the best of each series' eight, and at two a handful would clear it by chance with no edge
#: anywhere. Three, plus the holdout below, is a bar luck rarely clears.
T_STANDS_OUT = 3.0
#: The last share of a run's sessions a finding must also pay in, measured on its own. A result
#: built in the first two-thirds and gone in the last third was the period, not the signal.
HOLDOUT_SHARE = 1.0 / 3.0
#: Horizons the volatility score reads: how big the move after a call was, whichever way.
VOL_MINUTES: tuple[int, ...] = (5, 15, 30)
_Z95 = 1.959963984540054
_TOLERANCE_SECONDS = 90.0
_MIN_DAY_READINGS_FOR_CORRELATION = 10
# The minimum move a rough rupee figure assumes an at-the-money option earns per index point.
ATM_DELTA = 0.5
#: What a call's moves are measured from: the first trade after it (see the docstring).
ENTRY_BASIS = "next_trade"
#: A call with no trade this long after it is not scored -- whatever a bot did by then was not a
#: response to this call. The same five minutes `ExpansionParams.max_gap_seconds` allows a window.
ENTRY_WAIT_SECONDS = 300.0


def wilson(p: float, n: int) -> tuple[Optional[float], Optional[float]]:
    if n <= 0:
        return None, None
    z2 = _Z95 * _Z95
    denom = 1.0 + z2 / n
    centre = (p + z2 / (2 * n)) / denom
    half = _Z95 * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def _pearson(xs: list[float], ys: list[float]) -> Optional[float]:
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / math.sqrt(sxx * syy)


def _day_mean_and_t(
    pairs: list[tuple[datetime.date, float]], bar: float
) -> tuple[Optional[float], Optional[float], int]:
    """(average of the daily averages, how many times its own scatter that average is once the
    bar is paid, sessions). Averaged per day first: a day with 40 overlapping calls is one day's
    worth of evidence, not 40 independent ones (#33)."""
    by_day: dict[datetime.date, list[float]] = {}
    for day, value in pairs:
        by_day.setdefault(day, []).append(value)
    daily = [sum(v) / len(v) for v in by_day.values()]
    n = len(daily)
    if n < 3:
        return (sum(daily) / n if n else None), None, n
    mean = sum(daily) / n
    var = sum((d - mean) ** 2 for d in daily) / (n - 1)
    if var <= 0:
        return mean, None, n
    return mean, (mean - bar) / math.sqrt(var / n), n


def holdout_start(days: Iterable[datetime.date]) -> Optional[datetime.date]:
    """The first session of a run's last third, or None for a run too short to split."""
    ordered = sorted(set(days))
    if len(ordered) < 3:
        return None
    return ordered[int(len(ordered) * (1.0 - HOLDOUT_SHARE))]


def _horizon_score(
    pairs: list[tuple[datetime.date, float]], *, breakeven_bps: float, fade: bool,
    holdout_from: Optional[datetime.date] = None,
) -> dict[str, Any]:
    """One horizon, one direction: what a trade taken on every call would have averaged.

    `net_bps` is the average move the traded way once the round trip is paid. `t` is that average
    measured against its own day-to-day scatter, so a big average built out of two lucky sessions
    does not read as a finding. `stands_out` needs it positive, outside the noise (t >= 3), and
    positive again in the run's last third on its own (`holdout_net_bps`).
    """
    signed = [(day, (-value if fade else value)) for day, value in pairs]
    mean, t, days = _day_mean_and_t(signed, breakeven_bps)
    right = sum(1 for _, v in signed if v >= breakeven_bps)
    wrong = sum(1 for _, v in signed if v <= -breakeven_bps)
    net = None if mean is None else mean - breakeven_bps
    held_mean, _held_t, held_days = (
        _day_mean_and_t([p for p in signed if p[0] >= holdout_from], breakeven_bps)
        if holdout_from is not None else (None, None, 0)
    )
    held_net = None if held_mean is None else held_mean - breakeven_bps
    held_up = held_net is not None and held_net > 0
    clears_noise = t is not None and t >= T_STANDS_OUT
    stands_out = bool(
        net is not None and net > 0 and clears_noise and held_up
        and days >= MIN_DAYS_FOR_HORIZON
    )
    return {
        "calls": len(signed),
        "days": days,
        "right": right,
        "wrong": wrong,
        "hit_rate": _r(right / (right + wrong), 4) if (right + wrong) else None,
        "mean_move_bps": _r(mean),
        "net_bps": _r(net),
        "t": _r(t),
        "holdout_net_bps": _r(held_net),
        "holdout_days": held_days,
        "stands_out": stands_out,
        "verdict": (
            "not_enough_days" if days < MIN_DAYS_FOR_HORIZON
            else "pays" if stands_out
            else "unclear" if (net is not None and net > 0)
            else "loses"
        ),
        # Why a positive result fell short: inside the noise, or gone in the last third.
        "short_of": (
            None if stands_out or net is None or net <= 0 or days < MIN_DAYS_FOR_HORIZON
            else "noise" if not clears_noise else "holdout"
        ),
    }


def _vol_score(
    ratios: list[tuple[datetime.date, float]], holdout_from: Optional[datetime.date]
) -> dict[str, Any]:
    """How big the move after a call was against the usual move at that time of day (1.0 = usual),
    averaged per day, with the same t >= 3 and last-third test a direction finding needs."""
    mean, t, days = _day_mean_and_t(ratios, 1.0)
    held_mean, _ht, held_days = (
        _day_mean_and_t([p for p in ratios if p[0] >= holdout_from], 1.0)
        if holdout_from is not None else (None, None, 0)
    )
    verdict = "not_enough_days"
    if days >= MIN_DAYS_FOR_HORIZON and mean is not None:
        if t is not None and t >= T_STANDS_OUT and held_mean is not None and held_mean > 1.0:
            verdict = "expands"
        elif t is not None and t <= -T_STANDS_OUT and held_mean is not None and held_mean < 1.0:
            verdict = "calms"
        else:
            verdict = "unclear"
    return {"calls": len(ratios), "days": days, "mean_ratio": _r(mean, 3), "t": _r(t),
            "holdout_ratio": _r(held_mean, 3), "holdout_days": held_days, "verdict": verdict}


class Levels:
    """The futures close at each bar's close, for "where was it h minutes later"."""

    def __init__(self, bars: Iterable[Bar]) -> None:
        pts = sorted((b.close_ts, b) for b in bars if bars_mod.in_session(b))
        self.times = [t for t, _ in pts]
        self.bars = [b for _, b in pts]

    def at(self, t: float) -> Optional[Bar]:
        """The bar whose close is the first at or after `t`, on the same day, within tolerance."""
        j = bisect_left(self.times, t)
        if j >= len(self.times):
            return None
        if self.times[j] - t > _TOLERANCE_SECONDS:
            return None
        if bars_mod.trading_date(self.times[j] - 1) != bars_mod.trading_date(t - 1):
            return None
        return self.bars[j]

    def forward_bps(self, t0: float, level0: float, minutes: int) -> Optional[float]:
        later = self.at(t0 + 60 * minutes)
        if later is None or not level0 > 0:
            return None
        return (later.close / level0 - 1.0) * 1e4

    def entry_after(self, t0: float) -> Optional[Bar]:
        """The first bar after `t0` that traded, on the same day, within ENTRY_WAIT_SECONDS: where
        anyone who saw a call at `t0` could first have dealt. A bar with no trade carries the last
        traded price forward, which is the firing close again, so it is passed over."""
        day = bars_mod.trading_date(t0 - 1)
        for bar in self.bars[bisect_left(self.times, t0 + 1e-6):]:
            if bar.ts - t0 > ENTRY_WAIT_SECONDS or bars_mod.trading_date(bar.ts) != day:
                return None
            if bar.volume is None or bar.volume > 0:
                return bar
        return None

    def forward_from(
        self, entry: Optional[Bar], level0: Optional[float], t0: float, minutes: int
    ) -> Optional[float]:
        """The move from an entry at `level0` on bar `entry` to the close `minutes` after `t0`.
        None when nothing traded in time, or when that horizon closed before the entry bar did."""
        if entry is None or level0 is None or not level0 > 0:
            return None
        later = self.at(t0 + 60 * minutes)
        if later is None or later.close_ts < entry.close_ts:
            return None
        return (later.close / level0 - 1.0) * 1e4

    def between(self, t0: float, t1: float) -> list[Bar]:
        """Bars closing after `t0` and up to `t1`."""
        lo = bisect_left(self.times, t0 + 1e-6)
        hi = bisect_left(self.times, t1 + 1e-6)
        return self.bars[lo:hi]


def _direction(move: Optional[float], bar_bps: float) -> Optional[str]:
    if move is None:
        return None
    if move >= bar_bps and move > 0:
        return "up"
    if move <= -bar_bps and move < 0:
        return "down"
    return "flat"


def _called(move: Optional[float], side: str) -> Optional[float]:
    if move is None:
        return None
    return move if side == "bullish" else -move


def _session_minute(bar: Bar) -> int:
    """Minutes since 09:15 at the bar's start: the time-of-day key of the volatility baseline."""
    return int((bar.ts - bars_mod.session_start_ts(bars_mod.trading_date(bar.ts))) // 60)


def _entry_level(bar: Optional[Bar]) -> Optional[float]:
    """The first trade in the entry bar: its open, or its close if the open is missing."""
    if bar is None:
        return None
    level = bar.open if bar.open is not None and bar.open > 0 else bar.close
    return float(level) if level and level > 0 else None


def _r(x: Optional[float], places: int = 2) -> Optional[float]:
    return None if x is None else round(x, places)


@dataclass
class _SideTally:
    horizon_seconds: float
    calls: int = 0
    right: int = 0
    wrong: int = 0
    flat: int = 0
    separate: int = 0
    separate_decisive: int = 0
    _last: Optional[float] = None

    def add(self, t: float, outcome: Optional[str]) -> None:
        if outcome is None:
            return
        self.calls += 1
        if outcome == "right":
            self.right += 1
        elif outcome == "wrong":
            self.wrong += 1
        else:
            self.flat += 1
        if self._last is None or t >= self._last + self.horizon_seconds:
            self._last = t
            self.separate += 1
            if outcome != "flat":
                self.separate_decisive += 1


@dataclass
class SeriesScore:
    readings: list[dict[str, Any]] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    days: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)


def _flatten_components(components: dict[str, Any]) -> dict[str, Any]:
    return {f"c_{k}": v for k, v in (components or {}).items()}


def _time(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    return bars_mod.ist(float(ts)).strftime("%H:%M:%S")


def score_series(
    rows: list[tuple[Bar, dict[str, Any]]],
    *,
    duration: int,
    breakeven_bps: float,
    all_bars: Optional[list[Bar]] = None,
    breakeven: Optional[dict[str, Any]] = None,
    sessions_in_range: Optional[list[datetime.date]] = None,
) -> SeriesScore:
    """Score one series. `rows` are the replayed readings inside the range; `all_bars` also
    carries the bars after the range's last reading of each day, which the forward moves need."""
    levels = Levels(all_bars if all_bars is not None else [b for b, _ in rows])
    horizon_s = 60.0 * duration
    out = SeriesScore()

    # -- every reading --------------------------------------------------------------------
    base = {"up": 0, "down": 0}
    # The usual size of the move from the next trade, per minute of the session and horizon:
    # the yardstick the volatility score measures each call's move against.
    usual: dict[tuple[int, int], list[float]] = {}
    states: dict[str, int] = {}
    per_day: dict[datetime.date, dict[str, Any]] = {}
    for bar, snap in rows:
        t = bar.close_ts
        day = bars_mod.trading_date(bar.ts)
        state = str(snap.get("state"))
        states[state] = states.get(state, 0) + 1
        fwd = {m: levels.forward_bps(t, bar.close, m) for m in FORWARD_MINUTES}
        fwd_d = fwd.get(duration) if duration in fwd else levels.forward_bps(t, bar.close, duration)
        entry = levels.entry_after(t)
        level0 = _entry_level(entry)
        for h in VOL_MINUTES:
            move = levels.forward_from(entry, level0, t, h)
            if move is not None:
                acc = usual.setdefault((_session_minute(bar), h), [0.0, 0])
                acc[0] += abs(move)
                acc[1] += 1
        direction = _direction(fwd_d, breakeven_bps)
        if direction in base:
            base[direction] += 1
        d = per_day.setdefault(day, {
            "first": bar.close, "last": bar.close, "readings": 0, "directional": 0,
            "strength": [], "fwd": [], "calls": 0, "right": 0, "wrong": 0,
            "excluded": False, "bars": 0,
        })
        d["last"] = bar.close
        d["readings"] += 1
        d["bars"] += 1
        if snap.get("reason") == "excluded_session":
            d["excluded"] = True
        if state in DIRECTIONAL:
            d["directional"] += 1
        strength = snap.get("signal")
        if isinstance(strength, (int, float)) and fwd_d is not None:
            d["strength"].append(float(strength))
            d["fwd"].append(fwd_d)
        row = {
            "date": day.isoformat(),
            "time": _time(t),
            "bar_open": bar.open,
            "bar_high": bar.high,
            "bar_low": bar.low,
            "bar_close": bar.close,
            "bar_volume": bar.volume,
            "bar_oi": bar.oi,
            "state": state,
            "reason": snap.get("reason"),
            "strength": _r(strength if isinstance(strength, (int, float)) else None, 4),
            "call_started": _time(snap.get("call_started_at")),
            "call_until": _time(snap.get("held_until")),
            **{f"fwd_{m}m_bps": _r(v) for m, v in fwd.items()},
            f"fwd_{duration}m_move": direction,
            **_flatten_components(snap.get("components") or {}),
        }
        out.readings.append(row)

    decisive = base["up"] + base["down"]
    trend = {
        "bullish": base["up"] / decisive if decisive else None,
        "bearish": base["down"] / decisive if decisive else None,
    }

    # -- calls ----------------------------------------------------------------------------
    tallies = {s: _SideTally(horizon_s) for s in DIRECTIONAL}
    # Every call's called-way move at every horizon, kept with its session so the averages can be
    # taken per day. A call's information need not peak at its own duration -- see the docstring.
    by_horizon: dict[int, list[tuple[datetime.date, float]]] = {h: [] for h in FORWARD_MINUTES}
    # The same, measured from the close that fired the call -- for comparison only (docstring).
    by_horizon_close: dict[int, list[tuple[datetime.date, float]]] = {h: [] for h in FORWARD_MINUTES}
    # (session, minute of session, |move|) per volatility horizon, for every call.
    vol_moves: dict[int, list[tuple[datetime.date, int, float]]] = {h: [] for h in VOL_MINUTES}
    current: Optional[dict[str, Any]] = None

    def close_call(call: dict[str, Any], end_bar: Optional[Bar], end_snap: Optional[dict[str, Any]],
                   last_bar: Bar) -> None:
        side = call["side"]
        t0, fired = call["_t0"], call["level"]
        entry = levels.entry_after(t0)
        l0 = _entry_level(entry)
        end_t = end_bar.close_ts if end_bar is not None else last_bar.close_ts
        if end_snap is None:
            how = "session_end"
        else:
            s = str(end_snap.get("state"))
            how = ("turned" if s in DIRECTIONAL else
                   "lapsed" if s == "neutral" else f"unavailable:{end_snap.get('reason')}")
        lasted = (end_t - t0) / 60.0
        move_d = _called(levels.forward_from(entry, l0, t0, duration), side)
        move_2d = _called(levels.forward_from(entry, l0, t0, 2 * duration), side)
        call_day = bars_mod.trading_date(t0 - 1)
        horizon_moves: dict[int, Optional[float]] = {}
        for h in FORWARD_MINUTES:
            at_h = _called(levels.forward_from(entry, l0, t0, h), side)
            horizon_moves[h] = at_h
            if at_h is not None:
                by_horizon[h].append((call_day, at_h))
            from_close = _called(levels.forward_bps(t0, fired, h), side)
            if from_close is not None:
                by_horizon_close[h].append((call_day, from_close))
        for h in VOL_MINUTES:
            size = levels.forward_from(entry, l0, t0, h)
            if size is not None:
                vol_moves[h].append((call_day, call["_minute"], abs(size)))
        end_level = end_bar.close if end_bar is not None else last_bar.close
        # A call that ended before anything traded was never one anybody could have been in.
        in_time = entry is not None and l0 is not None and end_t >= entry.close_ts
        move_end = _called((end_level / l0 - 1.0) * 1e4, side) if in_time else None
        path = levels.between(entry.ts, end_t) if in_time else []
        highs = [b.high if b.high is not None else b.close for b in path]
        lows = [b.low if b.low is not None else b.close for b in path]
        mfe = mae = None
        if path and l0:
            up = (max(highs) / l0 - 1.0) * 1e4
            down = (min(lows) / l0 - 1.0) * 1e4
            mfe, mae = (up, down) if side == "bullish" else (-down, -up)
        outcome = None
        if move_d is not None:
            outcome = "right" if move_d >= breakeven_bps else "wrong" if move_d <= -breakeven_bps else "flat"
        tallies[side].add(t0, outcome)
        day = per_day.get(bars_mod.trading_date(t0 - 1))
        if day is not None:
            day["calls"] += 1
            day["right"] += 1 if outcome == "right" else 0
            day["wrong"] += 1 if outcome == "wrong" else 0
        call.pop("_t0")
        call.pop("_minute", None)
        call.pop("_started", None)
        call.update({
            "entry_time": _time(entry.ts) if entry is not None else None,
            "entry_level": l0,
            # The called-way move between the firing close and the first trade after it. Negative
            # is the market already back across the spike before anyone could act: a bounce a fade
            # measured from the firing close would have collected and no bot can.
            "entry_gap_bps": _r(_called((l0 / fired - 1.0) * 1e4, side)) if l0 and fired > 0 else None,
            "ended": _time(end_t),
            "how_it_ended": how,
            "lasted_minutes": round(lasted, 1),
            "withdrawn_early": how != "lapsed" and how != "session_end" and lasted < duration,
            **{f"move_{h}m_bps": _r(v) for h, v in horizon_moves.items()},
            f"move_{duration}m_bps": _r(move_d),
            f"move_{2 * duration}m_bps": _r(move_2d),
            "move_at_end_bps": _r(move_end),
            "best_move_bps": _r(mfe),
            "worst_move_bps": _r(mae),
            f"result_{duration}m": outcome,
            "breakeven_bps": _r(breakeven_bps, 3),
            "rough_pnl_rupees": _rough_rupees(move_d, l0, breakeven),
        })
        out.calls.append(call)

    prev_bar: Optional[Bar] = None
    for bar, snap in rows:
        day = bars_mod.trading_date(bar.ts)
        # A call never runs across the night: the session's last reading ends it.
        if current is not None and prev_bar is not None and bars_mod.trading_date(prev_bar.ts) != day:
            close_call(current, None, None, prev_bar)
            current = None
        state = str(snap.get("state"))
        started = snap.get("call_started_at")
        if current is not None and not (state == current["side"] and started == current["_started"]):
            close_call(current, bar, snap, prev_bar or bar)
            current = None
        if current is None and state in DIRECTIONAL and started is not None:
            strength = snap.get("signal")
            current = {
                "date": day.isoformat(),
                "fired": _time(bar.close_ts),
                "side": state,
                "level": bar.close,
                "strength": _r(strength if isinstance(strength, (int, float)) else None, 4),
                **_flatten_components(snap.get("components") or {}),
                "_t0": bar.close_ts,
                "_minute": _session_minute(bar),
                "_started": started,
            }
        prev_bar = bar
    if current is not None and prev_bar is not None:
        close_call(current, None, None, prev_bar)

    # -- days -----------------------------------------------------------------------------
    correlations: list[float] = []
    up_days = down_days = 0
    for day, d in sorted(per_day.items()):
        corr = (_pearson(d["strength"], d["fwd"])
                if len(d["strength"]) >= _MIN_DAY_READINGS_FOR_CORRELATION else None)
        if corr is not None:
            correlations.append(corr)
        move = (d["last"] / d["first"] - 1.0) * 1e4 if d["first"] else 0.0
        kind = "up" if move > 0 else "down" if move < 0 else "flat"
        up_days += kind == "up"
        down_days += kind == "down"
        decided = d["right"] + d["wrong"]
        out.days.append({
            "date": day.isoformat(),
            "day_move_bps": round(move, 2),
            "day": kind,
            "readings": d["readings"],
            "directional_readings": d["directional"],
            "calls": d["calls"],
            "right": d["right"],
            "wrong": d["wrong"],
            "hit_rate": round(d["right"] / decided, 4) if decided else None,
            "strength_vs_next_move_correlation": _r(corr, 4),
            "excluded_rollover": d["excluded"],
        })

    # -- summary --------------------------------------------------------------------------
    sides: dict[str, dict[str, Any]] = {}
    for side, t in tallies.items():
        decided = t.right + t.wrong
        hit = t.right / decided if decided else None
        low, high = wilson(hit, t.separate_decisive) if hit is not None else (None, None)
        share = trend[side]
        if t.separate_decisive < MIN_SEPARATE_CALLS:
            verdict = "too_few_calls" if t.calls else "no_calls"
        elif share is not None and low is not None and low > share:
            verdict = "better"
        elif share is not None and high is not None and high < share:
            verdict = "worse"
        else:
            verdict = "no_edge"
        sides[side] = {
            "calls": t.calls,
            "right": t.right,
            "wrong": t.wrong,
            "flat": t.flat,
            "separate_calls": t.separate_decisive,
            "hit_rate": _r(hit, 4),
            "hit_rate_low": _r(low, 4),
            "hit_rate_high": _r(high, 4),
            "trend_share": _r(share, 4),
            "edge": _r(hit - share, 4) if hit is not None and share is not None else None,
            "verdict": verdict,
        }
    # -- every horizon, both ways round ----------------------------------------------------
    held_from = holdout_start(per_day)
    horizons: dict[str, dict[str, Any]] = {}
    for h in FORWARD_MINUTES:
        horizons[str(h)] = {
            "follow": _horizon_score(by_horizon[h], breakeven_bps=breakeven_bps, fade=False,
                                     holdout_from=held_from),
            "fade": _horizon_score(by_horizon[h], breakeven_bps=breakeven_bps, fade=True,
                                   holdout_from=held_from),
        }
    best = _best(horizons)
    horizons_close: dict[str, dict[str, Any]] = {}
    for h in FORWARD_MINUTES:
        horizons_close[str(h)] = {
            "follow": _horizon_score(by_horizon_close[h], breakeven_bps=breakeven_bps, fade=False,
                                     holdout_from=held_from),
            "fade": _horizon_score(by_horizon_close[h], breakeven_bps=breakeven_bps, fade=True,
                                   holdout_from=held_from),
        }
    volatility: dict[str, dict[str, Any]] = {}
    for h in VOL_MINUTES:
        ratios = []
        for day, minute, size in vol_moves[h]:
            acc = usual.get((minute, h))
            if acc and acc[1] and acc[0] > 0:
                ratios.append((day, size / (acc[0] / acc[1])))
        volatility[str(h)] = _vol_score(ratios, held_from)

    verdicts = [sides[s]["verdict"] for s in DIRECTIONAL]
    if "worse" in verdicts:
        overall = "worse"
    elif all(v == "better" for v in verdicts):
        overall = "edge"
    elif any(v in ("too_few_calls", "no_calls") for v in verdicts):
        overall = "too_few_calls"
    else:
        overall = "no_edge"

    def mean(key: str) -> Optional[float]:
        vals = [c[key] for c in out.calls if isinstance(c.get(key), (int, float))]
        return round(sum(vals) / len(vals), 2) if vals else None

    rupees = [c["rough_pnl_rupees"] for c in out.calls
              if isinstance(c.get("rough_pnl_rupees"), (int, float))]
    replayed_days = sorted(per_day)
    readings = len(rows)
    directional = sum(states.get(s, 0) for s in DIRECTIONAL)
    all_right = sum(t.right for t in tallies.values())
    all_decided = sum(t.right + t.wrong for t in tallies.values())
    out.summary = {
        "duration_minutes": duration,
        "sessions_replayed": len(replayed_days),
        "sessions_without_data": (
            [d.isoformat() for d in sessions_in_range if d not in per_day] if sessions_in_range else []
        ),
        "first_session": replayed_days[0].isoformat() if replayed_days else None,
        "last_session": replayed_days[-1].isoformat() if replayed_days else None,
        "up_days": up_days,
        "down_days": down_days,
        "readings": readings,
        "states": states,
        "directional_share": round(directional / readings, 4) if readings else None,
        "calls": len(out.calls),
        "bullish_calls": tallies["bullish"].calls,
        "bearish_calls": tallies["bearish"].calls,
        "right": all_right,
        "wrong": sum(t.wrong for t in tallies.values()),
        "hit_rate": round(all_right / all_decided, 4) if all_decided else None,
        "sides": sides,
        "verdict": overall,
        "horizons": horizons,
        "best": best,
        "tradeable": bool(best and best.get("stands_out")),
        # How many cells `best` was picked from: what a reader should discount it by.
        "comparisons": 2 * len(FORWARD_MINUTES),
        "holdout_from": held_from.isoformat() if held_from else None,
        # How big the move after a call was against the usual move at that time of day, either
        # way: a volatility forecast, which a premium seller can use whatever the direction.
        "volatility": volatility,
        "entry_basis": ENTRY_BASIS,
        "entry_wait_seconds": ENTRY_WAIT_SECONDS,
        "calls_without_entry": sum(1 for c in out.calls if c.get("entry_level") is None),
        "mean_entry_gap_bps": mean("entry_gap_bps"),
        # The same scores measured from the close that fired each call, which no bot can deal at
        # -- kept so the share of a finding that was the bounce stays visible (docstring).
        "at_signal_close": {"horizons": horizons_close, "best": _best(horizons_close)},
        f"mean_move_{duration}m_bps": mean(f"move_{duration}m_bps"),
        f"mean_move_{2 * duration}m_bps": mean(f"move_{2 * duration}m_bps"),
        "mean_move_at_end_bps": mean("move_at_end_bps"),
        "mean_best_move_bps": mean("best_move_bps"),
        "mean_worst_move_bps": mean("worst_move_bps"),
        "withdrawn_early": sum(1 for c in out.calls if c.get("withdrawn_early")),
        "breakeven_bps": _r(breakeven_bps, 3),
        "breakeven": breakeven or {},
        "rough_pnl_rupees": round(sum(rupees), 0) if rupees else None,
        "mean_daily_correlation": round(sum(correlations) / len(correlations), 4) if correlations else None,
        "days_with_correlation": len(correlations),
    }
    return out


def _best(horizons: dict[str, dict[str, Any]]) -> Optional[dict[str, Any]]:
    """The horizon and direction that paid most."""
    best: Optional[dict[str, Any]] = None
    for h, both in horizons.items():
        for direction, score in both.items():
            net = score.get("net_bps")
            if net is None:
                continue
            # Something that stands out always beats something that merely averaged more, so a
            # big number built on a handful of sessions cannot become the headline.
            rank = (bool(score["stands_out"]), float(net))
            if best is None or rank > best["_rank"]:
                best = {"_rank": rank, "horizon_minutes": int(h), "direction": direction, **score}
    if best is not None:
        best.pop("_rank", None)
    return best


def _rough_rupees(move_bps: Optional[float], level: float, breakeven: Optional[dict[str, Any]]) -> Optional[float]:
    """What the at-the-money option might have made on the call over its duration, at the size the
    bar is priced on (`breakeven["lots"]`): called-way index points x quantity x delta 0.5, less
    one round trip of charges and spread. Rough by construction -- no decay, a fixed delta -- and
    labelled so wherever it is shown; the bot backtests price real options."""
    if move_bps is None or not breakeven:
        return None
    quantity, cost = breakeven.get("quantity"), breakeven.get("cost_rupees")
    if not quantity:  # older summaries carried only the one-lot size
        lot = breakeven.get("lot_size")
        quantity = (lot * int(breakeven.get("lots") or 1)) if lot else None
    if not quantity or cost is None or not level > 0:
        return None
    points = move_bps / 1e4 * level
    return round(points * float(quantity) * ATM_DELTA - float(cost), 2)
