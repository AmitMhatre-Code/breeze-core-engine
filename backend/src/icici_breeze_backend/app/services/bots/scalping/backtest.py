"""Bot 3's backtest (docs/bots-scalping-plan.md sections 8 and 8.7; signals-streamline-plan.md 8).

The bot trades a cell of the signal grid (`SignalChoice`), so the replay acts on that series'
replayed readings (`backtest_common.series_readings`) -- the very calls the signal backtest
scores -- one trade per call, held until the stop, the trailing stop, a signal reversal or the
square-off closes it (`signal.call_reversed`, as live).

**Two ways to price the option, never mixed in one run.**

* **Real** (the default once option bars are cached): the traded 1-minute bars ICICI serves
  for the exact ATM contract, with 1-second bars inside each trade so the ~90-second ladder is
  judged at the resolution it actually runs at. Traded prices are not bid/ask, so the spread
  is still modelled from what paper mode has observed.
* **Model** (`--model`): Black-Scholes off spot with that day's real India VIX -- the original
  harness. An honest test of the **signal** over real history, an approximate one of the
  **fills**.

Either way a result showing edge has shown the signal has edge; paper mode is what tests
execution. Every result line names its price, spot and spread sources, so a run priced off a
fallback is never mistaken for a calibrated one.

Pure: no broker, no clock, no database beyond the read-only cache. `backtest_store` supplies
the candles and `scripts/scalping_backtest.py` supplies the arguments.

Reuses the production signal, ladder, fills and gate stack rather than reimplementing them
-- a backtest that re-derived the entry rule would be testing a different bot from the one
that trades.
"""
from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from icici_breeze_backend.app.domain.bots import MomentumLongScalperConfig, ReasonCode
from icici_breeze_backend.app.services.bots.charges import ChargesModel
from icici_breeze_backend.app.services.bots.scalping import backtest_regime as regime
from icici_breeze_backend.app.services.bots.scalping import ladder as ladder_mod
from icici_breeze_backend.app.services.bots.scalping.backtest_common import (
    MINUTE,
    DayLedger,
    by_day,
    split_session,
    checkpoint,
    gate,
    rollback,
    spot_at,
    spot_map,
    tally_idle,
    unavailable_reading,
)
from icici_breeze_backend.app.services.bots.scalping.backtest_options import (
    MISSING,
    NO_DATA,
    ModelPricer,
    theoretical_price,
    years_to_expiry,
)
from icici_breeze_backend.app.services.bots.scalping.backtest_regime import (
    expiry_weekday_for,
    next_expiry,
)
from icici_breeze_backend.app.services.bots.scalping.backtest_store import HistCandle, OptionKey
from icici_breeze_backend.app.services.bots.scalping.paper import (
    round_trip_pnl,
    simulate_buy,
    simulate_sell,
)
from icici_breeze_backend.app.services.bots.scalping.signal import (
    call_reversed,
    call_unbroken,
    evaluate_reading,
)
from icici_breeze_backend.app.services.index_signal.series import apply_direction
from icici_breeze_backend.app.services.bots.scalping.spreads import SpreadStats

__all__ = [
    "DEFAULT_EXPIRY_WEEKDAY_MAP",
    "BacktestCycle",
    "BacktestResult",
    "atm_strike_for",
    "expiry_weekday_for",
    "next_expiry",
    "run_backtest",
    "theoretical_price",
    "years_to_expiry",
]

_logger = logging.getLogger(__name__)

STRIKE_STEP = 50.0
INDEX = "NIFTY"
DEFAULT_EXPIRY_WEEKDAY_MAP = regime.EXPIRY_WEEKDAY_MAP[INDEX]

# The runtime acts a pass or two after a candle closes; on 1-second bars the entry is the first
# trade after this delay, within the order's own fill timeout and retries.
ENTRY_LATENCY = datetime.timedelta(seconds=2)

_DAY_COUNTERS = (
    "skipped_no_signal",
    "skipped_unaffordable",
    "skipped_same_signal",
    "skipped_no_data",
    "skipped_no_fill",
)


