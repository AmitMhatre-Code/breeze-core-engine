"""Dynamic Iron Condor backtest: the live engine, replayed on ICICI's traded prices.

docs/dynamic-iron-condor-plan.md section 5. Every decision is `engine.decide` -- the same call
the Portfolio card and the bot make -- so a backtest result is a statement about the rules as
they will trade, not about a re-implementation of them.

How a check is replayed
-----------------------
Only at the two scheduled checks, because the engine decides nowhere else. Spot is the cash
index's 1-minute bar; each option's price is its 5-minute traded bar at the check (the bar
the check falls in, if it traded, else the last bar to close before it). Prices become a
two-sided "book" a modelled spread wide (`spreads.spread_stats`, the paper-mode samples),
fills take a further adverse `slippage_spread_fraction` of that spread, and charges come from
`bots/charges.py` -- all as the other replays do.

Which contracts a check sees is decided by the state, never by what happens to be cached, so
a result never depends on fetch history: the ATM pair (for the forward and ATM volatility),
every held leg, and whatever the engine's own answer names. When the answer names a contract
the chain did not quote, the check is decided again with it quoted, until the answer is
stable. Deltas for strikes nobody quoted come off the smile those contracts make -- thinner
than a live chain's, and the main approximation here.

Demand-driven and resumable
---------------------------
A contract the cache has never fetched stops the replay at that check and comes back as
`Need`s (12-session blocks of 5-minute bars, under ICICI's 1,000-row cap). After they are
fetched, `run()` carries on **from the same check**: data for checks already passed cannot
change, so nothing before it is replayed again.

What it cannot model: order-book depth and bid/ask (neither is in ICICI's history), margin
(sized once from today's levels, #36), intraday moves between checks -- including the
max-loss stop, which the user chose to evaluate only at checks.
"""
from __future__ import annotations

import datetime
import logging
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Literal, Optional, Protocol

from icici_breeze_backend.app.domain.condor import CondorSettings
from icici_breeze_backend.app.services.bots.charges import ChargesModel
from icici_breeze_backend.app.services.bots.scalping import backtest_regime as regime
from icici_breeze_backend.app.services.bots.scalping import backtest_store as store
from icici_breeze_backend.app.services.bots.scalping.backtest_store import Need, OptionKey
from icici_breeze_backend.app.services.bots.scalping.spreads import SpreadStats
from icici_breeze_backend.app.services.condor.engine import decide, entry_orders, entry_strikes
from icici_breeze_backend.app.services.condor.model import (
    CampaignState,
    Decision,
    Leg,
    MarketSnapshot,
    OrderLeg,
)
from icici_breeze_backend.app.services.condor.pricing import ChainRow, Quote, Right, build_greeks_model
from icici_breeze_backend.app.services.condor.strikes import cycle_expiry

_logger = logging.getLogger(__name__)

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
UNDERLYING = "NIFTY"
# The chain a check sees spans this share of spot either side: past a 5-delta wing at 45 DTE
# with room to spare.
GRID_SPAN = 0.15
# A 5-minute bar row cap of 1,000 holds about 13 sessions (77 bars each, to 15:40).
BLOCK_SESSIONS = 12
# A price older than this at a check is no price.
OPTION_STALE = datetime.timedelta(minutes=60)
SPOT_STALE = datetime.timedelta(minutes=10)
FIVE_MIN = datetime.timedelta(minutes=5)
MAX_RESOLVE_PASSES = 4
# Smile anchors: every strike on this grid within GRID_SPAN, out of the money on its own side
# (puts at and below spot, calls at and above). A live chain quotes every strike; a replay
# that quoted only the contracts it trades has a smile held flat past its last point, and a
# 5-delta wing then creeps outward one strike per pass, because each quoted wing extends the
# skew it is chosen against. A fixed grid this coarse barely moves as spot drifts, so it costs
# little more to fetch.
SMILE_ANCHOR_STEP = 500
# Contracts whose bars a StoreSource keeps in memory. A comparison run (#71) replays one source
# for every settings combination, so the bars of every contract any of them touched would
# otherwise stay loaded for the whole run; a check needs only a few dozen at once.
BARS_KEPT = 400

ExitAction = Literal["time_roll", "close"]
Status = Literal["ok", "missing", "stale", "unlisted"]
Contract = tuple[datetime.date, float, Right]


