"""CAS Bingo's backtest (docs/signals-streamline-plan.md section 8).

Replays the bot's saved strategy on each expiry day in a range, per enabled index, on real ICICI
prices, reusing the live decisions wherever they are pure:

* **Triggers** are `triggers.evaluate_debit / evaluate_credit / evaluate_auction_credit /
  strangle_due`, fed the same inputs live feeds them: today's readings of the bot's signal
  series (rebuilt from ICICI futures bars exactly as `runtime._today_rows` rebuilds them), the
  futures' own open for the credit move, and the cash index history -- which carries the CAS
  indicative index from 15:15 -- for the auction rule, strikes and settlement.
* **Strikes** follow `plan._strikes`: distances as a % of the day's cash open (credit), the
  indicative index (auction credit) or spot (debit, strangle), snapped at-or-beyond on the strike
  grid (`regime.strike_beyond`, the rule `market.pick_strike` applies to a live chain).
* **Exits** are `execution.evaluate_exit` each minute on the minute's closes, then settlement at
  intrinsic value against the index after the auction, entry charges only -- as live settles.

Two things are approximations and each run says so in its notes:

* A credit spread is sized by its maximum loss per lot (width less credit) against the margin
  budget. Live sizes by ICICI's `margin_calculator`, which has no history.
* Liquidation (buying back profitable shorts to free margin) is not replayed: it acts on whatever
  else the account held that day, which a backtest cannot know.

One entry per index per expiry day, as live.
"""
from __future__ import annotations

import datetime
import math
from dataclasses import asdict, dataclass, field
from types import SimpleNamespace
from typing import Any, Optional, Sequence

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.timezone import IST
from icici_breeze_backend.app.domain.bots import CasBingoConfig
from icici_breeze_backend.app.services.bots.cas_bingo import triggers
from icici_breeze_backend.app.services.bots.cas_bingo.execution import evaluate_exit, settle_value_per_unit
from icici_breeze_backend.app.services.bots.cas_bingo.plan import STRUCTURES, structure_for
from icici_breeze_backend.app.services.bots.charges import ChargesModel
from icici_breeze_backend.app.services.bots.scalping import backtest_regime as regime
from icici_breeze_backend.app.services.bots.scalping.backtest_options import MISSING, NO_DATA, OK
from icici_breeze_backend.app.services.bots.scalping.backtest_store import HistCandle, OptionKey
from icici_breeze_backend.app.services.bots.scalping.paper import simulate_buy, simulate_sell
from icici_breeze_backend.app.services.bots.scalping.spreads import SpreadStats
from icici_breeze_backend.app.services.index_signal.series import apply_direction

MINUTE = datetime.timedelta(minutes=1)
SESSION_OPEN = datetime.time(9, 15)
# Derivatives stop trading at 15:30; the last minute an exit can use is the 15:29 bar.
LAST_TRADE = datetime.time(15, 29)
# The auction matches 15:30-15:35; the index is frozen at its close from then (verified).
SETTLE_BY = datetime.time(15, 40)

NOTES = (
    "Credit spreads are sized by their maximum loss per lot (width less credit) against the "
    "margin budget; live sizes them with ICICI's margin calculator, which has no history.",
    "Liquidation of other positions to free margin is not replayed: it depends on what else the "
    "account held that day.",
)


@dataclass(frozen=True)
class CasTrade:
    day: datetime.date
    index: str
    structure: str
    legs: str  # e.g. "SELL 25100 CE / BUY 25200 CE"
    lots: int
    quantity: int
    entered_at: datetime.datetime
    exited_at: datetime.datetime
    net_entry_per_unit: float
    exit_value_per_unit: float
    gross_pnl: float
    friction: float
    net_pnl: float
    exit_reason: str
    trigger: str
    reference: float