@dataclass(frozen=True)
class BacktestCycle:
    entered_at: datetime.datetime
    exited_at: datetime.datetime
    right: str
    strike: float
    lots: int
    quantity: int
    entry_price: float
    exit_price: float
    gross_pnl: float
    friction: float
    net_pnl: float
    exit_reason: str
    spot_entry: float
    spot_exit: float
    iv: float
    # "model", "1s" (1-second bars through the trade) or "1m" (ICICI had no 1-second bars).
    resolution: str = "model"


@dataclass
class BacktestResult:
    cycles: list[BacktestCycle] = field(default_factory=list)
    skipped_no_signal: int = 0
    skipped_unaffordable: int = 0
    # Fired, but on the signal run that opened the previous trade (plan section 3.4).
    skipped_same_signal: int = 0
    # Real prices only: ICICI returned nothing for the contract, or nothing traded at entry.
    skipped_no_data: int = 0
    skipped_no_fill: int = 0
    # Every minute's gate verdict and the reading it saw, for the backtest zip's decisions.csv.
    decisions: list[dict[str, Any]] = field(default_factory=list)
    days: int = 0
    # Stopped at an uncached contract; the contract is now a need for `fetch-options`.
    days_awaiting_data: int = 0
    days_outside_history: int = 0
    days_without_spot: int = 0
    # Every other gate verdict, by reason code: windows, warm-up, cooldown, expiry day ...
    idle: dict[str, int] = field(default_factory=dict)
    iv_source: str = ""
    spread_source: str = ""
    price_source: str = ""
    spot_source: str = ""

    @property
    def wins(self) -> int:
        return sum(1 for c in self.cycles if c.net_pnl > 0)

    @property
    def gross(self) -> float:
        return round(sum(c.gross_pnl for c in self.cycles), 2)

    @property
    def friction(self) -> float:
        return round(sum(c.friction for c in self.cycles), 2)

    @property
    def net(self) -> float:
        return round(sum(c.net_pnl for c in self.cycles), 2)

    @property
    def win_rate(self) -> float:
        return round(100.0 * self.wins / len(self.cycles), 1) if self.cycles else 0.0

    def summary(self) -> dict[str, Any]:
        replayed = self.days - self.days_awaiting_data - self.days_outside_history - self.days_without_spot
        resolutions: dict[str, int] = {}
        for c in self.cycles:
            resolutions[c.resolution] = resolutions.get(c.resolution, 0) + 1
        return {
            "days": self.days,
            "days_replayed": replayed,
            "days_awaiting_data": self.days_awaiting_data,
            "days_outside_history": self.days_outside_history,
            "days_without_spot": self.days_without_spot,
            "cycles": len(self.cycles),
            "cycles_per_day": round(len(self.cycles) / replayed, 1) if replayed > 0 else 0.0,
            "win_rate_pct": self.win_rate,
            "gross_pnl": self.gross,
            "friction": self.friction,
            "net_pnl": self.net,
            # The section 6.4 number: friction as a share of what the trades actually made.
            "friction_pct_of_gross": (
                round(100.0 * self.friction / abs(self.gross), 1) if self.gross else None
            ),
            "skipped_no_signal": self.skipped_no_signal,
            "skipped_unaffordable": self.skipped_unaffordable,
            "skipped_same_signal": self.skipped_same_signal,
            "skipped_no_data": self.skipped_no_data,
            "skipped_no_fill": self.skipped_no_fill,
            "resolution": resolutions,
            "idle": dict(sorted(self.idle.items())),
            "price_source": self.price_source,
            "spot_source": self.spot_source,
            "iv_source": self.iv_source,
            "spread_source": self.spread_source,
        }


def atm_strike_for(spot: float, step: float = STRIKE_STEP) -> float:
    return round(spot / step) * step


