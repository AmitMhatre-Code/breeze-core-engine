"""Backtest for Bot 2, the expiry-day index writer (docs/bots-mvp-plan.md section 4a).

On every NIFTY or SENSEX expiry day in the cached history, it sells each shortlisted strategy
(naked CE, naked PE, short strangle) **side by side**, at the bot's entry time and safety
distance, and manages each the way the armed Strategy Group rule would: a loss stop at N times
the premium collected, a per-leg price target at a share of the premium (every short leg at or
below it), otherwise held to expiry and settled at intrinsic value against the index close.

**No ranking, and no margin-based sizing** (decided 2026-09-13). The live bot picks the
strategy with the best premium per rupee of margin and sizes it to a share of free margin;
margin has no history, so the backtest reports each strategy at a fixed lot count instead,
and the reader compares them. It never claims a return on margin.

Prices are real traded bars or Black-Scholes, never mixed (see `scalping.backtest_options`).
Entries fill at the entry minute's open, sold at the bid (traded price less half the observed
spread) with paper-mode slippage. Monitoring is per minute: a single leg is checked at its
open, high, low and close, the adverse high first; a strangle's two legs peak at opposite
moments, so it is checked at each minute's open and close only, which can miss a breach that
reverses inside one minute.

Settling at intrinsic charges an ITM leg as a buy at its intrinsic value. That is an
approximation: exchange settlement carries its own STT, which the cost model has no line for.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Mapping, Optional, Sequence, Union

from icici_breeze_backend.app.domain.bots import ExpiryIndexWriterConfig
from icici_breeze_backend.app.services.bots.charges import ChargesModel
from icici_breeze_backend.app.services.bots.scalping import backtest_regime as regime
from icici_breeze_backend.app.services.bots.scalping.backtest_options import (
    MISSING,
    NO_DATA,
    NO_TRADE,
    OK,
    ModelPricer,
    session_bounds,
)
from icici_breeze_backend.app.services.bots.scalping.backtest_store import HistCandle, OptionKey
from icici_breeze_backend.app.services.bots.scalping.paper import simulate_buy, simulate_sell
from icici_breeze_backend.app.services.bots.scalping.spreads import SpreadStats

STRATEGY_RIGHTS: dict[str, tuple[str, ...]] = {
    "naked_ce": ("call",),
    "naked_pe": ("put",),
    "short_strangle": ("call", "put"),
    # Hedged (docs/bot2-hedged-shapes-plan.md): the same shorts, each with a wing bought beyond.
    "bear_call_spread": ("call",),
    "bull_put_spread": ("put",),
    "iron_condor": ("call", "put"),
}
HEDGED = ("bear_call_spread", "bull_put_spread", "iron_condor")
NAKED = ("naked_ce", "naked_pe", "short_strangle")
DEFAULT_LOTS = 1

EXIT_STOP = "group_stop_loss_hit"
EXIT_TARGET = "group_target_hit"
EXIT_EXPIRED = "expired"


@dataclass(frozen=True)
class ExpiryLeg:
    right: str
    strike: float
    entry_price: float
    touch_bid: float
    exit_price: float = 0.0
    # "buy" for a hedged shape's wing; every other leg is sold.
    action: str = "sell"


@dataclass(frozen=True)
class ExpiryTrade:
    index: str
    day: datetime.date
    strategy: str
    legs: tuple[ExpiryLeg, ...]
    lots: int
    quantity: int
    premium_inr: float
    loss_limit_inr: float
    target_price: Optional[float]
    exit_reason: str
    exited_at: datetime.datetime
    gross_pnl: float
    friction: float
    net_pnl: float
    spot_entry: float
    spot_close: float


@dataclass
class ExpiryResult:
    trades: list[ExpiryTrade] = field(default_factory=list)
    expiry_days: int = 0
    days_awaiting_data: int = 0
    days_outside_history: int = 0
    days_without_spot: int = 0
    skipped_no_data: int = 0
    skipped_no_fill: int = 0
    # Days the premium gate refused, or had no reading for (docs/premium-gate-plan.md).
    skipped_premium: int = 0
    lots: Any = DEFAULT_LOTS  # one count, or {"NIFTY naked_pe": 3, ...}
    price_source: str = ""
    spread_source: str = ""

    def summary(self) -> dict[str, Any]:
        by_strategy: dict[str, dict[str, Any]] = {}
        for t in self.trades:
            s = by_strategy.setdefault(
                f"{t.index} {t.strategy}",
                {"trades": 0, "wins": 0, "premium": 0.0, "gross_pnl": 0.0, "friction": 0.0,
                 "net_pnl": 0.0, "worst_net": 0.0, "exits": {}},
            )
            s["trades"] += 1
            s["wins"] += 1 if t.net_pnl > 0 else 0
            s["premium"] = round(s["premium"] + t.premium_inr, 2)
            s["gross_pnl"] = round(s["gross_pnl"] + t.gross_pnl, 2)
            s["friction"] = round(s["friction"] + t.friction, 2)
            s["net_pnl"] = round(s["net_pnl"] + t.net_pnl, 2)
            s["worst_net"] = round(min(s["worst_net"], t.net_pnl), 2)
            s["exits"][t.exit_reason] = s["exits"].get(t.exit_reason, 0) + 1
        for s in by_strategy.values():
            s["win_rate_pct"] = round(100.0 * s["wins"] / s["trades"], 1) if s["trades"] else 0.0
        return {
            "expiry_days": self.expiry_days,
            "days_awaiting_data": self.days_awaiting_data,
            "days_outside_history": self.days_outside_history,
            "days_without_spot": self.days_without_spot,
            "lots": self.lots,
            "skipped_no_data": self.skipped_no_data,
            "skipped_no_fill": self.skipped_no_fill,
            "skipped_premium": self.skipped_premium,
            "by_strategy": dict(sorted(by_strategy.items())),
            "price_source": self.price_source,
            "spread_source": self.spread_source,
        }


_COUNTERS = (
    "expiry_days",
    "days_awaiting_data",
    "days_outside_history",
    "days_without_spot",
    "skipped_no_data",
    "skipped_no_fill",
    "skipped_premium",
)


def merge_expiry_results(
    results: Sequence[ExpiryResult], *, price_source: str, spread_source: str
) -> ExpiryResult:
    """One result from one run per index. Day counts add up across indices, so they read as
    index-days: two indices over one week is ten index-days, of which two are expiries."""
    out = ExpiryResult(lots={}, price_source=price_source, spread_source=spread_source)
    for r in results:
        out.trades.extend(r.trades)
        for name in _COUNTERS:
            setattr(out, name, getattr(out, name) + getattr(r, name))
    return out


def expiry_days(
    index: str,
    start: datetime.date,
    end: datetime.date,
    holidays: Optional[set[datetime.date]] = None,
) -> list[datetime.date]:
    weekday_map = regime.EXPIRY_WEEKDAY_MAP[index]
    return [
        d
        for d in regime.trading_days(start, end, holidays)
        if regime.next_expiry(d, weekday_map, holidays) == d
    ]


def run_expiry_backtest(
    *,
    index: str,
    days: Sequence[datetime.date],
    spot_bars: Sequence[HistCandle],
    config: ExpiryIndexWriterConfig,
    charges: ChargesModel,
    spread: SpreadStats,
    strategies: Sequence[str] = NAKED,
    pricer: Any = None,
    lots: Union[int, Mapping[str, int]] = DEFAULT_LOTS,
    vix_by_day: Optional[dict[datetime.date, float]] = None,
    default_iv: float = 0.13,
    on_day: Optional[Callable[[datetime.date], None]] = None,
    premium: Any = None,
) -> ExpiryResult:
    """`lots` is one count for every strategy, or a count per strategy -- the Backtest page
    sizes each from today's margin, so a strangle and a naked put get different counts.

    `premium` (a `premium_gate.replay.ReplayPremium`) replays the premium gate and strikes set
    by implied move; without one, both fail closed as they do live."""
    pricer = pricer or ModelPricer()
    result = ExpiryResult(lots=lots, price_source=pricer.source, spread_source=spread.describe())
    spots_by_day: dict[datetime.date, dict[datetime.datetime, HistCandle]] = {}
    for bar in spot_bars:
        spots_by_day.setdefault(bar.date, {})[bar.ts] = bar
    for day in days:
        if on_day is not None:
            on_day(day)
        result.expiry_days += 1
        spots = spots_by_day.get(day) or {}
        vix = (vix_by_day or {}).get(day)
        sigma = (vix if vix and vix > 0 else default_iv * 100.0) / 100.0
        _run_day(index, day, spots, config, charges, spread, strategies, pricer, lots, sigma, result,
                 premium=premium)
    return result


def _run_day(
    index: str,
    day: datetime.date,
    spots: dict[datetime.datetime, HistCandle],
    config: ExpiryIndexWriterConfig,
    charges: ChargesModel,
    spread: SpreadStats,
    strategies: Sequence[str],
    pricer: Any,
    lots: Union[int, Mapping[str, int]],
    sigma: float,
    result: ExpiryResult,
    *,
    premium: Any = None,
) -> None:
    hh, mm = (int(x) for x in config.entry_time_ist.split(":"))
    entry_at = datetime.datetime.combine(day, datetime.time(hh, mm))
    spot_entry = _spot_open(spots, entry_at)
    closing = [b for ts, b in sorted(spots.items()) if b.close > 0]
    if spot_entry is None or not closing:
        result.days_without_spot += 1
        return
    spot_close = closing[-1].close

    leg_cfg = config.indices[index]
    by_implied = leg_cfg.distance_basis == "implied_move"
    implied: Optional[float] = None
    wants_wings = any(s in HEDGED for s in strategies)
    if config.premium_gate.enabled or by_implied or wants_wings:
        # The live order: read the ATM pair, judge the gate, then place strikes by that reading.
        from icici_breeze_backend.app.services.premium_gate import reading as premium_reading
        from icici_breeze_backend.app.services.premium_gate.gate import refusal
        from icici_breeze_backend.app.services.premium_gate.replay import bar_price

        atm = regime.atm_strike(spot_entry, index)
        pair = [pricer.bar(OptionKey(index, day, atm, r), entry_at, spot=spot_entry, sigma=sigma)
                for r in ("call", "put")]
        if any(status == MISSING for status, _ in pair):
            result.days_awaiting_data += 1
            return
        if premium is not None:
            reading, implied = premium.reading(entry_at, day, spot_entry, atm,
                                               bar_price(pair[0]), bar_price(pair[1]))
        else:
            reading = premium_reading.Reading(None, None, None, premium_reading.REASON_NO_HISTORY)
        if refusal(reading, config.premium_gate, "sell") is not None or (by_implied and implied is None):
            result.skipped_premium += 1
            return
    if wants_wings and implied is None:
        # No wing without the implied move: the hedged shapes drop out, the naked ones trade.
        result.skipped_no_data += sum(1 for s in strategies if s in HEDGED)
        strategies = [s for s in strategies if s not in HEDGED]
    if by_implied and implied is not None:
        from icici_breeze_backend.app.services.premium_gate.reading import strike_target

        targets = {"call": strike_target(spot_entry, implied, leg_cfg.implied_multiple_ce, up=True),
                   "put": strike_target(spot_entry, implied, leg_cfg.implied_multiple_pe, up=False)}
    else:
        targets = {"call": spot_entry * (1 + leg_cfg.safety_pct_ce / 100.0),
                   "put": spot_entry * (1 - leg_cfg.safety_pct_pe / 100.0)}
    strikes = {
        "call": regime.strike_beyond(targets["call"], index, up=True),
        "put": regime.strike_beyond(targets["put"], index, up=False),
    }
    rights = sorted({r for s in strategies for r in STRATEGY_RIGHTS[s]})
    keys = {r: OptionKey(index, day, strikes[r], r) for r in rights}
    wing_rights = sorted({r for s in strategies if s in HEDGED for r in STRATEGY_RIGHTS[s]})
    wing_keys = {}
    if wing_rights:
        from icici_breeze_backend.app.services.premium_gate.reading import strike_target

        step = regime.STRIKE_STEP[index]
        for r in wing_rights:
            up = r == "call"
            wing = regime.strike_beyond(
                strike_target(strikes[r], implied, leg_cfg.wing_multiple, up=up), index, up=up)
            # At least one listed strike beyond the short, as live.
            wing = max(wing, strikes[r] + step) if up else min(wing, strikes[r] - step)
            wing_keys[r] = OptionKey(index, day, wing, r)
    # Every leg queried before any is judged, so one pass records every contract it lacks.
    entry_bars = {r: pricer.bar(keys[r], entry_at, spot=spot_entry, sigma=sigma) for r in rights}
    wing_bars = {r: pricer.bar(wing_keys[r], entry_at, spot=spot_entry, sigma=sigma) for r in wing_rights}
    if any(status == MISSING for status, _ in list(entry_bars.values()) + list(wing_bars.values())):
        result.days_awaiting_data += 1
        return

    lot_size = regime.lot_size_for(index, day)
    exchange = regime.OPTION_EXCHANGE[index]
    for strategy in strategies:
        legs = STRATEGY_RIGHTS[strategy]
        hedged = strategy in HEDGED
        statuses = {entry_bars[r][0] for r in legs} | ({wing_bars[r][0] for r in legs} if hedged else set())
        if NO_DATA in statuses:
            result.skipped_no_data += 1
            continue
        if NO_TRADE in statuses:
            result.skipped_no_fill += 1
            continue
        n_lots = int(lots.get(strategy, DEFAULT_LOTS) if isinstance(lots, Mapping) else lots)
        result.trades.append(
            _trade(
                index, day, strategy,
                ([(r, wing_keys[r], wing_bars[r][1], "buy") for r in legs] if hedged else [])
                + [(r, keys[r], entry_bars[r][1], "sell") for r in legs],
                entry_at, spot_entry, spot_close, spots, config, charges, spread, pricer,
                n_lots * lot_size, n_lots, exchange, sigma,
            )
        )


def _spot_open(spots: dict[datetime.datetime, HistCandle], at: datetime.datetime) -> Optional[float]:
    bar = spots.get(at)
    if bar is not None and bar.open > 0:
        return bar.open
    earlier = [b for ts, b in spots.items() if ts < at and b.close > 0]
    return max(earlier, key=lambda b: b.ts).close if earlier else None


def _touch(price: float, spread: SpreadStats) -> tuple[float, float]:
    half = spread.spread_for(price) / 2.0
    return max(0.05, round(price - half, 2)), round(price + half, 2)


def _exchange_charges(fill: Any, charges: ChargesModel, *, is_buy: bool, exchange: str) -> Any:
    """Paper fills charge at NSE rates; a SENSEX leg is billed at BSE's."""
    return replace(
        fill, charges=charges.leg_charges(fill.price, fill.quantity, is_buy=is_buy, exchange_code=exchange)
    )


