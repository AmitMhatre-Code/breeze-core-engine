"""Shared replay plumbing: the live gate stack, driven off a replayed clock.

The replays call the same `decide()` the runtime calls, with a snapshot built from the replayed
minute instead of the wall clock. That is what makes a backtest describe the bot that trades:
session windows, the hard square-off, the expiry-day rule, the daily loss cap and the
consecutive-loss cooldown all come from the one implementation, so none of them can drift
between the two paths. A replay that re-derived them would be testing a different bot.

What a replay cannot see is set to its "healthy" value and stated here once: the licence
allows trading, the feed is never stale, the API budget is full, and no Strategy Group rule
conflicts. Each of those is a live operational failure, not a property of the strategy.
"""
from __future__ import annotations

import datetime
from typing import Any, Optional, Sequence

from icici_breeze_backend.app.domain.bots import ReasonCode, ScalperDayTotals
from icici_breeze_backend.app.services.bots.scalping.backtest_store import HistCandle
from icici_breeze_backend.app.services.bots.scalping.candles import Candle
from icici_breeze_backend.app.services.bots.scalping.decide import (
    Decision,
    FeedHealth,
    Snapshot,
    decide,
)

MINUTE = datetime.timedelta(minutes=1)
# `runtime._feed_health` warms on max(EMA 9, volume MA 20) candles for both scalpers; Bot 4 has
# no signal config, so it takes the same defaults.
WARM_CANDLES = 20
_FULL_API_BUDGET = 90
_TS = "%Y-%m-%d %H:%M:%S"


class DayLedger:
    """Today's totals, kept the way `repositories.bots.scalper_day_totals` recomputes them.

    `consecutive_losses` counts back from the latest close and stops at the first non-loss;
    `last_closed_at` uses the repository's timestamp shape because the re-entry gate parses it.
    """

    def __init__(self) -> None:
        self.totals = ScalperDayTotals()

    def opened(self, *, candle_start: Optional[int] = None, side: Optional[str] = None) -> None:
        update: dict[str, Any] = {"cycles": self.totals.cycles + 1}
        if candle_start is not None:
            update["last_entry_candle_start"] = candle_start
            update["last_entry_side"] = side
        self.totals = self.totals.model_copy(update=update)

    def closed(self, net_pnl: float, at: datetime.datetime) -> None:
        self.totals = self.totals.model_copy(
            update={
                "realized_net_pnl": round(self.totals.realized_net_pnl + net_pnl, 2),
                "consecutive_losses": self.totals.consecutive_losses + 1 if net_pnl < 0 else 0,
                "last_closed_at": at.strftime(_TS),
            }
        )


def gate(
    config: Any,
    ledger: DayLedger,
    now: datetime.datetime,
    *,
    is_expiry_day: bool,
    warm: bool,
    has_open_position: bool,
    signal: Any = None,
    entry_hold: Optional[tuple[str, str]] = None,
    position_exit: Optional[tuple[str, str]] = None,
    unrealized: float = 0.0,
    exit_at_window_end: bool = False,
) -> Decision:
    snapshot = Snapshot(
        now_ist=now,
        trading_allowed=True,
        is_trading_day=True,
        is_expiry_day=is_expiry_day,
        feed=FeedHealth(warm=warm, stale=False),
        totals=ledger.totals,
        has_open_position=has_open_position,
        api_calls_remaining=_FULL_API_BUDGET,
        position_exit=position_exit,
        exit_at_window_end=exit_at_window_end,
        unrealized_pnl=float(unrealized),
        signal=signal,
        entry_hold=entry_hold,
    )
    return decide(snapshot, config)


def tally_idle(idle: dict[str, int], decision: Decision) -> None:
    idle[decision.reason_code] = idle.get(decision.reason_code, 0) + 1


def checkpoint(result: Any, counters: Sequence[str]) -> tuple[dict[str, int], int, dict[str, int]]:
    """The result's state at the start of a day, so a day that stops for data can be undone.

    A day that waits for an uncached contract is replayed again in full once the contract
    arrives. Keeping the trades it made before stopping would count them twice then -- and
    until then would put half a day into the totals as if it were a whole one.
    """
    return {k: getattr(result, k) for k in counters}, len(result.cycles), dict(result.idle)


def rollback(result: Any, saved: tuple[dict[str, int], int, dict[str, int]]) -> None:
    counters, n_cycles, idle = saved
    for k, v in counters.items():
        setattr(result, k, v)
    del result.cycles[n_cycles:]
    result.idle = idle


class SessionVwap:
    """Running volume-weighted typical price, matching the live session VWAP definition."""

    def __init__(self) -> None:
        self._pv = 0.0
        self._v = 0

    def add(self, bar: HistCandle) -> Optional[float]:
        if bar.volume > 0:
            self._pv += ((bar.high + bar.low + bar.close) / 3.0) * bar.volume
            self._v += bar.volume
        return self._pv / self._v if self._v > 0 else None


def to_candle(bar: HistCandle, vwap: Optional[float] = None) -> Candle:
    return Candle(
        start=int(bar.ts.timestamp()),
        open=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
        volume=bar.volume,
        turnover=None,
        ticks=1,
        vwap=vwap,
    )


def spot_map(bars: Sequence[HistCandle]) -> dict[datetime.datetime, HistCandle]:
    return {b.ts: b for b in bars}


def spot_at(
    spots: dict[datetime.datetime, HistCandle], minute: datetime.datetime, fallback: float
) -> float:
    """The index close for the minute, else the caller's fallback (the futures close)."""
    bar = spots.get(minute)
    return bar.close if bar is not None and bar.close > 0 else float(fallback)


def by_day(bars: Sequence[HistCandle]) -> dict[datetime.date, list[HistCandle]]:
    out: dict[datetime.date, list[HistCandle]] = {}
    for bar in bars:
        out.setdefault(bar.date, []).append(bar)
    return out


# Reason codes a replay reports as its own skips rather than as gate idles.
SIGNAL_REASONS = {ReasonCode.SIGNAL_NO_TRADE, ReasonCode.SIGNAL_NOT_FRESH}
