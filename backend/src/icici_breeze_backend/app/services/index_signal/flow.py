"""Challenger direction signals built from order FLOW, run in shadow beside W-OBI (#30).

W-OBI reads how much is *resting* in the heavyweights' books, and a resting wall is as often a
seller being absorbed as a buyer arriving. These challengers read how the books and the tape
*change*. They were chosen on 2026-09-13, before any evidence existed for them, one per index, so
that the readiness verdict judges the version picked in advance rather than whichever of several
looks best afterwards:

- **NIFTY -- futures pressure.** The NIFTY futures contract's own best bid/ask and trades, from
  the quote ticks the always-on scalper candle feed already receives (no new subscription). Half
  order-flow imbalance at the top of the book, half aggressor imbalance on the traded quantity.
- **SENSEX -- constituent queue flow.** Order-flow imbalance at the top of each tracked BSE
  constituent's book, from the depth rooms W-OBI already subscribes, weighted by index weight.
  SENSEX futures are too thin to carry a signal of their own.

Order-flow imbalance (Cont, Kukanov & Stoikov, 2014), per update of the best quotes:

    e = q_b,t * 1[P_b,t >= P_b,t-1] - q_b,t-1 * 1[P_b,t <= P_b,t-1]
      - q_a,t * 1[P_a,t <= P_a,t-1] + q_a,t-1 * 1[P_a,t >= P_a,t-1]

A best bid that drops scores minus its whole previous queue: it was eaten or pulled, the most
bearish thing a book can show. An externally reviewed spec scored that case 0, discarding exactly
the events that carry the most information.

Aggressor side comes from the traded-quantity counter, since ICICI sends no trade-by-trade tape: the
quantity traded since the last tick is a buy when the last price is at or above the previous ask
(or above the mid), a sell at or below the previous bid (or below the mid).

Each flow is normalised as a ratio of time-decayed sums -- signed contributions over absolute
ones, both decayed with alpha = exp(-dt/tau) -- so it reads as the net share of recent flow in one
direction, on the same -1..+1 scale the hysteresis uses whatever the stock's size.

**The parameters are constants, not settings, on purpose.** Tuning them against the shadow
report would be choosing the winner after seeing the results. tau is 30s rather than W-OBI's 3s
because single flow events are far noisier than a book level; the thresholds are W-OBI's.
Challengers are logged to the shadow log as `<index>:flow` and published nowhere else: no
consumer reads them until the evidence says one should replace W-OBI.
"""
from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Mapping, Optional, Sequence

from icici_breeze_backend.app.core.timezone import IST
from icici_breeze_backend.app.services.index_signal.engine import (
    REASON_LOW_COVERAGE,
    REASON_MARKET_CLOSED,
    REASON_NO_CONSTITUENTS,
    REASON_WARMING_UP,
    Constituent,
    DirectionalState,
    SignalState,
    next_state,
)

# Pre-registered 2026-09-13. See the module docstring before changing any of these.
TAU_SECONDS = 30.0
ENTER_THRESHOLD = 0.30
EXIT_THRESHOLD = 0.20
STALE_SECONDS = 30.0
WARMUP_SECONDS = 2 * TAU_SECONDS
MIN_COVERAGE = 0.70

CHALLENGER_SUFFIX = ":flow"
KIND_FUTURES = "futures"
KIND_CONSTITUENTS = "constituents"
CHALLENGER_KIND = {"nifty": KIND_FUTURES, "sensex": KIND_CONSTITUENTS}
CHALLENGER_NAME = {"nifty": "NIFTY futures pressure", "sensex": "SENSEX constituent queue flow"}


def challenger_label(label: str) -> str:
    return f"{label}{CHALLENGER_SUFFIX}"


@dataclass(frozen=True)
class Top:
    """Best bid and ask, price and quantity."""

    bid_px: float
    bid_qty: float
    ask_px: float
    ask_qty: float

    @property
    def usable(self) -> bool:
        # An empty side (BSE wipes its depth at the close; auction books go blank) is no quote.
        return self.bid_px > 0 and self.ask_px > 0 and self.bid_qty >= 0 and self.ask_qty >= 0