def _trade(
    index: str,
    day: datetime.date,
    strategy: str,
    legs: list[tuple[str, OptionKey, HistCandle, str]],
    entry_at: datetime.datetime,
    spot_entry: float,
    spot_close: float,
    spots: dict[datetime.datetime, HistCandle],
    config: ExpiryIndexWriterConfig,
    charges: ChargesModel,
    spread: SpreadStats,
    pricer: Any,
    quantity: int,
    lots: int,
    exchange: str,
    sigma: float,
) -> ExpiryTrade:
    fills, quotes = [], []
    for right, key, bar, action in legs:
        bid, ask = _touch(bar.open, spread)
        if action == "buy":
            # A hedged shape's wing, bought at the ask first, as live.
            fills.append(_exchange_charges(simulate_buy(bid, ask, quantity, charges), charges, is_buy=True, exchange=exchange))
            quotes.append(ask)
        else:
            fills.append(_exchange_charges(simulate_sell(bid, ask, quantity, charges), charges, is_buy=False, exchange=exchange))
            quotes.append(bid)
    signs = [1 if a == "sell" else -1 for *_, a in legs]
    # The rule is armed off the plan's quotes, as `_arm_exit` does, not off the slipped fills:
    # net premium (shorts' bids less wings' asks), and a price target on the shorts only.
    premium = round(sum(sg * q for sg, q in zip(signs, quotes)) * quantity, 2)
    loss_limit = config.loss_limit_premium_multiple * premium
    targets = [config.profit_target_price_for(q) for q, sg in zip(quotes, signs) if sg > 0]
    target = min(targets) if targets and all(t is not None for t in targets) else None

    keys = [key for _, key, _, _ in legs]
    exit_reason, exit_at, exit_ltps = _monitor(
        keys, [f.price for f in fills], entry_at, spots, quantity, loss_limit, target, pricer, sigma,
        len(legs) == 1, signs=signs,
    )
    exit_prices, exit_charges = [], 0.0
    if exit_ltps is None:
        exit_reason, exit_at = EXIT_EXPIRED, session_bounds(day)[1]
        for key, sg in zip(keys, signs):
            intrinsic = max(0.0, spot_close - key.strike) if key.right == "call" else max(0.0, key.strike - spot_close)
            exit_prices.append(round(intrinsic, 2))
            if intrinsic > 0:
                exit_charges += charges.leg_charges(intrinsic, quantity, is_buy=sg > 0, exchange_code=exchange)
    else:
        for ltp, sg in zip(exit_ltps, signs):
            bid, ask = _touch(ltp, spread)
            close_fill = simulate_buy(bid, ask, quantity, charges) if sg > 0 else simulate_sell(bid, ask, quantity, charges)
            out = _exchange_charges(close_fill, charges, is_buy=sg > 0, exchange=exchange)
            exit_prices.append(out.price)
            exit_charges += out.charges

    gross = round(sum(sg * (f.price - x) * quantity for sg, f, x in zip(signs, fills, exit_prices)), 2)
    friction = round(sum(f.charges for f in fills) + exit_charges, 2)
    return ExpiryTrade(
        index=index,
        day=day,
        strategy=strategy,
        legs=tuple(
            ExpiryLeg(right=r, strike=k.strike, entry_price=f.price, touch_bid=b, exit_price=x, action=a)
            for (r, k, _, a), f, b, x in zip(legs, fills, quotes, exit_prices)
        ),
        lots=lots,
        quantity=quantity,
        premium_inr=premium,
        loss_limit_inr=round(loss_limit, 2),
        target_price=target,
        exit_reason=exit_reason,
        exited_at=exit_at,
        gross_pnl=gross,
        friction=friction,
        net_pnl=round(gross - friction, 2),
        spot_entry=spot_entry,
        spot_close=spot_close,
    )


