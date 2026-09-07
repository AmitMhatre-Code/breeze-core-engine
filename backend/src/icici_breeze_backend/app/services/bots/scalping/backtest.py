"""Signal backtest for Bot 3 (docs/bots-scalping-plan.md section 8).

**What this tests, and what it does not.** There is no historical option-price series in this
deployment -- `ws_quote_snapshot` keeps only each contract's last value per day -- so option
prices here are *modelled*: Black-Scholes off real 1-minute NIFTY futures candles, with a
volatility taken from that day's real India VIX and a spread taken from what paper mode has
actually observed. That makes this an honest test of the **signal** over real history, and
only an approximate test of the **fills**. A result showing edge has shown the signal has
edge, not that the strategy survives execution -- paper mode is what tests the second half.
Every result line says which IV and which spread source it used, so a run priced off a
fallback is never mistaken for a calibrated one.

Pure: no broker, no clock, no database. `backtest_store` supplies the candles and
`scripts/scalping_backtest.py` supplies the arguments.

Reuses the production signal and ladder rather than reimplementing them. That is the whole
value of having kept them pure -- a backtest that re-derived the entry rule would be testing
a different bot from the one that trades.
"""
from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from icici_breeze_backend.app.domain.bots import MomentumLongScalperConfig
from icici_breeze_backend.app.services.bots.scalping import ladder as ladder_mod
from icici_breeze_backend.app.services.bots.scalping.backtest_store import HistCandle
from icici_breeze_backend.app.services.bots.scalping.candles import Candle
from icici_breeze_backend.app.services.bots.charges import ChargesModel
from icici_breeze_backend.app.services.bots.scalping.signal import evaluate_momentum
from icici_breeze_backend.app.services.bots.scalping.spreads import SpreadStats
from icici_breeze_backend.app.services.iv_compute import bs_price_call, bs_price_put

_logger = logging.getLogger(__name__)

STRIKE_STEP = 50.0
TRADING_DAYS_PER_YEAR = 252.0
MINUTES_PER_SESSION = 375.0

