"""Option prices for the replays: real ICICI candles, or Black-Scholes (plan section 8.7).

Two pricers behind one interface, so every replay runs either way and says which it used:

* **Real** reads the traded 1-minute (and, inside a Bot 3 trade, 1-second) bars ICICI serves
  for the exact contract. These are *traded* prices, not bid/ask -- the book is not in the
  history -- so the spread is still modelled from what paper mode has observed.
* **Model** is the original harness: Black-Scholes off spot and that day's India VIX.

The two are never mixed inside one run (decided 2026-09-13). A contract the cache has not
fetched stops the day and becomes a *need*; a contract ICICI returned nothing for, or a minute
with no trade in it, is counted and skipped. A missing price is never back-filled from the
model, because a total built from both would be evidence of neither.
"""
from __future__ import annotations

import datetime
from typing import Optional

from icici_breeze_backend.app.services.bots.scalping import backtest_store as store
from icici_breeze_backend.app.services.bots.scalping.backtest_store import (
    HistCandle,
    Need,
    OptionKey,
)
from icici_breeze_backend.app.services.iv_compute import bs_price_call, bs_price_put

OK = "ok"
# Never fetched: the replay cannot go on without it, so the day stops and it becomes a need.
MISSING = "missing"
# Fetched, and ICICI returned nothing for the contract that day.
NO_DATA = "no_data"
# The contract has data, but nothing traded in this minute -- no fill is possible.
NO_TRADE = "no_trade"

SESSION_OPEN = datetime.time(9, 15)
SESSION_CLOSE = datetime.time(15, 30)

# How long a 1-second window reaches past a Bot 3 entry. 15 minutes is 900 bars, under the
# ~1,000-candle per-call cap, and far past the 90-second time stop; a runner that outlives it
# continues on 1-minute bars.
SECOND_WINDOW = datetime.timedelta(minutes=15)


def session_bounds(day: datetime.date) -> tuple[datetime.datetime, datetime.datetime]:
    return (
        datetime.datetime.combine(day, SESSION_OPEN),
        datetime.datetime.combine(day, SESSION_CLOSE),
    )


def years_to_expiry(now: datetime.datetime, expiry: datetime.date) -> float:
    """Calendar-day time to expiry, floored so an expiry-day option is never worthless."""
    days = (expiry - now.date()).days
    remaining_minutes = max(0.0, (15 * 60 + 30) - (now.hour * 60 + now.minute))
    total_days = days + remaining_minutes / (24 * 60.0)
    return max(total_days / 365.0, 1.0 / (365.0 * 24 * 60))


def theoretical_price(spot: float, strike: float, right: str, t: float, sigma: float) -> float:
    fn = bs_price_call if right == "call" else bs_price_put
    return max(0.05, round(fn(spot, strike, t, sigma), 2))


class OptionBook:
    """Cached reads of option bars, recording every window it was asked for and lacked."""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path
        self.needs: set[Need] = set()
        self._minute: dict[tuple[OptionKey, datetime.date], tuple[str, dict[datetime.datetime, HistCandle]]] = {}

    def minute_bars(
        self, key: OptionKey, day: datetime.date
    ) -> tuple[str, dict[datetime.datetime, HistCandle]]:
        cached = self._minute.get((key, day))
        if cached is not None:
            return cached
        start, end = session_bounds(day)
        need = Need(key, store.INTERVAL_MINUTE, start, end)
        if not store.fetched(need, path=self.path):
            self.needs.add(need)
            return MISSING, {}
        bars = store.load_option_bars(key, store.INTERVAL_MINUTE, start, end, path=self.path)
        out = (OK if bars else NO_DATA, {b.ts: b for b in bars})
        self._minute[(key, day)] = out
        return out

    def second_bars(
        self, key: OptionKey, start: datetime.datetime, end: datetime.datetime
    ) -> tuple[str, list[HistCandle]]:
        need = Need(key, store.INTERVAL_SECOND, start, end)
        if not store.fetched(need, path=self.path):
            self.needs.add(need)
            return MISSING, []
        bars = store.load_option_bars(key, store.INTERVAL_SECOND, start, end, path=self.path)
        return (OK if bars else NO_DATA), bars


class ModelPricer:
    """Black-Scholes off spot and the day's VIX -- the original harness, unchanged in spirit."""

    real = False
    source = "modelled (Black-Scholes off spot, daily VIX)"

    def bar(
        self, key: OptionKey, minute: datetime.datetime, *, spot: float, sigma: float
    ) -> tuple[str, Optional[HistCandle]]:
        price = theoretical_price(
            spot, key.strike, key.right, years_to_expiry(minute, key.expiry), sigma
        )
        return OK, HistCandle(minute, price, price, price, price, 1)


class RealPricer:
    """Traded bars for the exact contract, from the cache."""

    real = True
    source = "real (ICICI traded candles; spread modelled)"

    def __init__(self, book: OptionBook) -> None:
        self.book = book

    def bar(
        self, key: OptionKey, minute: datetime.datetime, *, spot: float = 0.0, sigma: float = 0.0
    ) -> tuple[str, Optional[HistCandle]]:
        status, bars = self.book.minute_bars(key, minute.date())
        if status != OK:
            return status, None
        found = bars.get(minute)
        if found is None or found.volume <= 0:
            return NO_TRADE, None
        return OK, found

    def last_traded(self, key: OptionKey, minute: datetime.datetime) -> Optional[HistCandle]:
        """The latest traded bar at or before `minute` -- what the LTP was at that time."""
        status, bars = self.book.minute_bars(key, minute.date())
        if status != OK:
            return None
        best = None
        for ts, bar in bars.items():
            if ts <= minute and bar.volume > 0 and (best is None or ts > best.ts):
                best = bar
        return best

    def long_path(
        self, key: OptionKey, entry_at: datetime.datetime
    ) -> tuple[str, list[tuple[datetime.datetime, float]], str]:
        """Every traded price from `entry_at` to the close, for a LONG position.

        1-second bars inside `SECOND_WINDOW`, 1-minute bars after it. A 1-minute bar is
        expanded open -> low -> high -> close: the adverse extreme first, so a stop and a
        target inside one bar resolve as the stop. That is the conservative reading of a bar
        whose internal order is unknown.

        Returns (status, points, resolution). A 1-second window ICICI returned nothing for
        falls back to 1-minute bars and says so in `resolution`; a window never fetched is
        MISSING, because the answer might still be there.
        """
        status, minute = self.book.minute_bars(key, entry_at.date())
        if status != OK:
            return status, [], ""
        s_status, seconds = self.book.second_bars(key, entry_at, entry_at + SECOND_WINDOW)
        if s_status == MISSING:
            return MISSING, [], ""
        points: list[tuple[datetime.datetime, float]] = []
        if s_status == OK:
            points.extend((b.ts, b.close) for b in seconds if b.volume > 0 and b.ts >= entry_at)
            minute_from, resolution = entry_at + SECOND_WINDOW, "1s"
        else:
            minute_from, resolution = entry_at, "1m"
        for ts in sorted(minute):
            bar = minute[ts]
            if ts < minute_from or bar.volume <= 0:
                continue
            points.extend(
                [
                    (ts, bar.open),
                    (ts + datetime.timedelta(seconds=15), bar.low),
                    (ts + datetime.timedelta(seconds=30), bar.high),
                    (ts + datetime.timedelta(seconds=59), bar.close),
                ]
            )
        return OK, points, resolution
