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
from icici_breeze_backend.app.services.bots.scalping.backtest_options import SESSION_CLOSE, SESSION_OPEN
from icici_breeze_backend.app.services.bots.scalping.backtest_store import HistCandle
from icici_breeze_backend.app.services.bots.scalping.candles import Candle
from icici_breeze_backend.app.services.bots.scalping.decide import (
    Decision,
    FeedHealth,
    Snapshot,
    decide,
)

MINUTE = datetime.timedelta(minutes=1)
# Bot 4's re-entry range needs 20 of today's candles (`runtime._feed_health`); Bot 3's warm-up
# is its signal's, which reads `unavailable` until the series is warm.
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


def series_readings(
    bars: Sequence[Any],
    key: Any,
    *,
    rollover_days: Optional[set[datetime.date]] = None,
) -> dict[datetime.datetime, dict[str, Any]]:
    """A signal series' reading as each bar closed, keyed by the bar's start (naive IST).

    Built with the signal backtest's own replay loop (`index_signal.series.replay_series`) over
    the whole range at once, so the baselines carry across days exactly as they do live, and a
    bot replay acts on the very calls the signal backtest scores. Pass bars from a few days
    before the range too: they only warm the series. Readings are as published (`follow`); a
    bot that fades turns them itself, as it does live."""
    from icici_breeze_backend.app.services.index_signal.bars import from_hist
    from icici_breeze_backend.app.services.index_signal.series import replay_series

    by_start = {from_hist(c).ts: c.ts for c in bars}
    excluded = (rollover_days or set()) if key.uses_oi else set()
    return {
        by_start[bar.ts]: snap
        for bar, snap in replay_series([from_hist(c) for c in bars], key, excluded_days=excluded)
    }


def unavailable_reading(series_id: str) -> dict[str, Any]:
    """What a minute with no replayed reading looks like: no trade, and says why."""
    return {"state": "unavailable", "reason": "no_reading", "key": series_id}


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
    """The session VWAP rebuilt from 1-minute bars, standing in for the tick's `avgPrice`.

    The live bot reads the exchange's own VWAP; history has only bars. Each bar's volume is
    priced at its OHLC average, and the pre-open bars count, because `avgPrice` fits better with
    them in. Against the live log of 2026-09-15 (122 candles) this reads 0.98 pts from `avgPrice`
    on average -- the typical price from 09:15, the first formula, read 1.80 -- and neither ever
    put a close on the other side of VWAP from where the live bot saw it (plan section 8.7a).
    """

    def __init__(self, pre_open: Sequence[HistCandle] = ()) -> None:
        self._pv = 0.0
        self._v = 0
        for bar in pre_open:
            self.add(bar)

    def add(self, bar: HistCandle) -> Optional[float]:
        # Not `!= 0`: ICICI served a pre-open bar with volume -650 on 2026-09-15.
        if bar.volume > 0:
            self._pv += ((bar.open + bar.high + bar.low + bar.close) / 4.0) * bar.volume
            self._v += bar.volume
        return self._pv / self._v if self._v > 0 else None


def split_session(day_bars: Sequence[HistCandle]) -> tuple[list[HistCandle], list[HistCandle]]:
    """(pre-open bars, the bars the live feed builds candles from).

    ICICI's futures history carries pre-open bars from 09:00 and post-close bars to 15:39. The
    live feed builds candles from 09:15 only, so a replay that fed it the pre-open would warm the
    EMA and volume MA on bars the bot never sees. The pre-open is returned for VWAP alone.
    """
    pre_open = [b for b in day_bars if b.ts.time() < SESSION_OPEN]
    session = [b for b in day_bars if SESSION_OPEN <= b.ts.time() < SESSION_CLOSE]
    return pre_open, session


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