@dataclass
class CasResult:
    cycles: list[CasTrade] = field(default_factory=list)
    days: int = 0
    days_awaiting_data: int = 0
    days_without_index: int = 0
    no_trigger: int = 0
    skipped_no_data: int = 0
    skipped_unaffordable: int = 0
    decisions: list[dict[str, Any]] = field(default_factory=list)
    price_source: str = ""
    notes: tuple[str, ...] = NOTES

    def summary(self) -> dict[str, Any]:
        replayed = self.days - self.days_awaiting_data - self.days_without_index
        wins = sum(1 for c in self.cycles if c.net_pnl > 0)
        gross = round(sum(c.gross_pnl for c in self.cycles), 2)
        friction = round(sum(c.friction for c in self.cycles), 2)
        return {
            "days": self.days,
            "days_replayed": replayed,
            "days_awaiting_data": self.days_awaiting_data,
            "days_without_index": self.days_without_index,
            "cycles": len(self.cycles),
            "win_rate_pct": round(100.0 * wins / len(self.cycles), 1) if self.cycles else 0.0,
            "gross_pnl": gross,
            "friction": friction,
            "net_pnl": round(gross - friction, 2),
            "no_trigger_days": self.no_trigger,
            "skipped_no_data": self.skipped_no_data,
            "skipped_unaffordable": self.skipped_unaffordable,
            "price_source": self.price_source,
            "notes": list(self.notes),
        }


def _epoch(moment: datetime.datetime) -> float:
    return moment.replace(tzinfo=IST).timestamp()


def _rows_until(day_readings: list[tuple[datetime.datetime, HistCandle, dict[str, Any]]],
                now: datetime.datetime, direction: str) -> list[dict[str, Any]]:
    """Today's readings seen by `now`, in the shape `triggers` reads (`runtime._today_rows`)."""
    rows: list[dict[str, Any]] = []
    prev: Optional[str] = None
    for start, bar, snap in day_readings:
        if start + MINUTE > now:
            break
        turned = apply_direction(snap, direction)
        state = str(turned.get("state") or "unavailable")
        rows.append({"ts": _epoch(start + MINUTE), "kind": "transition" if state != prev else "sample",
                     "state": state, "signal": turned.get("signal"), "spot": bar.close})
        prev = state
    return rows


def _legs_for(structure: str, config: CasBingoConfig, index: str, reference: float,
              credit_pcts: Optional[tuple[float, float]] = None) -> Optional[list[dict[str, Any]]]:
    """`plan._strikes` on the strike grid: distances as % of `reference`, snapped away from it."""
    def up(pct: float) -> float:
        return regime.strike_beyond(reference * (1 + pct / 100.0), index, up=True)

    def down(pct: float) -> float:
        return regime.strike_beyond(reference * (1 - pct / 100.0), index, up=False)

    c, d, s = config.credit, config.debit, config.strangle
    inner, outer = credit_pcts or (c.inner_pct, c.outer_pct)
    if structure == "bear_call_credit":
        legs = [("call", up(inner), cfg.SELL), ("call", up(outer), cfg.BUY)]
    elif structure == "bull_put_credit":
        legs = [("put", down(inner), cfg.SELL), ("put", down(outer), cfg.BUY)]
    elif structure == "bull_call_debit":
        legs = [("call", up(d.inner_pct), cfg.BUY), ("call", up(d.outer_pct), cfg.SELL)]
    elif structure == "bear_put_debit":
        legs = [("put", down(d.inner_pct), cfg.BUY), ("put", down(d.outer_pct), cfg.SELL)]
    else:
        legs = [("call", up(s.call_pct), cfg.BUY), ("put", down(s.put_pct), cfg.BUY)]
    if STRUCTURES[structure][0] != "strangle" and legs[0][1] == legs[1][1]:
        return None
    return [{"right": r, "strike_price": k, "action": a} for r, k, a in legs]


def _touch(price: float, spread: SpreadStats) -> tuple[float, float]:
    half = spread.spread_for(price) / 2.0
    return max(0.05, round(price - half, 2)), round(price + half, 2)


def run_cas_backtest(
    *,
    config: CasBingoConfig,
    index: str,
    days: Sequence[datetime.date],
    index_bars: Sequence[HistCandle],
    futures_bars: Sequence[HistCandle],
    readings: dict[datetime.datetime, dict[str, Any]],
    charges: ChargesModel,
    spread: SpreadStats,
    pricer: Any,
    record_decisions: bool = False,
) -> CasResult:
    """One index's expiry days. `readings` is the bot's signal series for this index as
    published (`backtest_common.series_readings`); `index_bars` the cash index, `futures_bars`
    the futures the series reads."""
    result = CasResult(price_source=getattr(pricer, "source", ""))
    cash_by_day: dict[datetime.date, list[HistCandle]] = {}
    for b in index_bars:
        cash_by_day.setdefault(b.ts.date(), []).append(b)
    fut_by_day: dict[datetime.date, list[HistCandle]] = {}
    for b in futures_bars:
        fut_by_day.setdefault(b.ts.date(), []).append(b)
    for day in days:
        result.days += 1
        cash = sorted(cash_by_day.get(day) or [], key=lambda b: b.ts)
        if not cash:
            result.days_without_index += 1
            continue
        _run_day(day, index, config, cash, sorted(fut_by_day.get(day) or [], key=lambda b: b.ts),
                 readings, charges, spread, pricer, result, record_decisions)
    return result