# --------------------------------------------------------------------------------------
# Prices
# --------------------------------------------------------------------------------------


class PriceSource(Protocol):
    def spot(self, at: datetime.datetime) -> Optional[float]: ...

    def option(self, contract: Contract, at: datetime.datetime) -> tuple[Status, Optional[float]]: ...

    def needs(self, contract: Contract, at: datetime.datetime, until: datetime.date) -> list[Need]: ...

    def refresh(self) -> None: ...


def _right_api(right: Right) -> str:
    return "call" if right == "Call" else "put"


class Blocks:
    """Fixed 12-session windows from HISTORY_START, so the same contract-window is always the
    same `Need` and a fetched one is recognised as fetched."""

    def __init__(self, holidays: set[datetime.date], last_day: datetime.date) -> None:
        horizon = last_day + datetime.timedelta(days=120)
        self.days = regime.trading_days(regime.HISTORY_START, horizon, holidays)
        self._index = {d: i for i, d in enumerate(self.days)}
        # Never ask for a window past the last completed session: a block fetched with a
        # future end would read as covered later, with the newer days still missing.
        self.last_day = last_day

    def window(self, day: datetime.date, expiry: datetime.date) -> Optional[tuple[datetime.date, datetime.date]]:
        i = self._index.get(day)
        if i is None:
            return None
        b = i // BLOCK_SESSIONS
        first = self.days[b * BLOCK_SESSIONS]
        last = self.days[min(len(self.days) - 1, (b + 1) * BLOCK_SESSIONS - 1)]
        last = min(last, expiry, self.last_day)
        return (first, last) if last >= first else None


class StoreSource:
    """Prices from the backtest cache (`backtest_store`)."""

    def __init__(
        self,
        holidays: set[datetime.date],
        start: datetime.date,
        end: datetime.date,
        *,
        data_until: datetime.date,
        path: Optional[str] = None,
        interval: str = store.INTERVAL_5MIN,
    ) -> None:
        self.path = path
        self.interval = interval
        self.blocks = Blocks(holidays, data_until)
        spot = store.load_candles(
            stock_code=UNDERLYING, table="spot_candles", from_date=start, to_date=end, path=path
        )
        self._spot = {c.ts: c.close for c in spot}
        self._spot_ts = sorted(self._spot)
        self._bars: OrderedDict[Contract, list[store.HistCandle]] = OrderedDict()
        self._fetched: dict[tuple[Contract, datetime.date], bool] = {}

    def refresh(self) -> None:
        self._bars.clear()
        self._fetched = {k: v for k, v in self._fetched.items() if v}

    def spot(self, at: datetime.datetime) -> Optional[float]:
        import bisect

        # The last 1-minute bar to have closed by `at`.
        i = bisect.bisect_right(self._spot_ts, at - datetime.timedelta(minutes=1)) - 1
        if i < 0:
            return None
        ts = self._spot_ts[i]
        return self._spot[ts] if at - ts <= SPOT_STALE and ts.date() == at.date() else None

    def _key(self, contract: Contract) -> OptionKey:
        expiry, strike, right = contract
        return OptionKey(UNDERLYING, expiry, float(strike), _right_api(right))

    def _need(self, contract: Contract, first: datetime.date, last: datetime.date) -> Need:
        return Need(
            self._key(contract),
            self.interval,
            datetime.datetime.combine(first, datetime.time(0, 0)),
            datetime.datetime.combine(last, datetime.time(23, 59, 59)),
        )

    def _is_fetched(self, contract: Contract, day: datetime.date) -> bool:
        window = self.blocks.window(day, contract[0])
        if window is None:
            return False
        cached = self._fetched.get((contract, window[0]))
        if cached is None:
            cached = store.fetched(self._need(contract, *window), path=self.path)
            self._fetched[(contract, window[0])] = cached
        return cached

    def _load(self, contract: Contract) -> list[store.HistCandle]:
        bars = self._bars.get(contract)
        if bars is not None:
            self._bars.move_to_end(contract)
        else:
            expiry = contract[0]
            bars = store.load_option_bars(
                self._key(contract),
                self.interval,
                datetime.datetime.combine(regime.HISTORY_START, datetime.time(0, 0)),
                datetime.datetime.combine(expiry, datetime.time(23, 59, 59)),
                path=self.path,
            )
            self._bars[contract] = bars
            if len(self._bars) > BARS_KEPT:
                self._bars.popitem(last=False)
        return bars

    def option(self, contract: Contract, at: datetime.datetime) -> tuple[Status, Optional[float]]:
        if not self._is_fetched(contract, at.date()):
            return "missing", None
        bars = self._load(contract)
        window = self.blocks.window(at.date(), contract[0])
        if window and not any(window[0] <= b.ts.date() <= window[1] for b in bars):
            return "unlisted", None
        return price_at(bars, at)

    def needs(self, contract: Contract, at: datetime.datetime, until: datetime.date) -> list[Need]:
        """Every unfetched block from `at` to `until` (or expiry): a contract wanted once is
        usually wanted for the rest of its cycle, so the whole span goes in one round."""
        out: list[Need] = []
        seen: set[datetime.date] = set()
        for day in self.blocks.days:
            if day < at.date() or day > min(until, contract[0]):
                continue
            window = self.blocks.window(day, contract[0])
            if window is None or window[0] in seen:
                continue
            seen.add(window[0])
            if not self._is_fetched(contract, day):
                out.append(self._need(contract, *window))
        return out