def _monitor(
    keys: list[OptionKey],
    entry_prices: list[float],
    entry_at: datetime.datetime,
    spots: dict[datetime.datetime, HistCandle],
    quantity: int,
    loss_limit: float,
    target: Optional[float],
    pricer: Any,
    sigma: float,
    single_leg: bool,
    *,
    signs: Optional[list[int]] = None,
) -> tuple[str, datetime.datetime, Optional[list[float]]]:
    """Walk the minutes to the close. (reason, at, exit LTPs) or (_, _, None) to expire.
    `signs` is +1 for a sold leg, -1 for a bought wing; the target reads sold legs only."""
    signs = signs or [1] * len(keys)
    last = list(entry_prices)
    minute = entry_at
    close = session_bounds(entry_at.date())[1]
    while minute < close:
        spot_bar = spots.get(minute)
        spot = spot_bar.close if spot_bar is not None else 0.0
        bars = []
        for k in keys:
            status, bar = pricer.bar(k, minute, spot=spot or k.strike, sigma=sigma)
            bars.append(bar if status == OK else None)
        # The fill happened at the entry minute's open, so that minute is judged from its high.
        moments = ("open", "high", "low", "close") if single_leg else ("open", "close")
        if minute == entry_at:
            moments = moments[1:]
        for moment in moments:
            ltps = [
                getattr(bar, moment) if bar is not None else prev for bar, prev in zip(bars, last)
            ]
            pnl = sum(sg * (e - p) * quantity for sg, e, p in zip(signs, entry_prices, ltps))
            if loss_limit > 0 and pnl <= -abs(loss_limit):
                return EXIT_STOP, minute, ltps
            if target is not None and all(p <= target for p, sg in zip(ltps, signs) if sg > 0):
                return EXIT_TARGET, minute, ltps
        last = [bar.close if bar is not None else prev for bar, prev in zip(bars, last)]
        minute += datetime.timedelta(minutes=1)
    return EXIT_EXPIRED, close, None
