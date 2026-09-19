"""One series' state, the same object live and in replay (docs/signals-streamline-plan.md section 3).

A `SeriesEngine` is fed one-minute bars and asked for snapshots. It owns what the mechanisms do
not: how long a call stands, when a reading is unavailable, and the shape of the payload every
consumer reads. The live publisher feeds it bars built from ticks and snapshots it at the wall
clock; `replay_series` feeds it bars from ICICI history and snapshots it as each bar closes. Both
go through this class, so a live reading and its replay can only differ where the bars differ.

A call's lifetime
-----------------
A call fired as a bar (or candle) closes at time C stands until C + duration. Re-firing the same
side before then extends it and keeps its start (`call_started_at`): a bot trades one call once,
however many times it re-fires. A different side starts a new call. Quiet evaluations inside the
duration do not end a call -- the duration is the horizon the call is about.

Unavailable, never neutral
--------------------------
Outside 09:15-15:15, on an excluded session, before today's first whole reading, or with no bar
for three minutes, the state is `unavailable` with its reason (#30). A call held into any of
those is dropped: not knowing is not a reading.
"""
from __future__ import annotations

import datetime
import threading
from typing import Any, Iterable, Iterator, Optional

from icici_breeze_backend.app.services.index_signal import bars as bars_mod
from icici_breeze_backend.app.services.index_signal.bars import Bar
from icici_breeze_backend.app.services.index_signal.mechanisms import SeriesKey, make_evaluator
from icici_breeze_backend.app.services.index_signal.states import (
    DIRECTIONAL,
    NO_READING_REASONS,
    REASON_EXCLUDED_SESSION,
    REASON_MARKET_CLOSED,
    REASON_NO_BARS,
    REASON_OUTSIDE_SESSION,
    REASON_STALE,
    REASON_WARMING_UP,
    Evaluation,
)

# No bar for this long means the feed has stopped: there is no reading, not an old one.
STALE_SECONDS = 180.0

_FLIP = {"bullish": "bearish", "bearish": "bullish"}


class SeriesEngine:
    """Thread-safe: bars arrive on the socket thread, snapshots on the publisher's."""

    def __init__(self, key: SeriesKey) -> None:
        self.key = key
        self._lock = threading.Lock()
        self._evaluator = make_evaluator(key)
        self._last: Optional[Evaluation] = None
        self._last_at: Optional[float] = None  # close time of the bar that produced `_last`
        self._last_bar_ts: Optional[float] = None  # start of the latest in-session bar
        self._last_fed_ts: Optional[float] = None  # start of the latest bar of any kind
        self._held: Optional[str] = None
        self._held_until = 0.0
        self._call_started: Optional[float] = None

    def on_bar(self, bar: Bar) -> None:
        with self._lock:
            if self._last_fed_ts is not None and bar.ts <= self._last_fed_ts:
                return  # out of order or a duplicate (a restart re-seeding today's bars)
            self._last_fed_ts = bar.ts
            if bars_mod.in_session(bar):
                self._last_bar_ts = bar.ts
            evaluation = self._evaluator.on_bar(bar)
            if evaluation is None:
                return
            at = bar.close_ts
            self._last, self._last_at = evaluation, at
            if evaluation.side in DIRECTIONAL:
                # A re-fire AT the lapse moment extends the call: a momentum candle is judged
                # exactly when the previous one's call would end, and consecutive firing candles
                # are one run (one trade), not a new call every candle.
                if evaluation.side != self._held or at > self._held_until:
                    self._call_started = at
                self._held = evaluation.side
                self._held_until = at + self.key.duration * 60.0

    def seed(self, bars: Iterable[Bar]) -> None:
        for bar in bars:
            self.on_bar(bar)

    def _drop_call(self) -> None:
        self._held, self._held_until, self._call_started = None, 0.0, None

    def snapshot(self, now: float, *, session_open: bool = True, excluded: bool = False) -> dict[str, Any]:
        with self._lock:
            last = self._last
            reason: Optional[str] = last.reason if last is not None else REASON_WARMING_UP
            today = bars_mod.trading_date(now)
            if not session_open:
                reason = REASON_MARKET_CLOSED
            elif not bars_mod.reading_window_open(now):
                reason = REASON_OUTSIDE_SESSION
            elif excluded and self.key.uses_oi:
                reason = REASON_EXCLUDED_SESSION
            elif self._last_bar_ts is None or bars_mod.trading_date(self._last_bar_ts) != today:
                reason = REASON_NO_BARS
            elif now - (self._last_bar_ts + bars_mod.BAR_SECONDS) > STALE_SECONDS:
                reason = REASON_STALE
            elif self._last_at is None or bars_mod.trading_date(self._last_at - 1) != today:
                # Nothing evaluated today yet (a 15-minute candle has not closed): yesterday's
                # verdict is not today's reading.
                reason = REASON_WARMING_UP

            if reason in NO_READING_REASONS:
                self._drop_call()
                state = "unavailable"
            elif self._held is not None and now < self._held_until:
                state, reason = self._held, None
            else:
                self._drop_call()
                state = "neutral"

            components = dict(last.components) if last is not None else {}
            return {
                "key": self.key.id,
                "index": self.key.index,
                "label": self.key.index,
                "mechanism": self.key.mechanism,
                "duration_minutes": self.key.duration,
                "version": self.key.version,
                "uses_oi": self.key.uses_oi,
                "thin_data": self.key.thin_data,
                "state": state,
                "reason": reason,
                "signal": last.strength if last is not None else None,
                "components": components,
                "call_started_at": self._call_started if state in DIRECTIONAL else None,
                "held_until": self._held_until if state in DIRECTIONAL else None,
                "evaluated_at": self._last_at,
                "bar_ts": self._last_bar_ts,
                "computed_at": now,
            }