def run_backtest(
    bars: Sequence[HistCandle],
    *,
    config: MomentumLongScalperConfig,
    charges: ChargesModel,
    spread: SpreadStats,
    vix_by_day: dict[datetime.date, float],
    readings: dict[datetime.datetime, dict[str, Any]],
    default_iv: float = 0.13,
    holidays: Optional[set[datetime.date]] = None,
    weekday_map: regime.WeekdayMap = DEFAULT_EXPIRY_WEEKDAY_MAP,
    pricer: Any = None,
    spot_bars: Sequence[HistCandle] = (),
    record_decisions: bool = False,
) -> BacktestResult:
    """Replay the bot's signal series, gates and ladder over historical futures bars.

    `readings` is the bot's series as published, keyed by bar start
    (`backtest_common.series_readings`, built over the range plus warm-up days); the bot's
    direction is applied here, as it is live. Each minute's entry is that minute's call, one
    trade per call, held until the call ends or the ladder's stop takes it.

    One position at a time, matching the bot. Sessions are per calendar day and positions do
    not carry across days.

    `spot_bars` are the cash index's 1-minute bars: the live bot picks its ATM strike off the
    index, and a monthly future's basis would put it a strike or two away. Real pricing needs
    them; model pricing falls back to the futures close and says so.
    """
    pricer = pricer or ModelPricer()
    spots = spot_map(spot_bars)
    result = BacktestResult(
        spread_source=spread.describe(),
        iv_source="daily India VIX" if vix_by_day else f"constant {default_iv:.3f}",
        price_source=pricer.source,
        spot_source="cash index" if spots else "futures close (no index bars cached)",
    )
    days = by_day(bars)
    spot_days = {ts.date() for ts in spots}
    result.days = len(days)

    for day, day_bars in sorted(days.items()):
        if pricer.real and day not in spot_days:
            result.days_without_spot += 1
            continue
        sigma = (vix_by_day.get(day, default_iv * 100.0)) / 100.0
        if sigma <= 0:
            sigma = default_iv
        expiry = next_expiry(day, weekday_map, holidays)
        _run_day(
            day_bars, day, expiry, sigma, config, charges, spread, pricer, spots, result,
            readings=readings, record_decisions=record_decisions,
        )
    return result


def _fresh_call_hold(reading: dict[str, Any], ledger: DayLedger) -> Optional[tuple[str, str]]:
    """The runtime's one-trade-per-call rule, on the replayed reading."""
    start, side = ledger.totals.last_entry_candle_start, ledger.totals.last_entry_side
    if start is None or side is None:
        return None
    if call_unbroken(reading, entry_candle_start=int(start), side=str(side)):
        return (ReasonCode.SIGNAL_NOT_FRESH, "Still the call that opened the last trade.")
    return None


def call_reversal_time(
    readings: dict[datetime.datetime, dict[str, Any]],
    later_bars: Sequence[HistCandle],
    *,
    series_id: str,
    direction: str,
    side: str,
) -> Optional[datetime.datetime]:
    """When the runtime would first see the signal fire the other way: the close of the first
    later bar whose reading is a call against the open trade -- judged by `signal.call_reversed`,
    the function the live exit uses. None when nothing turns before the session ends.

    A call merely lapsing no longer closes a trade, so this no longer needs to know when the call
    began or when it would have run out."""
    for bar in later_bars:
        reading = apply_direction(readings.get(bar.ts) or unavailable_reading(series_id), direction)
        if call_reversed(reading, side=side):
            return bar.ts + MINUTE
    return None