def ofi_step(prev: Top, cur: Top) -> float:
    """Order-flow imbalance between two consecutive top-of-book readings (module docstring)."""
    e_bid = (cur.bid_qty if cur.bid_px >= prev.bid_px else 0.0) - (
        prev.bid_qty if cur.bid_px <= prev.bid_px else 0.0
    )
    e_ask = (cur.ask_qty if cur.ask_px <= prev.ask_px else 0.0) - (
        prev.ask_qty if cur.ask_px >= prev.ask_px else 0.0
    )
    return e_bid - e_ask


def aggressor_step(prev: Top, last: float, traded: float) -> float:
    """Signed quantity traded since the previous tick: + bought at the offer, - sold at the bid."""
    if traded <= 0 or last <= 0:
        return 0.0
    if last >= prev.ask_px:
        return traded
    if last <= prev.bid_px:
        return -traded
    mid = (prev.bid_px + prev.ask_px) / 2.0
    if last > mid:
        return traded
    if last < mid:
        return -traded
    return 0.0


def _num(raw: Any) -> Optional[float]:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def parse_futures_quote(payload: Mapping[str, Any]) -> tuple[Optional[Top], Optional[float], Optional[float]]:
    """(top of book, last price, cumulative traded quantity) from a breeze F&O quote tick."""
    bp, bq, sp, sq = (_num(payload.get(k)) for k in ("bPrice", "bQty", "sPrice", "sQty"))
    top: Optional[Top] = None
    if bp is not None and bq is not None and sp is not None and sq is not None:
        top = Top(bp, bq, sp, sq)
        if not top.usable:
            top = None
    return top, _num(payload.get("last")), _num(payload.get("ttq"))


class FlowRatio:
    """Signed over absolute time-decayed sums: the net share of recent flow, in [-1, +1]."""

    def __init__(self, tau_seconds: float) -> None:
        self.tau = tau_seconds
        self.num = 0.0
        self.den = 0.0
        self.last_ts: Optional[float] = None

    def add(self, contribution: float, ts: float) -> None:
        if self.last_ts is not None and ts > self.last_ts:
            decay = math.exp(-(ts - self.last_ts) / self.tau)
            self.num *= decay
            self.den *= decay
        self.num += contribution
        self.den += abs(contribution)
        self.last_ts = ts if self.last_ts is None else max(self.last_ts, ts)

    def value(self) -> Optional[float]:
        return self.num / self.den if self.den > 0 else None

    def fresh(self, now: float) -> bool:
        return self.last_ts is not None and now - self.last_ts <= STALE_SECONDS


class _StockFlow:
    __slots__ = ("prev", "flow")

    def __init__(self) -> None:
        self.prev: Optional[Top] = None
        self.flow = FlowRatio(TAU_SECONDS)


def _round(value: Optional[float], places: int = 4) -> Optional[float]:
    return None if value is None else round(value, places)


