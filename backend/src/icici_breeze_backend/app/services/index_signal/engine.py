"""Weighted order-book imbalance (W-OBI) direction signal: the pure part.

    OBI_i   = (sum bid qty - sum ask qty) / (sum bid qty + sum ask qty)   over the top 5 levels
    W-OBI   = sum(w_i * OBI_i) / sum(w_i)                                over constituents with a live book
    signal  = time-aware EWMA of W-OBI, alpha = 1 - exp(-dt / tau)
    state   = hysteresis on the signal: take a side past +/-enter, fall back to neutral inside +/-exit

No I/O and no clock reads -- every call takes its timestamp, so irregular tick spacing and
staleness are testable exactly. `depth_feed` pushes books in; `publisher` asks for snapshots.

Why the EWMA is time-aware rather than per-sample
-------------------------------------------------
The smoothing window is a time (3s), while the signal is *published* at the user's P&L recompute
interval (1-30s). A fixed-alpha EWMA stepped at that interval would smooth over a different horizon
for every setting -- at 2s alpha is ~0.49, at 30s it is a single snapshot. Updating on every depth
tick with alpha derived from the actual gap keeps tau a real time constant whatever the cadence, and
lets a long gap (overnight, a feed outage) wash the old value out on its own.

Why "unavailable" is not "neutral"
----------------------------------
Neutral is a reading: the heavyweights' books are balanced. Unavailable means there is no reading --
the market is shut, too little index weight has a live book, or the smoother has not run long
enough. A consumer deciding whether to trade must be able to tell those apart, so the state machine
never reports neutral for want of data (docs/design-decisions.md #30).
"""
from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from typing import Any, Literal, Mapping, Sequence

SignalState = Literal["bullish", "bearish", "neutral", "unavailable"]
DirectionalState = Literal["bullish", "bearish", "neutral"]

REASON_NO_CONSTITUENTS = "no_constituents"
REASON_MARKET_CLOSED = "market_closed"
REASON_LOW_COVERAGE = "low_coverage"
REASON_WARMING_UP = "warming_up"


@dataclass(frozen=True)
class SignalParams:
    tau_seconds: float = 3.0
    enter_threshold: float = 0.30
    exit_threshold: float = 0.20
    # Share of tracked index weight that must have a live book before the W-OBI is read at all.
    min_coverage: float = 0.70
    book_stale_seconds: float = 30.0
    # How long the smoother must have been fed at adequate coverage before the state may leave
    # "unavailable". The first reading is a raw W-OBI with no smoothing behind it -- exactly the
    # spoofable number the EWMA exists to filter.
    warmup_seconds: float = 6.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.exit_threshold <= self.enter_threshold <= 1.0:
            raise ValueError(
                "index signal thresholds need 0 <= exit <= enter <= 1 "
                f"(got exit={self.exit_threshold}, enter={self.enter_threshold})"
            )


@dataclass(frozen=True)
class Constituent:
    symbol: str  # exchange symbol, as the weights source names it (HDFCBANK)
    short_name: str  # ICICI ShortName -- the depth feed's identity (HDFBAN)
    weight: float  # index weight in percent; only its size relative to the other tracked names matters


@dataclass(frozen=True)
class Book:
    bid_qty: float
    ask_qty: float
    ts: float


def order_book_imbalance(bid_qty: float, ask_qty: float) -> float | None:
    """(bid - ask) / (bid + ask) in [-1, +1], or None for an empty book.

    An empty book is no evidence either way (BSE wipes its depth at the close), so it is
    excluded from the weighted sum rather than scored as a balanced 0."""
    total = bid_qty + ask_qty
    if not total > 0:
        return None
    return (bid_qty - ask_qty) / total


def weighted_obi(
    constituents: Sequence[Constituent],
    books: Mapping[str, Book],
    now: float,
    stale_seconds: float,
) -> tuple[float | None, float, list[dict[str, Any]]]:
    """(W-OBI, coverage, per-constituent rows).

    W-OBI is renormalised over the constituents that currently have a usable book, and coverage
    is the share of tracked weight those represent. Renormalising keeps the number on the same
    -1..+1 scale when a name drops out; coverage is what stops that from quietly turning a
    10-stock signal into a 2-stock one -- callers refuse to read it below a floor.
    """
    total_weight = 0.0
    live_weight = 0.0
    weighted_sum = 0.0
    rows: list[dict[str, Any]] = []
    for c in constituents:
        if c.weight <= 0:
            continue
        total_weight += c.weight
        book = books.get(c.short_name)
        age: float | None = None
        obi: float | None = None
        if book is not None:
            age = max(0.0, now - book.ts)
            if age <= stale_seconds:
                obi = order_book_imbalance(book.bid_qty, book.ask_qty)
        if obi is not None:
            live_weight += c.weight
            weighted_sum += c.weight * obi
        rows.append(
            {
                "symbol": c.symbol,
                "short_name": c.short_name,
                "weight": c.weight,
                "obi": obi,
                "bid_qty": book.bid_qty if book is not None else None,
                "ask_qty": book.ask_qty if book is not None else None,
                "age_seconds": age,
            }
        )
    coverage = live_weight / total_weight if total_weight > 0 else 0.0
    wobi = weighted_sum / live_weight if live_weight > 0 else None
    return wobi, coverage, rows


def ewma_alpha(dt_seconds: float, tau_seconds: float) -> float:
    """Weight of a new reading arriving `dt_seconds` after the last one, for time constant tau."""
    if tau_seconds <= 0:
        return 1.0
    if dt_seconds <= 0:
        return 0.0
    return 1.0 - math.exp(-dt_seconds / tau_seconds)


