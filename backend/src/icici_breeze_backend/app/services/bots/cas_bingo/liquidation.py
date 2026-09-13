"""Freeing margin by buying back profitable shorts (docs/bots-cas-bingo-plan.md section 9).

When the free margin cannot fund the chosen structure, the bot may buy back shorts on the SAME
index and the SAME (today's) expiry -- Bot 2's, hand-placed, anything -- but only ones that have
already captured `min_captured_pct` of their premium, and only as many lots as the shortfall
needs.

**No `margin_calculator` call is spent here.** The user asked to minimise them, and every one is
a broker call a square-off might need. The release is estimated locally instead:

    release(L) = SPAN(book) - SPAN(book without L)     # the in-app SPAN engine
               + ELM those shorts carried               # 2% of notional, 3% deep OTM
               - cost of buying L back                  # premium leaves the account

The SPAN engine is bit-identical to ICICI on SPAN (margin harness, 2026-09-08/09), but ICICI
charges up to ~22% more than it on short *calls*. That gap is what `safety_buffer_pct` and the
caller's verify-by-limits loop absorb -- the limits call (`get_margin_situation`) is the
broker's own answer, and costs nothing like a margin quote.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.services.bots.cas_bingo import market
from icici_breeze_backend.app.services.bots.scalping.momentum_bot import Quote

_logger = logging.getLogger(__name__)

_TICK = 0.05
# Guard on the local walk. Each step is one in-process SPAN evaluation, no broker call, but a
# book of hundreds of lots should still not become thousands of evaluations on the loop thread.
_MAX_STEPS = 200

SpanFn = Callable[[list[dict[str, Any]]], Optional[float]]


def _norm_right(raw: Any) -> str:
    r = str(raw or "").strip().lower()
    return "call" if r in ("call", "ce", "c") else "put"


@dataclass
class BookLeg:
    """One same-expiry position, as the SPAN engine wants it. Quantity is always positive;
    `action` carries the side."""

    right: str  # "call" | "put"
    strike: float
    quantity: int
    action: str  # cfg.BUY | cfg.SELL
    average_price: float = 0.0

    def as_span_leg(self) -> dict[str, Any]:
        # The SPAN engine reads the broker's spellings ("Call"/"Put", "Buy"/"Sell"); a
        # lower-case "call" would be read as a put.
        return {
            "strike_price": self.strike,
            "quantity": self.quantity,
            "right": cfg.CALL if self.right == "call" else cfg.PUT,
            "action": self.action,
        }


@dataclass(frozen=True)
class ShortPosition:
    right: str
    strike: float
    quantity: int
    average_price: float
    ask: Optional[float]

    @property
    def captured_pct(self) -> Optional[float]:
        if self.ask is None or self.average_price <= 0:
            return None
        return (self.average_price - float(self.ask)) / self.average_price * 100.0

    def cap_price(self, min_captured_pct: float) -> float:
        """The most a buy-back may pay: the price at which exactly `min_captured_pct` of the
        premium has been kept. Rounded DOWN to the tick, so the cap is never exceeded."""
        raw = self.average_price * (1 - min_captured_pct / 100.0)
        return max(_TICK, math.floor(round(raw / _TICK, 6)) * _TICK)


@dataclass(frozen=True)
class BuyBack:
    right: str
    strike: float
    quantity: int
    average_price: float
    ask: float
    cap_price: float
    captured_pct: float
    est_release: float
    cost: float

    def summary(self) -> dict[str, Any]:
        return {
            "right": self.right,
            "strike_price": self.strike,
            "quantity": self.quantity,
            "average_price": self.average_price,
            "ask": self.ask,
            "cap_price": round(self.cap_price, 2),
            "captured_pct": round(self.captured_pct, 1),
            "est_release": round(self.est_release, 2),
            "cost": round(self.cost, 2),
        }


@dataclass
class LiquidationPlan:
    shortfall: float
    target: float
    buybacks: list[BuyBack] = field(default_factory=list)
    est_release: float = 0.0
    covered: bool = False
    ineligible: list[dict[str, Any]] = field(default_factory=list)
    note: str = ""

    def summary(self) -> dict[str, Any]:
        return {
            "shortfall": round(self.shortfall, 2),
            "target": round(self.target, 2),
            "est_release": round(self.est_release, 2),
            "covered": self.covered,
            "buybacks": [b.summary() for b in self.buybacks],
            "ineligible": self.ineligible,
            "note": self.note,
        }


def book_from_positions(rows: list[dict[str, Any]], expiry_display: str) -> list[BookLeg]:
    """Same-expiry legs from `get_positions` rows. Other expiries are left out: the local
    engine prices one underlying and one expiry (design-decisions #23), and buying back a
    same-expiry short cannot change another expiry's margin in that model."""
    out: list[BookLeg] = []
    for row in rows:
        if str(row.get("expiry_date") or "").strip().lower() != expiry_display.strip().lower():
            continue
        try:
            qty = abs(int(float(row.get("quantity") or 0)))
            strike = float(row.get("strike_price") or 0)
        except (TypeError, ValueError):
            continue
        if qty <= 0 or strike <= 0:
            continue
        action = cfg.SELL if str(row.get("action") or "").strip().lower() == "sell" else cfg.BUY
        try:
            avg = float(row.get("average_price") or 0)
        except (TypeError, ValueError):
            avg = 0.0
        out.append(BookLeg(_norm_right(row.get("right")), strike, qty, action, avg))
    return out


def shorts_in(book: list[BookLeg], quotes: dict[tuple[str, float], Quote]) -> list[ShortPosition]:
    return [
        ShortPosition(
            right=leg.right,
            strike=leg.strike,
            quantity=leg.quantity,
            average_price=leg.average_price,
            ask=(quotes.get((leg.right, leg.strike)) or Quote(None, None, None)).ask,
        )
        for leg in book
        if leg.action == cfg.SELL
    ]


def _elm_rate(right: str, strike: float, spot: Optional[float]) -> float:
    if not spot or spot <= 0:
        return cfg.ELM_INDEX_STD
    otm = (strike - spot) / spot if right == "call" else (spot - strike) / spot
    return cfg.ELM_INDEX_DEEP_OTM if otm > cfg.ELM_INDEX_DEEP_OTM_THRESHOLD else cfg.ELM_INDEX_STD


def plan_liquidation(
    book: list[BookLeg],
    shorts: list[ShortPosition],
    *,
    shortfall: float,
    min_captured_pct: float,
    safety_buffer_pct: float,
    lot_size: int,
    spot: Optional[float],
    span_fn: SpanFn,
) -> LiquidationPlan:
    """Walk eligible shorts, most premium captured first, one lot at a time, until the local
    estimate covers the shortfall plus the buffer. Pure apart from `span_fn`."""
    target = max(0.0, float(shortfall)) * (1 + safety_buffer_pct / 100.0)
    plan = LiquidationPlan(shortfall=float(shortfall), target=target)
    if target <= 0:
        plan.covered = True
        return plan
    if lot_size <= 0:
        plan.note = "Lot size unknown; nothing can be sized for buy-back."
        return plan

    eligible: list[ShortPosition] = []
    for s in shorts:
        cap = s.cap_price(min_captured_pct)
        why = None
        if s.ask is None or s.ask <= 0:
            why = "no live ask"
        elif s.ask > cap:
            why = (
                f"ask {s.ask:.2f} is above the {cap:.2f} cap "
                f"({s.captured_pct or 0:.0f}% captured, {min_captured_pct:.0f}% required)"
            )
        elif s.quantity < lot_size:
            why = "less than one lot"
        if why:
            plan.ineligible.append(
                {"right": s.right, "strike_price": s.strike, "quantity": s.quantity, "reason": why}
            )
        else:
            eligible.append(s)
    eligible.sort(key=lambda s: s.captured_pct or 0.0, reverse=True)
    if not eligible:
        plan.note = "No short on this expiry has captured enough of its premium to buy back."
        return plan

    working = [BookLeg(l.right, l.strike, l.quantity, l.action, l.average_price) for l in book]
    current = span_fn([l.as_span_leg() for l in working if l.quantity > 0])
    if current is None:
        plan.note = "The local margin engine could not price this expiry's book."
        return plan

    released = 0.0
    steps = 0
    for pos in eligible:
        leg = next(
            (l for l in working if l.action == cfg.SELL and l.right == pos.right and l.strike == pos.strike and l.quantity > 0),
            None,
        )
        if leg is None:
            continue
        taken = 0
        release_here = 0.0
        while leg.quantity >= lot_size and released < target and steps < _MAX_STEPS:
            steps += 1
            leg.quantity -= lot_size
            after = span_fn([l.as_span_leg() for l in working if l.quantity > 0])
            if after is None:
                leg.quantity += lot_size
                plan.note = "The local margin engine failed part-way; the plan stops there."
                break
            step = (
                (current - after)
                + (spot or 0.0) * lot_size * _elm_rate(pos.right, pos.strike, spot)
                - float(pos.ask) * lot_size
            )
            if step <= 0:
                # Buying this lot back frees nothing net of its cost: stop on this position.
                leg.quantity += lot_size
                break
            current = after
            taken += 1
            release_here += step
            released += step
        if taken:
            units = taken * lot_size
            plan.buybacks.append(
                BuyBack(
                    right=pos.right,
                    strike=pos.strike,
                    quantity=units,
                    average_price=pos.average_price,
                    ask=float(pos.ask),
                    cap_price=pos.cap_price(min_captured_pct),
                    captured_pct=float(pos.captured_pct or 0.0),
                    est_release=round(release_here, 2),
                    cost=round(float(pos.ask) * units, 2),
                )
            )
        if released >= target:
            break

    plan.est_release = round(released, 2)
    plan.covered = released >= target
    if not plan.covered and not plan.note:
        plan.note = (
            f"Buying back every eligible short would free about {released:,.0f}, short of the "
            f"{target:,.0f} needed (shortfall plus the {safety_buffer_pct:.0f}% buffer)."
        )
    return plan


def local_span_fn(index_code: str, expiry_display: str, spot: Optional[float]) -> SpanFn:
    """The in-app SPAN engine for one index's expiry, as `plan_liquidation` wants it."""
    from icici_breeze_backend.app.services.reference_data.span_portfolio_scan import (
        resolve_portfolio_span_margin,
    )

    exchange = market.INDEX_EXCHANGE[index_code]

    def span(legs: list[dict[str, Any]]) -> Optional[float]:
        if not legs:
            return 0.0
        try:
            out = resolve_portfolio_span_margin(exchange, index_code, expiry_display, legs, spot=spot)
        except Exception:  # noqa: BLE001
            _logger.warning("cas bingo: local SPAN failed for %s %s", index_code, expiry_display, exc_info=True)
            return None
        if not out.get("found"):
            return None
        try:
            return float(out.get("span_margin_required") or 0.0)
        except (TypeError, ValueError):
            return None

    return span


def fetch_book(
    proc: Any, user_id: str, index_code: str, expiry_display: str
) -> tuple[Optional[list[BookLeg]], dict[tuple[str, float], Quote], Optional[str]]:
    """(same-expiry book, live quotes for its shorts, error). One positions call; quotes are
    cache-first from the WS feed."""
    from icici_breeze_backend.app.services.portfolio_margin_netting import positions_for_underlying

    positions = positions_for_underlying(proc, user_id, index_code, market.INDEX_EXCHANGE[index_code])
    if not positions.available:
        return None, {}, positions.error or "Positions could not be read."
    book = book_from_positions(positions.rows, expiry_display)
    quotes: dict[tuple[str, float], Quote] = {}
    for leg in book:
        if leg.action == cfg.SELL:
            quotes[(leg.right, leg.strike)] = market.live_quote(
                proc, user_id, index_code, expiry_display, leg.strike, leg.right
            )
    return book, quotes, None


def plan_for(
    proc: Any,
    user_id: str,
    *,
    index_code: str,
    expiry_display: str,
    shortfall: float,
    min_captured_pct: float,
    safety_buffer_pct: float,
    lot_size: int,
    spot: Optional[float],
) -> LiquidationPlan:
    book, quotes, error = fetch_book(proc, user_id, index_code, expiry_display)
    if book is None:
        return LiquidationPlan(shortfall=shortfall, target=shortfall, note=error or "")
    return plan_liquidation(
        book,
        shorts_in(book, quotes),
        shortfall=shortfall,
        min_captured_pct=min_captured_pct,
        safety_buffer_pct=safety_buffer_pct,
        lot_size=lot_size,
        spot=spot,
        span_fn=local_span_fn(index_code, expiry_display, spot),
    )