def _run_day(
    day_bars: list[HistCandle],
    day: datetime.date,
    expiry: datetime.date,
    sigma: float,
    config: MomentumLongScalperConfig,
    charges: ChargesModel,
    spread: SpreadStats,
    pricer: Any,
    spots: dict,
    result: BacktestResult,
    *,
    readings: dict[datetime.datetime, dict[str, Any]],
    record_decisions: bool = False,
) -> None:
    _pre_open, day_bars = split_session(day_bars)
    choice = config.signal
    series_id = choice.series_id(config.index)
    lot_size = regime.lot_size_for(INDEX, day)
    saved = checkpoint(result, _DAY_COUNTERS)
    saved_decisions = len(result.decisions)
    ledger = DayLedger()
    i, n = 0, len(day_bars)

    while i < n:
        bar = day_bars[i]
        i += 1
        now = bar.ts + MINUTE  # the bar has closed; this is when the runtime sees it
        reading = apply_direction(readings.get(bar.ts) or unavailable_reading(series_id), choice.direction)
        signal = evaluate_reading(reading, series_id)
        hold = _fresh_call_hold(reading, ledger)
        decision = gate(
            config,
            ledger,
            now,
            is_expiry_day=day == expiry,
            warm=True,
            has_open_position=False,
            signal=signal,
            entry_hold=hold,
        )
        if record_decisions:
            result.decisions.append({
                "date": day.isoformat(),
                "time": now.strftime("%H:%M"),
                "futures_close": bar.close,
                "action": decision.action,
                "reason_code": decision.reason_code,
                "reason": decision.reason_text,
                "signal_state": reading.get("state"),
                "signal_reason": reading.get("reason"),
                "strength": reading.get("signal"),
                "call_started": reading.get("call_started_at"),
            })
        if decision.action != "enter":
            if decision.reason_code == ReasonCode.SIGNAL_NO_TRADE:
                result.skipped_no_signal += 1
            elif decision.reason_code == ReasonCode.SIGNAL_NOT_FRESH:
                result.skipped_same_signal += 1
            else:
                tally_idle(result.idle, decision)
            continue

        right = signal.right or "call"
        spot = spot_at(spots, bar.ts, bar.close)
        key = OptionKey(INDEX, expiry, regime.atm_strike(spot, INDEX), right)
        status, points, resolution = _price_path(
            pricer, key, now, spot, day_bars[i:], spots, sigma
        )
        if status == MISSING:
            # Everything after this entry depends on how it plays out, so the whole day waits
            # for the data rather than being replayed on a guess.
            rollback(result, saved)
            del result.decisions[saved_decisions:]
            result.days_awaiting_data += 1
            return
        if status == NO_DATA:
            result.skipped_no_data += 1
            continue
        entry = _entry_point(points, now, resolution, config)
        if entry is None:
            result.skipped_no_fill += 1
            continue
        entry_idx, entry_at, price = entry

        half = spread.spread_for(price) / 2.0
        bid, ask = max(0.05, round(price - half, 2)), round(price + half, 2)
        lots = int(config.premium_outlay_inr // (ask * lot_size))
        if lots < 1:
            result.skipped_unaffordable += 1
            continue
        quantity = lots * lot_size
        fill = simulate_buy(bid, ask, quantity, charges)
        started = float(signal.values["call_started_at"])
        ledger.opened(candle_start=int(started), side=str(signal.side))
        reversal_at = call_reversal_time(
            readings, day_bars[i:], series_id=series_id, direction=choice.direction,
            side=str(signal.side),
        )

        exit_at, exit_bid, exit_ask, reason = _hold(
            points, entry_idx, entry_at, fill.price, quantity, config, spread, ledger, day == expiry,
            reversal_at=reversal_at,
        )
        out = simulate_sell(exit_bid, exit_ask, quantity, charges)
        gross, friction, net = round_trip_pnl(fill, out)
        ledger.closed(net, exit_at)
        result.cycles.append(
            BacktestCycle(
                entered_at=entry_at,
                exited_at=exit_at,
                right=right,
                strike=key.strike,
                lots=lots,
                quantity=quantity,
                entry_price=fill.price,
                exit_price=out.price,
                gross_pnl=gross,
                friction=friction,
                net_pnl=net,
                exit_reason=reason,
                spot_entry=spot,
                spot_exit=_spot_near(spots, day_bars, exit_at),
                iv=sigma,
                resolution=resolution,
            )
        )
        # One position at a time: bars that closed while it was held are skipped, as the live
        # runtime makes no entry decision while a position is open.
        while i < n and day_bars[i].ts + MINUTE <= exit_at:
            i += 1


def _price_path(
    pricer: Any,
    key: OptionKey,
    now: datetime.datetime,
    spot: float,
    later_bars: Sequence[HistCandle],
    spots: dict,
    sigma: float,
) -> tuple[str, list[tuple[datetime.datetime, float]], str]:
    if pricer.real:
        return pricer.long_path(key, now)
    points = [(now, theoretical_price(spot, key.strike, key.right, years_to_expiry(now, key.expiry), sigma))]
    for bar in later_bars:
        at = bar.ts + MINUTE
        s = spot_at(spots, bar.ts, bar.close)
        points.append(
            (at, theoretical_price(s, key.strike, key.right, years_to_expiry(at, key.expiry), sigma))
        )
    return "ok", points, "model"


def _entry_point(
    points: list[tuple[datetime.datetime, float]],
    now: datetime.datetime,
    resolution: str,
    config: MomentumLongScalperConfig,
) -> Optional[tuple[int, datetime.datetime, float]]:
    """Where the entry fills: (index into points, time, traded price), or None for no fill.

    Model: at the signal bar's close, as before. 1-second bars: the first trade inside the
    order's own fill window (timeout x attempts) after the runtime's reaction delay. 1-minute
    bars: the next minute's open -- and a minute with no trade in it is no fill.
    """
    if not points:
        return None
    if resolution == "model":
        return 0, points[0][0], points[0][1]
    if resolution == "1m":
        ts, price = points[0]
        return (0, ts, price) if ts == now else None
    execution = config.execution
    window_end = now + ENTRY_LATENCY + datetime.timedelta(
        seconds=execution.entry_fill_timeout_seconds * (execution.entry_retries + 1)
    )
    for idx, (ts, price) in enumerate(points):
        if ts < now + ENTRY_LATENCY:
            continue
        if ts > window_end:
            return None
        return idx, ts, price
    return None


def _hold(
    points: list[tuple[datetime.datetime, float]],
    entry_idx: int,
    entry_at: datetime.datetime,
    entry_price: float,
    quantity: int,
    config: MomentumLongScalperConfig,
    spread: SpreadStats,
    ledger: DayLedger,
    is_expiry_day: bool,
    *,
    reversal_at: Optional[datetime.datetime] = None,
) -> tuple[datetime.datetime, float, float, str]:
    """Walk the price path through the ladder, a signal reversal and the exit gates.
    (at, bid, ask, reason)."""
    state = ladder_mod.open_ladder(entry_price, entry_at.timestamp(), config.exits)
    at, bid, ask = entry_at, *_touch(points[entry_idx][1], spread)
    for ts, price in points[entry_idx + 1 :]:
        at = ts
        bid, ask = _touch(price, spread)
        state, _ = ladder_mod.advance(state, bid, config.exits)
        verdict = ladder_mod.exit_decision(state, bid, ts.timestamp(), config.exits)
        if verdict is None and reversal_at is not None and ts >= reversal_at:
            verdict = (ReasonCode.SIGNAL_REVERSED, "The signal turned the other way.")
        decision = gate(
            config,
            ledger,
            ts,
            is_expiry_day=is_expiry_day,
            warm=True,
            has_open_position=True,
            position_exit=verdict,
            unrealized=(bid - entry_price) * quantity,
        )
        if decision.action == "exit":
            return at, bid, ask, decision.reason_code
    return at, bid, ask, "session_end"


def _touch(price: float, spread: SpreadStats) -> tuple[float, float]:
    half = spread.spread_for(price) / 2.0
    return max(0.05, round(price - half, 2)), round(price + half, 2)


def _spot_near(spots: dict, day_bars: Sequence[HistCandle], at: datetime.datetime) -> float:
    minute = at.replace(second=0, microsecond=0)
    for bar in reversed(day_bars):
        if bar.ts <= minute:
            return spot_at(spots, bar.ts, bar.close)
    return day_bars[0].close if day_bars else 0.0