def price_at(bars: list[store.HistCandle], at: datetime.datetime) -> tuple[Status, Optional[float]]:
    """The traded price at `at`: the open of the 5-minute bar `at` falls in, if it traded, else
    the close of the last bar to finish by `at`, if recent enough."""
    last: Optional[store.HistCandle] = None
    for bar in bars:
        if bar.ts <= at < bar.ts + FIVE_MIN and bar.volume > 0:
            return "ok", bar.open
        if bar.ts + FIVE_MIN <= at:
            last = bar
        elif bar.ts > at:
            break
    if last is not None and at - (last.ts + FIVE_MIN) <= OPTION_STALE and last.ts.date() == at.date():
        return "ok", last.close
    return "stale", None


# --------------------------------------------------------------------------------------
# The replay
# --------------------------------------------------------------------------------------


@dataclass
class _Cycle:
    expiry: datetime.date
    opened: Optional[str] = None
    closed: Optional[str] = None
    close_reason: Optional[str] = None
    tranches: int = 0
    rolls: int = 0
    # (strike, right) -> [signed quantity, average price of what is held]
    legs: dict[tuple[float, Right], list[float]] = field(default_factory=dict)

    def leg_tuple(self) -> tuple[Leg, ...]:
        out = []
        for (strike, right), (qty, avg) in sorted(self.legs.items()):
            if qty:
                out.append(Leg(strike, right, "Buy" if qty > 0 else "Sell", int(abs(qty)), avg))
        return tuple(out)


@dataclass
class _Campaign:
    started: str
    cash: float = 0.0
    charges: float = 0.0
    cycles: list[_Cycle] = field(default_factory=list)
    worst_pnl: float = 0.0
    # Campaign P&L at the latest check it was marked at: what closing then would have left.
    last_pnl: Optional[float] = None
    ended: Optional[str] = None
    end_reason: Optional[str] = None

    @property
    def cycle(self) -> Optional[_Cycle]:
        return self.cycles[-1] if self.cycles and self.cycles[-1].closed is None else None


