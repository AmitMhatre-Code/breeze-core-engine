"""Backtest for Bot 4, the ATM iron fly (docs/bots-scalping-plan.md sections 8.3 and 8.8).

Replays the live entry and exit rules over history -- the gate stack, the re-entry gate
(cooldown, then a settled futures range), `evaluate_exit` (drift first, then credit decay,
then the loss stop) and the window-end square-off -- pricing all four legs from real traded
bars or from Black-Scholes, never a mix of the two.

Two deliberate differences from the live bot, both because the history cannot answer them:

* **Fixed lots.** Live sizing is the largest fly under a rupee margin ceiling, verified with
  `margin_calculator`, and margin has no history. A run states its lot count instead, and
  reports P&L and cycle counts but no return on margin.
* **Traded prices, modelled spread.** The book is not in the history, so each leg's bid/ask is
  its traded price plus or minus half the spread paper mode has observed. Entry fills at the
  next minute's open; the position is marked on each minute's close, carrying a leg's last
  trade forward through a minute in which it did not trade, as an LTP would.

Exits apply the paper-mode slippage on every leg, entry and exit alike. (Paper mode's own fly
close prices at the touch without it; the backtest does not copy that, for the reason the
Bot 3 harness gives: the two paths must not flatter one side.)
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field, replace
from typing import Any, Optional, Sequence

from icici_breeze_backend.app.domain.bots import IronFlyScalperConfig
from icici_breeze_backend.app.services.bots.charges import ChargesModel
from icici_breeze_backend.app.services.bots.scalping import backtest_regime as regime
from icici_breeze_backend.app.services.bots.scalping.backtest_common import (
    MINUTE,
    WARM_CANDLES,
    DayLedger,
    SessionVwap,
    by_day,
    split_session,
    checkpoint,
    gate,
    rollback,
    spot_at,
    spot_map,
    tally_idle,
    to_candle,
)
from icici_breeze_backend.app.services.bots.scalping.backtest_options import (
    MISSING,
    NO_DATA,
    NO_TRADE,
    OK,
    ModelPricer,
)
from icici_breeze_backend.app.services.bots.scalping.backtest_store import HistCandle, OptionKey
from icici_breeze_backend.app.services.bots.scalping.iron_fly_bot import (
    evaluate_exit,
    reentry_blocked,
    wing_width_for,
)
from icici_breeze_backend.app.services.bots.scalping.paper import simulate_buy, simulate_sell
from icici_breeze_backend.app.services.bots.scalping.spreads import SpreadStats

INDEX = "NIFTY"
DEFAULT_LOTS = 3  # about what the 25,000 margin ceiling buys (IronFlyScalperConfig)


@dataclass(frozen=True)
class FlyLegSpec:
    key: OptionKey
    is_short: bool


@dataclass(frozen=True)
class FlyCycle:
    entered_at: datetime.datetime
    exited_at: datetime.datetime
    atm_strike: float
    wing_width: float
    lots: int
    quantity: int
    net_credit_per_unit: float
    exit_cost_per_unit: float
    gross_pnl: float
    friction: float
    net_pnl: float
    exit_reason: str
    spot_entry: float
    spot_exit: float


@dataclass
class FlyResult:
    cycles: list[FlyCycle] = field(default_factory=list)
    days: int = 0
    days_awaiting_data: int = 0
    days_outside_history: int = 0
    days_without_spot: int = 0
    skipped_no_data: int = 0
    skipped_no_fill: int = 0
    idle: dict[str, int] = field(default_factory=dict)
    lots: int = DEFAULT_LOTS
    price_source: str = ""
    spread_source: str = ""
    spot_source: str = ""

    def summary(self) -> dict[str, Any]:
        replayed = self.days - self.days_awaiting_data - self.days_outside_history - self.days_without_spot
        wins = sum(1 for c in self.cycles if c.net_pnl > 0)
        gross = round(sum(c.gross_pnl for c in self.cycles), 2)
        friction = round(sum(c.friction for c in self.cycles), 2)
        reasons: dict[str, int] = {}
        for c in self.cycles:
            reasons[c.exit_reason] = reasons.get(c.exit_reason, 0) + 1
        return {
            "days": self.days,
            "days_replayed": replayed,
            "days_awaiting_data": self.days_awaiting_data,
            "days_outside_history": self.days_outside_history,
            "days_without_spot": self.days_without_spot,
            "lots": self.lots,
            "cycles": len(self.cycles),
            "cycles_per_day": round(len(self.cycles) / replayed, 1) if replayed > 0 else 0.0,
            "win_rate_pct": round(100.0 * wins / len(self.cycles), 1) if self.cycles else 0.0,
            "gross_pnl": gross,
            "friction": friction,
            "net_pnl": round(gross - friction, 2),
            "exit_reasons": dict(sorted(reasons.items())),
            "skipped_no_data": self.skipped_no_data,
            "skipped_no_fill": self.skipped_no_fill,
            "idle": dict(sorted(self.idle.items())),
            "price_source": self.price_source,
            "spot_source": self.spot_source,
            "spread_source": self.spread_source,
        }


@dataclass
class _OpenFly:
    legs: tuple[FlyLegSpec, ...]
    entered_at: datetime.datetime
    atm: float
    width: float
    quantity: int
    credit: float
    entry_charges: float
    spot_entry: float
    last_price: dict[OptionKey, float]


def fly_legs(atm: float, width: float, expiry: datetime.date) -> tuple[FlyLegSpec, ...]:
    """Wings first -- the live entry sequence, so a failed wing never leaves a naked short."""
    return (
        FlyLegSpec(OptionKey(INDEX, expiry, atm + width, "call"), is_short=False),
        FlyLegSpec(OptionKey(INDEX, expiry, atm - width, "put"), is_short=False),
        FlyLegSpec(OptionKey(INDEX, expiry, atm, "call"), is_short=True),
        FlyLegSpec(OptionKey(INDEX, expiry, atm, "put"), is_short=True),
    )


def run_fly_backtest(
    futures_bars: Sequence[HistCandle],
    *,
    config: IronFlyScalperConfig,
    charges: ChargesModel,
    spread: SpreadStats,
    vix_by_day: dict[datetime.date, float],
    spot_bars: Sequence[HistCandle] = (),
    pricer: Any = None,
    lots: int = DEFAULT_LOTS,
    holidays: Optional[set[datetime.date]] = None,
    weekday_map: regime.WeekdayMap = regime.EXPIRY_WEEKDAY_MAP[INDEX],
    default_iv: float = 0.13,
) -> FlyResult:
    pricer = pricer or ModelPricer()
    spots = spot_map(spot_bars)
    spot_days = {ts.date() for ts in spots}
    result = FlyResult(
        lots=lots,
        price_source=pricer.source,
        spread_source=spread.describe(),
        spot_source="cash index" if spots else "futures close (no index bars cached)",
    )
    days = by_day(futures_bars)
    result.days = len(days)
    for day, day_bars in sorted(days.items()):
        if pricer.real and day not in spot_days:
            result.days_without_spot += 1
            continue
        vix = vix_by_day.get(day)
        sigma = (vix if vix and vix > 0 else default_iv * 100.0) / 100.0
        expiry = regime.next_expiry(day, weekday_map, holidays)
        _run_day(day_bars, day, expiry, sigma, vix, config, charges, spread, pricer, spots, lots, result)
    return result


def _run_day(
    day_bars: list[HistCandle],
    day: datetime.date,
    expiry: datetime.date,
    sigma: float,
    vix: Optional[float],
    config: IronFlyScalperConfig,
    charges: ChargesModel,
    spread: SpreadStats,
    pricer: Any,
    spots: dict,
    lots: int,
    result: FlyResult,
) -> None:
    pre_open, day_bars = split_session(day_bars)
    quantity = lots * regime.lot_size_for(INDEX, day)
    saved = checkpoint(result, ("skipped_no_data", "skipped_no_fill"))
    ledger = DayLedger()
    vwap = SessionVwap(pre_open)
    candles: list = []
    position: Optional[_OpenFly] = None
    is_expiry_day = day == expiry

    for i, bar in enumerate(day_bars):
        candles.append(to_candle(bar, vwap.add(bar)))
        now = bar.ts + MINUTE
        spot = spot_at(spots, bar.ts, bar.close)

        if position is not None:
            for leg in position.legs:
                status, leg_bar = pricer.bar(leg.key, bar.ts, spot=spot, sigma=sigma)
                if status == OK and leg_bar is not None:
                    position.last_price[leg.key] = leg_bar.close
            close_cost = _close_cost(position, spread)
            verdict = evaluate_exit(
                config,
                net_credit_per_unit=position.credit,
                close_cost_per_unit=close_cost,
                quantity=quantity,
                spot=spot,
                atm_strike_price=position.atm,
            )
            decision = gate(
                config,
                ledger,
                now,
                is_expiry_day=is_expiry_day,
                warm=True,
                has_open_position=True,
                position_exit=verdict,
                unrealized=(position.credit - close_cost) * quantity if close_cost is not None else 0.0,
                exit_at_window_end=True,
            )
            if decision.action == "exit":
                result.cycles.append(
                    _close(position, now, decision.reason_code, spot, charges, spread)
                )
                ledger.closed(result.cycles[-1].net_pnl, now)
                position = None
            continue

        decision = gate(
            config,
            ledger,
            now,
            is_expiry_day=is_expiry_day,
            warm=len(candles) >= WARM_CANDLES,
            has_open_position=False,
            entry_hold=reentry_blocked(
                config, now=now, last_closed_at=ledger.totals.last_closed_at, candles=candles
            ),
        )
        if decision.action != "enter":
            tally_idle(result.idle, decision)
            continue
        if i + 1 >= len(day_bars):
            continue  # no later minute to fill in

        atm = regime.atm_strike(spot, INDEX)
        width = wing_width_for(config, vix)
        legs = fly_legs(atm, width, expiry)
        fill_spot = spot_at(spots, now, day_bars[i + 1].close)
        # Query all four before judging any, so one pass records every leg it lacks.
        priced = [pricer.bar(leg.key, now, spot=fill_spot, sigma=sigma) for leg in legs]
        statuses = {status for status, _ in priced}
        if MISSING in statuses:
            rollback(result, saved)
            result.days_awaiting_data += 1
            return
        if NO_DATA in statuses:
            result.skipped_no_data += 1
            continue
        if NO_TRADE in statuses:
            result.skipped_no_fill += 1
            continue

        credit, entry_charges, last = 0.0, 0.0, {}
        for leg, (_, leg_bar) in zip(legs, priced):
            bid, ask = _touch(leg_bar.open, spread)
            fill = (simulate_sell if leg.is_short else simulate_buy)(bid, ask, quantity, charges)
            credit += fill.price if leg.is_short else -fill.price
            entry_charges += fill.charges
            last[leg.key] = leg_bar.open
        position = _OpenFly(
            legs=legs,
            entered_at=now,
            atm=atm,
            width=width,
            quantity=quantity,
            credit=round(credit, 2),
            entry_charges=round(entry_charges, 2),
            spot_entry=spot,
            last_price=last,
        )
        ledger.opened()

    if position is not None:
        # Only reachable if the bars end before the square-off; never leave a cycle unpriced.
        last_bar = day_bars[-1]
        at = last_bar.ts + MINUTE
        result.cycles.append(
            _close(position, at, "session_end", spot_at(spots, last_bar.ts, last_bar.close), charges, spread)
        )
        ledger.closed(result.cycles[-1].net_pnl, at)


def _touch(price: float, spread: SpreadStats) -> tuple[float, float]:
    half = spread.spread_for(price) / 2.0
    return max(0.05, round(price - half, 2)), round(price + half, 2)


def _close_cost(position: _OpenFly, spread: SpreadStats) -> Optional[float]:
    """Per unit, shorts bought at the ask and longs sold at the bid -- `cost_to_close`."""
    total = 0.0
    for leg in position.legs:
        price = position.last_price.get(leg.key)
        if price is None:
            return None
        bid, ask = _touch(price, spread)
        total += ask if leg.is_short else -bid
    return round(total, 2)


def _close(
    position: _OpenFly,
    at: datetime.datetime,
    reason: str,
    spot: float,
    charges: ChargesModel,
    spread: SpreadStats,
) -> FlyCycle:
    exit_cost, exit_charges = 0.0, 0.0
    for leg in position.legs:
        bid, ask = _touch(position.last_price[leg.key], spread)
        # Closing reverses each leg: a short is bought back, a long is sold.
        fill = (simulate_buy if leg.is_short else simulate_sell)(bid, ask, position.quantity, charges)
        exit_cost += fill.price if leg.is_short else -fill.price
        exit_charges += fill.charges
    gross = round((position.credit - exit_cost) * position.quantity, 2)
    friction = round(position.entry_charges + exit_charges, 2)
    return FlyCycle(
        entered_at=position.entered_at,
        exited_at=at,
        atm_strike=position.atm,
        wing_width=position.width,
        lots=position.quantity // max(1, regime.lot_size_for(INDEX, at.date())),
        quantity=position.quantity,
        net_credit_per_unit=position.credit,
        exit_cost_per_unit=round(exit_cost, 2),
        gross_pnl=gross,
        friction=friction,
        net_pnl=round(gross - friction, 2),
        exit_reason=reason,
        spot_entry=position.spot_entry,
        spot_exit=spot,
    )