class FlowEngine:
    """One index's challenger. Same state contract as `IndexSignalEngine`: `unavailable` means no
    reading and is never `neutral`. Ticks arrive on the SDK socket thread, snapshots on the
    publisher's, so both take the lock and neither does I/O."""

    def __init__(self, label: str, kind: str) -> None:
        if kind not in (KIND_FUTURES, KIND_CONSTITUENTS):
            raise ValueError(f"unknown challenger kind {kind!r}")
        self.label = label
        self.kind = kind
        self._lock = threading.Lock()
        self._constituents: tuple[Constituent, ...] = ()
        self._tracked: frozenset[str] = frozenset()
        self._stocks: dict[str, _StockFlow] = {}
        self._fut_prev: Optional[Top] = None
        self._fut_ttq: Optional[float] = None
        self._fut_day: Optional[date] = None
        self._fut_ofi = FlowRatio(TAU_SECONDS)
        self._fut_aggr = FlowRatio(TAU_SECONDS)
        self._available_since: Optional[float] = None
        self._directional: DirectionalState = "neutral"

    def set_constituents(self, constituents: Sequence[Constituent]) -> bool:
        """Replace the basket (constituent kind only). A change of names restarts the reading."""
        new = tuple(constituents)
        names = frozenset(c.short_name for c in new)
        with self._lock:
            changed = names != self._tracked
            self._constituents = new
            self._tracked = names
            if changed:
                self._stocks = {k: v for k, v in self._stocks.items() if k in names}
                self._available_since = None
                self._directional = "neutral"
        return changed

    def on_top(self, short_name: str, top: Top, ts: float) -> None:
        with self._lock:
            if short_name not in self._tracked or not top.usable:
                return
            stock = self._stocks.setdefault(short_name, _StockFlow())
            if stock.prev is not None:
                stock.flow.add(ofi_step(stock.prev, top), ts)
            stock.prev = top

    def on_futures_quote(
        self, top: Optional[Top], last: Optional[float], ttq: Optional[float], ts: float
    ) -> None:
        if top is None:
            return
        day = datetime.fromtimestamp(ts, IST).date()
        with self._lock:
            if day != self._fut_day:
                # The traded-quantity counter restarts with the session; yesterday's baseline
                # would read as one enormous trade.
                self._fut_day, self._fut_ttq, self._fut_prev = day, None, None
            prev = self._fut_prev
            if prev is not None:
                self._fut_ofi.add(ofi_step(prev, top), ts)
                if last is not None and ttq is not None and self._fut_ttq is not None:
                    traded = ttq - self._fut_ttq
                    if traded >= 0:  # a counter running backwards is a stale packet
                        self._fut_aggr.add(aggressor_step(prev, last, traded), ts)
            self._fut_prev = top
            if ttq is not None and (self._fut_ttq is None or ttq >= self._fut_ttq):
                self._fut_ttq = ttq

    def _reading_locked(self, now: float) -> tuple[Optional[float], float, dict[str, Any]]:
        """(signal, coverage, components). Signal is None when there is no usable reading."""
        if self.kind == KIND_FUTURES:
            ofi = self._fut_ofi.value() if self._fut_ofi.fresh(now) else None
            aggr = self._fut_aggr.value() if self._fut_aggr.fresh(now) else None
            parts = {"order_flow": _round(ofi), "aggressor": _round(aggr)}
            if ofi is None or aggr is None:
                return None, 0.0, parts
            return 0.5 * (ofi + aggr), 1.0, parts

        total = live = weighted = 0.0
        parts: dict[str, Any] = {}
        for c in self._constituents:
            if c.weight <= 0:
                continue
            total += c.weight
            stock = self._stocks.get(c.short_name)
            value = stock.flow.value() if stock is not None and stock.flow.fresh(now) else None
            parts[c.short_name] = _round(value)
            if value is not None:
                live += c.weight
                weighted += c.weight * value
        coverage = live / total if total > 0 else 0.0
        return (weighted / live if live > 0 else None), coverage, parts

    def snapshot(self, now: float, *, session_open: bool) -> dict[str, Any]:
        """The shadow-log view at `now`. Advances the hysteresis, so once per publish."""
        with self._lock:
            value, coverage, parts = self._reading_locked(now)
            reason: Optional[str] = None
            if self.kind == KIND_CONSTITUENTS and not self._constituents:
                reason = REASON_NO_CONSTITUENTS
            elif not session_open:
                reason = REASON_MARKET_CLOSED
            elif value is None or coverage < MIN_COVERAGE:
                reason = REASON_LOW_COVERAGE

            state: SignalState
            if reason is not None:
                self._available_since = None
                self._directional = "neutral"
                state = "unavailable"
            else:
                if self._available_since is None:
                    self._available_since = now
                if now - self._available_since < WARMUP_SECONDS:
                    reason = REASON_WARMING_UP
                    state = "unavailable"
                else:
                    assert value is not None
                    self._directional = next_state(
                        self._directional,
                        value,
                        enter_threshold=ENTER_THRESHOLD,
                        exit_threshold=EXIT_THRESHOLD,
                    )
                    state = self._directional
        return {
            "label": challenger_label(self.label),
            "index": self.label,
            "kind": self.kind,
            "state": state,
            "reason": reason,
            "signal": _round(value),
            "raw_wobi": None,
            "coverage": round(coverage, 4),
            "thresholds": {"enter": ENTER_THRESHOLD, "exit": EXIT_THRESHOLD},
            "tau_seconds": TAU_SECONDS,
            "components": parts,
            "computed_at": now,
        }