class CondorReplay:
    def __init__(
        self,
        settings: CondorSettings,
        start: datetime.date,
        end: datetime.date,
        *,
        lots_per_tranche: int,
        exit_action: ExitAction,
        source: PriceSource,
        charges: ChargesModel,
        spread: SpreadStats,
        holidays: set[datetime.date],
        on_day: Optional[Callable[[datetime.date], None]] = None,
        listed_band: Optional[tuple[float, float]] = None,
    ) -> None:
        self.settings = settings
        # (below, above) as fractions of spot: new legs only inside it (#69). None = unlimited.
        self.listed_band = listed_band
        self.start, self.end = start, end
        self.qty = int(lots_per_tranche) * regime.lot_size_for(UNDERLYING, start)
        self.lot_size = regime.lot_size_for(UNDERLYING, start)
        self.exit_action = exit_action
        self.source = source
        self.charges = charges
        self.spread = spread
        self.holidays = holidays
        self.on_day = on_day
        self.checks = self._schedule()
        self.i = 0
        self.campaign: Optional[_Campaign] = None
        self.campaigns: list[_Campaign] = []
        self.events: list[dict[str, Any]] = []
        self.skipped: dict[str, int] = {}
        self.closed_pnl = 0.0
        self.peak = 0.0
        self.max_drawdown = 0.0
        self.unlisted: set[Contract] = set()

    # -- schedule ------------------------------------------------------------------------

    def _schedule(self) -> list[tuple[datetime.date, str, datetime.datetime]]:
        out = []
        for day in regime.trading_days(self.start, self.end, self.holidays):
            for kind, hhmm in (("sod", self.settings.sod_check_ist), ("eod", self.settings.eod_check_ist)):
                h, m = (int(x) for x in hhmm.split(":"))
                out.append((day, kind, datetime.datetime.combine(day, datetime.time(h, m))))
        return out

    def listed_expiries(self, day: datetime.date) -> list[datetime.date]:
        weekdays = regime.EXPIRY_WEEKDAY_MAP[UNDERLYING]
        out: set[datetime.date] = set()
        d = day
        while d <= day + datetime.timedelta(days=100):
            e = regime.next_expiry(d, weekdays, self.holidays)
            out.add(e)
            d = e + datetime.timedelta(days=1)
        return sorted(out)

    # -- running -------------------------------------------------------------------------

    @property
    def done(self) -> bool:
        return self.i >= len(self.checks)

    def run(self) -> list[Need]:
        """Advance until finished (returns []) or until a check needs data (returns the needs;
        call again after fetching them to carry on from that check)."""
        last_day: Optional[datetime.date] = None
        while not self.done:
            day, kind, ts = self.checks[self.i]
            if self.on_day and day != last_day:
                self.on_day(day)
                last_day = day
            needs = self._check(day, kind, ts)
            if needs:
                return needs
            self.i += 1
        return []

    def _skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1

    def _check(self, day: datetime.date, kind: str, ts: datetime.datetime) -> list[Need]:
        spot = self.source.spot(ts)
        if spot is None:
            self._skip("no_spot")
            return []
        camp = self.campaign
        cycle = camp.cycle if camp else None
        if cycle is not None and not cycle.legs and (cycle.expiry - day).days < self.settings.tranche_cutoff_dte:
            # The cycle passed its cut-off without a single tranche going in.
            cycle.closed, cycle.close_reason = ts.isoformat(), "never_entered"
            cycle = None
        if cycle is None:
            expiry = cycle_expiry(self.listed_expiries(day), day, self.settings)
            if expiry is None:
                return []
            if camp is None:
                camp = self.campaign = _Campaign(started=ts.isoformat())
                self.campaigns.append(camp)
            cycle = _Cycle(expiry=expiry)
            camp.cycles.append(cycle)

        state = CampaignState(cycle.expiry, self.lot_size, cycle.leg_tuple(), camp.cash, cycle.tranches)
        resolved = self._resolve(cycle.expiry, ts, spot, state, lambda m: (decide(state, m, self.settings, kind, self.charges), None))
        if isinstance(resolved, list):
            return resolved
        decision, market = resolved
        plan = None
        if decision.action == "exit_or_roll" and self.exit_action == "time_roll":
            # Plan the next cycle before closing this one: if its contracts are not cached the
            # check must stop here, untouched, so that it can be replayed after the fetch.
            plan = self._plan_roll(cycle.expiry, ts, spot, day)
            if isinstance(plan, list):
                return plan
        self._act(decision, market, kind, ts, day, plan)
        self._mark(decision)
        return []

    # -- building a check's chain --------------------------------------------------------

    def _grid(self, expiry: datetime.date, spot: float, held: Iterable[Contract]) -> list[float]:
        step = regime.STRIKE_STEP[UNDERLYING]
        lo = int(spot * (1 - GRID_SPAN) // step * step)
        hi = int(spot * (1 + GRID_SPAN) // step * step)
        strikes = {float(k) for k in range(lo, hi + 1, int(step))} | {c[1] for c in held}
        dead = {c[1] for c in self.unlisted if c[0] == expiry}
        return sorted(strikes - dead)

    def _resolve(self, expiry, ts, spot, state: Optional[CampaignState], chooser):
        """Decide with the chain quoting what the answer needs. Returns (decision, market) or
        the needs that stopped it."""
        held = {(expiry, l.strike, l.right) for l in state.legs} if state else set()
        atm = regime.atm_strike(spot, UNDERLYING)
        quoted: set[Contract] = {(expiry, atm, "Call"), (expiry, atm, "Put")} | held | self._anchors(expiry, spot)
        decision = market = None
        for attempt in range(MAX_RESOLVE_PASSES + 1):
            rows, missing = self._chain(expiry, ts, spot, quoted, held)
            if missing:
                needs: list[Need] = []
                for c in sorted(missing):
                    needs.extend(self.source.needs(c, ts, self.end))
                if needs:
                    return needs
            market = MarketSnapshot(
                now=ts.replace(tzinfo=IST), spot=spot, spot_live=True, feeds_ok=True, chain=rows,
                listed=self._listed(rows, spot, held),
            )
            decision, wanted = chooser(market)
            wanted = set(wanted or ()) | self._contracts_of(expiry, decision)
            new = wanted - quoted
            if not new or attempt == MAX_RESOLVE_PASSES:
                break
            quoted |= new
        return decision, market

    def _listed(self, rows, spot: float, held: set[Contract]) -> Optional[tuple[float, ...]]:
        """The strikes a new leg may use at this check: inside today's tradeable band, plus
        whatever the campaign already holds (it has to be able to close it)."""
        if self.listed_band is None:
            return None
        below, above = self.listed_band
        lo, hi = spot * (1 - below), spot * (1 + above)
        kept = {c[1] for c in held}
        return tuple(r.strike for r in rows if lo <= r.strike <= hi or r.strike in kept)

    def _anchors(self, expiry: datetime.date, spot: float) -> set[Contract]:
        lo, hi = spot * (1 - GRID_SPAN), spot * (1 + GRID_SPAN)
        out: set[Contract] = set()
        k = (int(lo) // SMILE_ANCHOR_STEP + 1) * SMILE_ANCHOR_STEP
        while k <= hi:
            out.add((expiry, float(k), "Put" if k <= spot else "Call"))
            k += SMILE_ANCHOR_STEP
        return {c for c in out if c not in self.unlisted}

    def _contracts_of(self, expiry: datetime.date, decision: Any) -> set[Contract]:
        out: set[Contract] = set()
        if isinstance(decision, Decision):
            for o in decision.orders:
                out.add((expiry, float(o.strike), o.right))
            s = decision.tranche_strikes or {}
            if s:
                out |= {
                    (expiry, s["short_call"], "Call"), (expiry, s["long_call"], "Call"),
                    (expiry, s["short_put"], "Put"), (expiry, s["long_put"], "Put"),
                }
        return out

    def _chain(self, expiry, ts, spot, quoted: set[Contract], held: set[Contract]):
        quotes: dict[tuple[float, Right], Quote] = {}
        missing: set[Contract] = set()
        for c in quoted:
            status, price = self.source.option(c, ts)
            if status == "missing":
                missing.add(c)
            elif status == "unlisted":
                if c not in held:
                    self.unlisted.add(c)
            elif status == "ok" and price and price > 0:
                s = self.spread.spread_for(price)
                quotes[(c[1], c[2])] = Quote(
                    bid=max(0.0, price - s / 2), ask=price + s / 2, ltp=price, bid_qty=1, ask_qty=1
                )
        rows = []
        for k in self._grid(expiry, spot, held):
            rows.append(ChainRow(k, call=quotes.get((k, "Call")), put=quotes.get((k, "Put"))))
        return tuple(rows), missing

    # -- acting --------------------------------------------------------------------------

    def _fill(self, cycle: _Cycle, orders: Iterable[OrderLeg]) -> tuple[float, float]:
        """Apply orders at their decision price plus adverse slippage. Returns (cash, charges)."""
        cash = fees = 0.0
        frac = float(self.charges.slippage_spread_fraction)
        for o in orders:
            base = float(o.price or 0.0)
            slip = frac * self.spread.spread_for(base) if base > 0 else 0.0
            price = base + slip if o.action == "Buy" else max(0.0, base - slip)
            leg_fee = self.charges.leg_charges(price, o.quantity, is_buy=o.action == "Buy")
            cash += (price if o.action == "Sell" else -price) * o.quantity - leg_fee
            fees += leg_fee
            key = (float(o.strike), o.right)
            qty, avg = cycle.legs.get(key, [0.0, 0.0])
            signed = o.quantity if o.action == "Buy" else -o.quantity
            new_qty = qty + signed
            if o.opening:
                avg = (abs(qty) * avg + o.quantity * price) / abs(new_qty) if new_qty else price
            if new_qty:
                cycle.legs[key] = [new_qty, avg]
            else:
                cycle.legs.pop(key, None)
        return cash, fees

    def _event(self, ts, kind, spot, decision: Optional[Decision], action: str, **extra) -> None:
        camp = self.campaign
        row = {
            "at": ts.isoformat(),
            "check": kind,
            "spot": round(spot, 2),
            "action": action,
            "reason": decision.reason if decision else extra.pop("reason", None),
            "text": decision.text if decision else extra.pop("text", ""),
            "expiry": camp.cycle.expiry.isoformat() if camp and camp.cycle else None,
        }
        if decision and decision.metrics:
            m = decision.metrics
            row.update(
                dte=m.dte,
                campaign_pnl=round(m.campaign_pnl_inr, 2) if m.campaign_pnl_inr is not None else None,
                net_delta_per_lot=round(m.net_delta_per_lot, 3) if m.net_delta_per_lot is not None else None,
            )
        row.update(extra)
        self.events.append(row)

    def _orders_json(self, orders: Iterable[OrderLeg]) -> list[dict[str, Any]]:
        return [
            {"action": o.action, "strike": o.strike, "right": o.right, "qty": o.quantity,
             "opening": o.opening, "price": round(o.price, 2) if o.price else None}
            for o in orders
        ]

    def _act(self, d: Decision, market: MarketSnapshot, kind: str, ts: datetime.datetime, day: datetime.date, plan=None) -> None:
        camp = self.campaign
        cycle = camp.cycle
        spot = market.spot
        if d.action == "unavailable":
            self._skip(d.reason)
            self._event(ts, kind, spot, d, "skipped")
            return
        if d.action == "no_action":
            if d.reason in ("roll_credit_below_min", "roll_near_exit", "roll_unpriced", "tranche_unpriced", "roll_no_wing"):
                self._skip(d.reason)
                self._event(ts, kind, spot, d, "skipped", roll_credit=d.roll_credit_points)
            return
        if d.action == "enter_tranche":
            self._enter(cycle, d.tranche_strikes, market, kind, ts, d)
            return
        if d.action == "roll_untested":
            cash, fees = self._fill(cycle, d.orders)
            camp.cash += cash
            camp.charges += fees
            cycle.rolls += 1
            self._event(ts, kind, spot, d, "roll", cash=round(cash, 2), roll_credit=round(d.roll_credit_points, 2),
                        orders=self._orders_json(d.orders))
            return
        if d.action in ("close_all", "exit_or_roll"):
            cash, fees = self._fill(cycle, d.orders)
            camp.cash += cash
            camp.charges += fees
            cycle.closed, cycle.close_reason = ts.isoformat(), d.reason
            self._event(ts, kind, spot, d, "close", cash=round(cash, 2), orders=self._orders_json(d.orders))
            if d.action == "exit_or_roll" and self.exit_action == "time_roll" and plan:
                expiry, strikes, roll_market = plan
                nxt = _Cycle(expiry=expiry)
                camp.cycles.append(nxt)
                if strikes is None:
                    self._skip("tranche_unpriced")
                else:
                    self._enter(nxt, strikes, roll_market, kind, ts, None)
            else:
                self._end_campaign(ts, d.reason if plan is not False else "no_expiry_to_roll_into")

    def _enter(self, cycle: _Cycle, strikes: dict, market: MarketSnapshot, kind, ts, d: Optional[Decision]) -> bool:
        orders = entry_orders(strikes, self.qty, market)
        if orders is None:
            self._skip("tranche_unpriced")
            self._event(ts, kind, market.spot, d, "skipped", reason="tranche_unpriced",
                        text="Tranche due, but a leg has no traded price at the check.")
            return False
        cash, fees = self._fill(cycle, orders)
        self.campaign.cash += cash
        self.campaign.charges += fees
        cycle.tranches += 1
        cycle.opened = cycle.opened or ts.isoformat()
        self._event(ts, kind, market.spot, d, "enter", cash=round(cash, 2), tranche=cycle.tranches,
                    orders=self._orders_json(orders),
                    **({} if d else {"reason": "time_roll", "text": f"Time roll into {cycle.expiry:%d-%b-%Y}."}))
        return True

    def _plan_roll(self, old_expiry: datetime.date, ts, spot: float, day: datetime.date):
        """The next cycle a time roll would open: (expiry, strikes or None, market), False when
        no later expiry qualifies, or the needs that stop the check."""
        later = [e for e in self.listed_expiries(day) if e > old_expiry]
        expiry = cycle_expiry(later, day, self.settings)
        if expiry is None:
            return False

        def choose(m: MarketSnapshot):
            model = build_greeks_model(m.chain, m.spot, expiry, m.now)
            strikes = entry_strikes(model, m.strikes, self.settings) if model else None
            wanted = set()
            if strikes:
                wanted = {(expiry, strikes["short_call"], "Call"), (expiry, strikes["long_call"], "Call"),
                          (expiry, strikes["short_put"], "Put"), (expiry, strikes["long_put"], "Put")}
            return strikes, wanted

        resolved = self._resolve(expiry, ts, spot, None, choose)
        if isinstance(resolved, list):
            return resolved
        strikes, market = resolved
        return expiry, strikes, market

    def _end_campaign(self, ts: datetime.datetime, reason: str) -> None:
        camp = self.campaign
        camp.ended, camp.end_reason = ts.isoformat(), reason
        self.closed_pnl += camp.cash
        self.campaign = None

    def _mark(self, d: Decision) -> None:
        """Track the equity curve at every check: closed campaigns + the open one."""
        open_pnl = 0.0
        camp = self.campaign
        if camp is not None:
            if camp.cycle is None or not camp.cycle.legs:
                open_pnl = camp.cash
            elif d.metrics and d.metrics.campaign_pnl_inr is not None:
                open_pnl = d.metrics.campaign_pnl_inr
            else:
                return
            camp.worst_pnl = min(camp.worst_pnl, open_pnl)
            camp.last_pnl = open_pnl
        equity = self.closed_pnl + open_pnl
        self.peak = max(self.peak, equity)
        self.max_drawdown = max(self.max_drawdown, self.peak - equity)

    # -- results -------------------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        rows = []
        for c in self.campaigns:
            traded = [cy for cy in c.cycles if cy.tranches]
            rows.append({
                "started": c.started,
                "ended": c.ended,
                "end_reason": c.end_reason or ("open" if c is self.campaign else None),
                "pnl": round(c.cash, 2),
                "charges": round(c.charges, 2),
                "worst_pnl_at_check": round(c.worst_pnl, 2),
                "pnl_at_last_check": round(c.last_pnl, 2) if c.last_pnl is not None else None,
                "cycles": [
                    {"expiry": cy.expiry.isoformat(), "opened": cy.opened, "closed": cy.closed,
                     "close_reason": cy.close_reason, "tranches": cy.tranches, "rolls": cy.rolls}
                    for cy in traded
                ],
            })
        finished = [r for r in rows if r["ended"]]
        open_now = self.campaign
        return {
            "complete": self.done,
            "checks": len(self.checks),
            "checks_replayed": self.i,
            "lots_per_tranche": self.qty // self.lot_size if self.lot_size else None,
            "quantity_per_tranche": self.qty,
            "exit_action": self.exit_action,
            "closed_pnl": round(self.closed_pnl, 2),
            "open_campaign_cash": round(open_now.cash, 2) if open_now else None,
            "campaigns": rows,
            "campaigns_finished": len(finished),
            "wins": sum(1 for r in finished if r["pnl"] > 0),
            "losses": sum(1 for r in finished if r["pnl"] <= 0),
            "worst_campaign_pnl": min((r["pnl"] for r in finished), default=None),
            "best_campaign_pnl": max((r["pnl"] for r in finished), default=None),
            "max_drawdown": round(self.max_drawdown, 2),
            "charges": round(sum(c.charges for c in self.campaigns), 2),
            "rolls": sum(cy.rolls for c in self.campaigns for cy in c.cycles),
            "skipped": dict(self.skipped),
            "spread_model": self.spread.describe(),
        }