def apply_direction(payload: dict[str, Any], direction: str) -> dict[str, Any]:
    """The reading a bot trades: `follow` as published, `fade` with the side swapped and the
    strength negated. `neutral` and `unavailable` pass through -- an unreadable signal is never a
    trade, whichever way the bot leans (#30)."""
    out = dict(payload)
    out["direction"] = direction
    out["source_state"] = payload.get("state")
    if direction == "fade" and payload.get("state") in _FLIP:
        out["state"] = _FLIP[payload["state"]]
        if isinstance(payload.get("signal"), (int, float)):
            out["signal"] = -float(payload["signal"])
    return out


def replay_series(
    bars: Iterable[Bar],
    key: SeriesKey,
    *,
    excluded_days: frozenset[datetime.date] | set[datetime.date] = frozenset(),
) -> Iterator[tuple[Bar, dict[str, Any]]]:
    """(bar, snapshot as that bar closed) for every in-session bar, in order.

    The one replay loop: the signal backtest scores these, and the bot backtests act on them, so a
    bot replay trades exactly the calls the signal replay reports. Pass bars from before the
    range too -- they warm the baseline exactly as the live warm-up fetch does."""
    engine = SeriesEngine(key)
    for bar in bars:
        engine.on_bar(bar)
        if not bars_mod.in_session(bar):
            continue
        day = bars_mod.trading_date(bar.ts)
        yield bar, engine.snapshot(bar.close_ts, excluded=day in excluded_days)


def rollover_days(
    start: datetime.date, end: datetime.date, holidays: Optional[set[datetime.date]] = None
) -> set[datetime.date]:
    """The days whose NIFTY OI moves for mechanical reasons (near-month futures rolling), which a
    reading that uses OI must not speak on (#34). Covers `start`..`end` inclusive."""
    from icici_breeze_backend.app.services.bots.scalping import backtest_regime as regime
    from icici_breeze_backend.app.services.index_signal.expansion import in_rollover_window

    out: set[datetime.date] = set()
    day = start
    while day <= end:
        try:
            expiry = regime.near_month_futures_expiry(day, "NIFTY", holidays or set())
            if in_rollover_window(day, expiry):
                out.add(day)
        except Exception:  # noqa: BLE001 -- a calendar gap costs an exclusion, never the replay
            pass
        day += datetime.timedelta(days=1)
    return out