# NIFTY weekly expiry has not always fallen on the same weekday -- SEBI moved it. A single
# weekday would misprice every option on one side of the change, so the map is date-ranged
# and explicit. Monday=0 ... Sunday=6.
DEFAULT_EXPIRY_WEEKDAY_MAP: tuple[tuple[Optional[datetime.date], int], ...] = (
    (datetime.date(2025, 8, 31), 3),   # Thursday, up to and including this date
    (None, 1),                          # Tuesday, thereafter
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


@dataclass
class BacktestResult:
    cycles: list[BacktestCycle] = field(default_factory=list)
    skipped_no_signal: int = 0
    skipped_unaffordable: int = 0
    days: int = 0
    iv_source: str = ""
    spread_source: str = ""

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
        return {
            "days": self.days,
            "cycles": len(self.cycles),
            "cycles_per_day": round(len(self.cycles) / self.days, 1) if self.days else 0.0,
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
            "iv_source": self.iv_source,
            "spread_source": self.spread_source,
        }


def expiry_weekday_for(
    d: datetime.date, weekday_map: Sequence[tuple[Optional[datetime.date], int]]
) -> int:
    for until, weekday in weekday_map:
        if until is None or d <= until:
            return weekday
    return weekday_map[-1][1]


def next_expiry(
    d: datetime.date,
    weekday_map: Sequence[tuple[Optional[datetime.date], int]],
    holidays: Optional[set[datetime.date]] = None,
) -> datetime.date:
    """The next weekly expiry on or after `d`, shifted back off an exchange holiday.

    Shifted *back*, not forward: when an expiry day is a holiday the exchange brings the
    expiry forward to the previous trading day, it does not defer it.
    """
    holidays = holidays or set()
    target = expiry_weekday_for(d, weekday_map)
    ahead = (target - d.weekday()) % 7
    expiry = d + datetime.timedelta(days=ahead)
    while expiry in holidays or expiry.weekday() >= 5:
        expiry -= datetime.timedelta(days=1)
        if expiry < d:
            # Shifting back has moved the expiry into the past; the next one is a week out.
            return next_expiry(d + datetime.timedelta(days=1), weekday_map, holidays)
    return expiry


def years_to_expiry(now: datetime.datetime, expiry: datetime.date) -> float:
    """Calendar-day time to expiry, floored so an expiry-day option is never worthless.

    Deliberately simple. These trades last about 90 seconds, so intraday theta is
    irrelevant to the result; time to expiry matters here only because it sets the option's
    delta and gamma.
    """
    days = (expiry - now.date()).days
    remaining_minutes = max(0.0, (15 * 60 + 30) - (now.hour * 60 + now.minute))
    total_days = days + remaining_minutes / (24 * 60.0)
    return max(total_days / 365.0, 1.0 / (365.0 * 24 * 60))


def theoretical_price(spot: float, strike: float, right: str, t: float, sigma: float) -> float:
    fn = bs_price_call if right == "call" else bs_price_put
    return max(0.05, round(fn(spot, strike, t, sigma), 2))


def atm_strike_for(spot: float, step: float = STRIKE_STEP) -> float:
    return round(spot / step) * step


def _to_candle(bar: HistCandle) -> Candle:
    return Candle(
        start=int(bar.ts.timestamp()),
        open=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
        volume=bar.volume,
        turnover=None,
        ticks=1,
    )


def _session_vwap(bars: Sequence[HistCandle]) -> Optional[float]:
    """Volume-weighted mean of the session's bars so far, matching the live definition."""
    total_v = sum(b.volume for b in bars)
    if total_v <= 0:
        return None
    return sum(((b.high + b.low + b.close) / 3.0) * b.volume for b in bars) / total_v


def run_backtest(
    bars: Sequence[HistCandle],
    *,
    config: MomentumLongScalperConfig,
    charges: ChargesModel,
    spread: SpreadStats,
    vix_by_day: dict[datetime.date, float],
    default_iv: float = 0.13,
    holidays: Optional[set[datetime.date]] = None,
    weekday_map: Sequence[tuple[Optional[datetime.date], int]] = DEFAULT_EXPIRY_WEEKDAY_MAP,
) -> BacktestResult:
    """Replay the live signal and ladder over historical futures bars.

    One position at a time, matching the bot. Sessions are per calendar day and state does
    not carry across days -- a candle history is not a position.
    """
    result = BacktestResult(
        spread_source=spread.describe(),
        iv_source="daily India VIX" if vix_by_day else f"constant {default_iv:.3f}",
    )
    by_day: dict[datetime.date, list[HistCandle]] = {}
    for bar in bars:
        by_day.setdefault(bar.date, []).append(bar)
    result.days = len(by_day)

    for day, day_bars in sorted(by_day.items()):
        sigma = (vix_by_day.get(day, default_iv * 100.0)) / 100.0
        if sigma <= 0:
            sigma = default_iv
        expiry = next_expiry(day, weekday_map, holidays)
        _run_day(day_bars, expiry, sigma, config, charges, spread, result)
    return result


def _run_day(
    day_bars: list[HistCandle],
    expiry: datetime.date,
    sigma: float,
    config: MomentumLongScalperConfig,
    charges: ChargesModel,
    spread: SpreadStats,
    result: BacktestResult,
) -> None:
    required = max(config.signal.ema_period, config.signal.volume_ma_period)
    open_position: Optional[dict[str, Any]] = None

    for i in range(len(day_bars)):
        bar = day_bars[i]
        history = day_bars[: i + 1]
        spot = bar.close
        t = years_to_expiry(bar.ts, expiry)

        if open_position is not None:
            price = theoretical_price(
                spot, open_position["strike"], open_position["right"], t, sigma
            )
            half = spread.spread_for(price) / 2.0
            bid = max(0.05, round(price - half, 2))
            state, _ = ladder_mod.advance(open_position["ladder"], bid, config.exits)
            open_position["ladder"] = state
            # The ladder was opened with this bar series' own timestamps, so the wall clock
            # it compares against is simply the current bar.
            verdict = ladder_mod.exit_decision(state, bid, bar.ts.timestamp(), config.exits)
            last_bar = i == len(day_bars) - 1
            if verdict or last_bar:
                _close(
                    open_position, bar, bid, spot, sigma, charges,
                    spread.spread_for(price), verdict, result,
                )
                open_position = None
            continue

        if len(history) < required:
            continue
        signal = evaluate_momentum(
            [_to_candle(b) for b in history], _session_vwap(history), config.signal
        )
        if not signal.fired:
            result.skipped_no_signal += 1
            continue

        right = signal.right or "call"
        strike = atm_strike_for(spot)
        price = theoretical_price(spot, strike, right, t, sigma)
        half = spread.spread_for(price) / 2.0
        ask = round(price + half, 2)
        lot_size = 75
        lots = int(config.premium_outlay_inr // (ask * lot_size))
        if lots < 1:
            result.skipped_unaffordable += 1
            continue

        fill = round(ask + half * charges.slippage_spread_fraction * 2, 2)
        open_position = {
            "entered_at": bar.ts,
            "right": right,
            "strike": strike,
            "lots": lots,
            "quantity": lots * lot_size,
            "entry_price": fill,
            "spot_entry": spot,
            "iv": sigma,
            "ladder": ladder_mod.open_ladder(fill, bar.ts.timestamp(), config.exits),
            "entry_charges": charges.leg_charges(fill, lots * lot_size, is_buy=True),
        }


def _close(
    position: dict[str, Any],
    bar: HistCandle,
    bid: float,
    spot: float,
    sigma: float,
    charges: ChargesModel,
    spread_abs: float,
    verdict: Optional[tuple[str, str]],
    result: BacktestResult,
) -> None:
    # Slippage is adverse on BOTH legs, exactly as `paper.simulate_sell` applies it. Applying
    # it only on entry -- as an earlier draft of this did -- makes every backtested cycle look
    # better than the same trade would in paper mode, which is the one comparison that has to
    # hold if the two are to be believed together.
    exit_price = max(
        0.05, round(bid - spread_abs * charges.slippage_spread_fraction, 2)
    )
    qty = int(position["quantity"])
    gross = round((exit_price - position["entry_price"]) * qty, 2)
    friction = round(
        position["entry_charges"] + charges.leg_charges(exit_price, qty, is_buy=False), 2
    )
    result.cycles.append(
        BacktestCycle(
            entered_at=position["entered_at"],
            exited_at=bar.ts,
            right=position["right"],
            strike=position["strike"],
            lots=int(position["lots"]),
            quantity=qty,
            entry_price=position["entry_price"],
            exit_price=exit_price,
            gross_pnl=gross,
            friction=friction,
            net_pnl=round(gross - friction, 2),
            exit_reason=(verdict[0] if verdict else "session_end"),
            spot_entry=position["spot_entry"],
            spot_exit=spot,
            iv=sigma,
        )
    )