def _run_day(
    day: datetime.date,
    index: str,
    config: CasBingoConfig,
    cash: list[HistCandle],
    futures: list[HistCandle],
    readings: dict[datetime.datetime, dict[str, Any]],
    charges: ChargesModel,
    spread: SpreadStats,
    pricer: Any,
    result: CasResult,
    record: bool,
) -> None:
    session = [b for b in cash if b.ts.time() >= SESSION_OPEN]
    if not session:
        result.days_without_index += 1
        return
    cash_at = {b.ts: b for b in cash}
    cash_open = session[0].open
    fut_session = [b for b in futures if SESSION_OPEN <= b.ts.time()]
    futures_open = fut_session[0].open if fut_session else None
    day_readings = [(b.ts, b, readings[b.ts]) for b in fut_session if b.ts in readings]
    windows = [(w.start, w.end) for w in config.windows()]
    direction = config.signal.direction if config.strategy == "debit_spread" else "follow"
    lot = regime.lot_size_for(index, day)
    decisions_mark = len(result.decisions)

    start = datetime.datetime.combine(day, datetime.time.fromisoformat(config.pre_cas_window.start))
    end = datetime.datetime.combine(day, datetime.time.fromisoformat(config.cas_window.end))
    minutes = int((end - start).total_seconds() // 60) + 1
    for k in range(max(0, minutes)):
        now = start + k * MINUTE  # the bar that closed at `now` started a minute earlier
        hhmm = now.strftime("%H:%M")
        spot_bar = cash_at.get(now - MINUTE)
        spot = spot_bar.close if spot_bar else None
        auction = (config.strategy == "credit_spread"
                   and config.cas_window.start <= hhmm < config.cas_window.end)
        if config.strategy == "long_strangle":
            verdict = triggers.strangle_due(_epoch(now), config.strangle.entry_time_ist, windows)
        elif auction:
            if hhmm < triggers.AUCTION_ORDER_ENTRY_IST:
                verdict = triggers.Verdict(None, "Waiting for auction order entry.")
            else:
                verdict = triggers.evaluate_auction_credit(indicative=spot, day_open=cash_open,
                                                           now_ts=_epoch(now))
        else:
            rows = _rows_until(day_readings, now, direction)
            live_state = rows[-1]["state"] if rows and rows[-1]["ts"] >= _epoch(now) - 60 else "unavailable"
            if live_state == "unavailable":
                verdict = triggers.Verdict(None, "The signal is unavailable.")
            elif config.strategy == "debit_spread":
                verdict = triggers.evaluate_debit(rows, live_state=live_state, now_ts=_epoch(now),
                                                  windows=windows,
                                                  sustain_seconds=config.debit.sustain_minutes * 60.0)
            else:
                verdict = triggers.evaluate_credit(rows, live_state=live_state, now_ts=_epoch(now),
                                                   windows=windows, day_open=futures_open,
                                                   move_trigger_pct=config.credit.move_trigger_pct)
        if record:
            result.decisions.append({"date": day.isoformat(), "index": index, "time": hhmm,
                                     "index_level": spot, "entered": verdict.trigger is not None,
                                     "reason": verdict.reason or (verdict.trigger.text if verdict.trigger else "")})
        if verdict.trigger is None:
            continue

        structure = structure_for(config.strategy, verdict.trigger.right)
        family = STRUCTURES[structure][0]
        credit_pcts = None
        if family == "credit" and auction:
            reference = float(verdict.trigger.level or spot or 0)
            c = config.credit
            credit_pcts = (c.auction_gap_pct, c.auction_gap_pct + (c.outer_pct - c.inner_pct))
        elif family == "credit":
            reference = float(cash_open)
        else:
            reference = float(spot or 0)
        if reference <= 0:
            continue
        legs = _legs_for(structure, config, index, reference, credit_pcts)
        if legs is None:
            continue
        expiry = day
        keys = [OptionKey(index, expiry, float(l["strike_price"]), l["right"]) for l in legs]
        priced = [pricer.bar(k, now) for k in keys]
        statuses = {s for s, _ in priced}
        if MISSING in statuses:
            # Everything after depends on this entry: the day waits for the data.
            del result.decisions[decisions_mark:]
            result.days_awaiting_data += 1
            return
        if NO_DATA in statuses or any(s != OK for s in statuses):
            result.skipped_no_data += 1
            continue

        net = 0.0
        touches = []
        for leg, (_s, bar) in zip(legs, priced):
            bid, ask = _touch(float(bar.open), spread)
            touches.append((bid, ask))
            net += bid if leg["action"] == cfg.SELL else -ask
        net = round(net, 2)
        if family == "credit":
            if net <= 0:
                continue
            width = abs(legs[0]["strike_price"] - legs[1]["strike_price"])
            if auction and 100.0 * net / width < config.credit.auction_min_credit_pct:
                continue
            max_loss = (width - net) * lot
            lots = int(math.floor(config.credit.margin_lakhs * 100_000.0 / max_loss)) if max_loss > 0 else 0
        else:
            debit = -net
            if debit <= 0:
                continue
            budget = config.debit.premium_budget_inr if family == "debit" else config.strangle.premium_budget_inr
            lots = int(math.floor(budget / (debit * lot)))
        if lots < 1:
            result.skipped_unaffordable += 1
            return  # live resolves "below one lot" as the day's answer
        quantity = lots * lot
        entry_charges = 0.0
        for leg, (bid, ask) in zip(legs, touches):
            fill = (simulate_sell if leg["action"] == cfg.SELL else simulate_buy)(bid, ask, quantity, charges)
            entry_charges += fill.charges
        cycle = SimpleNamespace(
            legs=[{**l, "quantity": quantity} for l in legs],
            detail={"net_entry_per_unit": net, "family": family},
        )
        exit_at, value, reason = _exit_or_settle(cycle, config, keys, legs, now, cash, spread, pricer)
        gross = round((net + value) * quantity, 2)
        result.cycles.append(CasTrade(
            day=day, index=index, structure=structure,
            legs=" / ".join(f"{l['action'].upper()} {int(l['strike_price'])} {'CE' if l['right'] == 'call' else 'PE'}"
                            for l in legs),
            lots=lots, quantity=quantity, entered_at=now, exited_at=exit_at,
            net_entry_per_unit=net, exit_value_per_unit=value, gross_pnl=gross,
            friction=round(entry_charges, 2), net_pnl=round(gross - entry_charges, 2),
            exit_reason=reason, trigger=verdict.trigger.text, reference=round(reference, 2),
        ))
        return  # one entry per index per day
    result.no_trigger += 1


def _exit_or_settle(cycle: Any, config: CasBingoConfig, keys: list[OptionKey], legs: list[dict[str, Any]],
                    entered: datetime.datetime, cash: list[HistCandle], spread: SpreadStats,
                    pricer: Any) -> tuple[datetime.datetime, float, str]:
    """(when, close value per unit, reason): the bot's own exit each minute, else settlement."""
    t = entered
    last_trade = datetime.datetime.combine(entered.date(), LAST_TRADE)
    while t <= last_trade:
        value = 0.0
        ok = True
        for leg, key in zip(legs, keys):
            status, bar = pricer.bar(key, t)
            if status != OK or bar is None:
                ok = False
                break
            bid, ask = _touch(float(bar.close), spread)
            value += -ask if leg["action"] == cfg.SELL else bid
        if ok:
            verdict = evaluate_exit(config, cycle, round(value, 2))
            if verdict is not None:
                return t + MINUTE, round(value, 2), verdict[0]
        t += MINUTE
    settle_until = datetime.datetime.combine(entered.date(), SETTLE_BY)
    levels = [b for b in cash if b.ts < settle_until]
    level = levels[-1].close if levels else cash[-1].close
    return settle_until, settle_value_per_unit(cycle.legs, level), "expired_settled"


def trade_rows(result: CasResult) -> list[dict[str, Any]]:
    import json

    return [json.loads(json.dumps(asdict(c), default=str)) for c in result.cycles]