def next_state(
    prev: DirectionalState,
    value: float,
    *,
    enter_threshold: float,
    exit_threshold: float,
) -> DirectionalState:
    """Hysteresis. From neutral a reading must pass +/-enter to take a side; once on a side it
    holds until the reading falls back inside +/-exit (or crosses straight past the other side's
    enter). Without the gap, a reading sitting at 0.30 flips state on every publish."""
    if value > enter_threshold:
        return "bullish"
    if value < -enter_threshold:
        return "bearish"
    if prev == "bullish" and value > exit_threshold:
        return "bullish"
    if prev == "bearish" and value < -exit_threshold:
        return "bearish"
    return "neutral"


def _round(value: float | None, places: int = 4) -> float | None:
    return None if value is None else round(value, places)


class IndexSignalEngine:
    """One index's live state.

    `on_book` runs on the SDK socket thread and `snapshot` on the publisher's, so both take the
    lock; neither does I/O, so the socket thread is never held up by anything slower than a few
    multiplications.
    """

    def __init__(self, label: str, exchange: str, params: SignalParams) -> None:
        self.label = label
        self.exchange = exchange
        self._lock = threading.Lock()
        self._params = params
        self._constituents: tuple[Constituent, ...] = ()
        self._tracked: frozenset[str] = frozenset()
        self._books: dict[str, Book] = {}
        self._ewma: float | None = None
        self._ewma_ts: float | None = None
        self._available_since: float | None = None
        self._directional: DirectionalState = "neutral"

    @property
    def params(self) -> SignalParams:
        with self._lock:
            return self._params

    def set_params(self, params: SignalParams) -> None:
        """Swap tuning in place. A new tau restarts the smoother (and its warm-up): the value it
        holds was smoothed on a different time scale. Thresholds, coverage and staleness apply
        from the next snapshot without disturbing it."""
        with self._lock:
            if params == self._params:
                return
            if params.tau_seconds != self._params.tau_seconds:
                self._reset_smoother()
            self._params = params

    def tracked_short_names(self) -> frozenset[str]:
        with self._lock:
            return self._tracked

    def set_constituents(self, constituents: Sequence[Constituent]) -> bool:
        """Replace the basket. Returns True when the tracked names changed.

        A weight-only change keeps the smoother running; a change of names resets it, because
        the smoothed value describes a basket that no longer exists."""
        new = tuple(constituents)
        names = frozenset(c.short_name for c in new)
        with self._lock:
            changed = names != self._tracked
            self._constituents = new
            self._tracked = names
            if changed:
                self._books = {k: v for k, v in self._books.items() if k in names}
                self._reset_smoother()
        return changed

    def _reset_smoother(self) -> None:
        self._ewma = None
        self._ewma_ts = None
        self._available_since = None
        self._directional = "neutral"

    def on_book(self, short_name: str, bid_qty: float, ask_qty: float, ts: float) -> None:
        with self._lock:
            if short_name not in self._tracked:
                return
            self._books[short_name] = Book(float(bid_qty), float(ask_qty), float(ts))
            p = self._params
            wobi, coverage, _rows = weighted_obi(
                self._constituents, self._books, ts, p.book_stale_seconds
            )
            if wobi is None or coverage < p.min_coverage:
                # A partial picture is not folded into the smoother. The next adequate reading
                # after a gap is weighted by the gap itself (alpha -> 1), so no reset is needed.
                return
            if self._ewma is None or self._ewma_ts is None:
                self._ewma = wobi
            else:
                alpha = ewma_alpha(ts - self._ewma_ts, p.tau_seconds)
                self._ewma = alpha * wobi + (1.0 - alpha) * self._ewma
            self._ewma_ts = ts

    def snapshot(self, now: float, *, session_open: bool) -> dict[str, Any]:
        """The published view at `now`. Also advances the hysteresis state, so it is meant to be
        called once per publish, not on every read -- readers go through `reader`."""
        with self._lock:
            p = self._params
            wobi, coverage, rows = weighted_obi(
                self._constituents, self._books, now, p.book_stale_seconds
            )
            reason: str | None = None
            if not self._constituents:
                reason = REASON_NO_CONSTITUENTS
            elif not session_open:
                reason = REASON_MARKET_CLOSED
            elif wobi is None or coverage < p.min_coverage:
                reason = REASON_LOW_COVERAGE
            elif self._ewma is None:
                reason = REASON_WARMING_UP

            state: SignalState
            if reason is not None:
                self._available_since = None
                self._directional = "neutral"
                state = "unavailable"
            else:
                if self._available_since is None:
                    self._available_since = now
                if now - self._available_since < p.warmup_seconds:
                    reason = REASON_WARMING_UP
                    state = "unavailable"
                else:
                    assert self._ewma is not None
                    self._directional = next_state(
                        self._directional,
                        self._ewma,
                        enter_threshold=p.enter_threshold,
                        exit_threshold=p.exit_threshold,
                    )
                    state = self._directional
            smoothed = self._ewma

        return {
            "label": self.label,
            "exchange": self.exchange,
            "state": state,
            "reason": reason,
            "signal": _round(smoothed),
            "raw_wobi": _round(wobi),
            "coverage": round(coverage, 4),
            "thresholds": {"enter": p.enter_threshold, "exit": p.exit_threshold},
            "tau_seconds": p.tau_seconds,
            "constituents": [
                {**row, "obi": _round(row["obi"]), "age_seconds": _round(row["age_seconds"], 2)}
                for row in rows
            ],
            "computed_at": now,
        }
