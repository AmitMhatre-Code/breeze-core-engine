"""CAS Bingo's structures: strikes and sizing (docs/bots-cas-bingo-plan.md section 4).

Five structures, one builder. Which side a spread goes on is decided by the trigger (or, in
the manual sheet, by the user picking a row); this module only turns a structure name into
priced, sized legs -- or into a reason it could not.

Two rules that are easy to break quietly:

* **Credit strikes come from the day's open, debit and strangle strikes from spot.** The
  asymmetry is the user's, and deliberate: spot swings 2-3% inside CAS, and a credit spread is
  a bet on a return toward the open. So the sold leg of a credit spread can be in the money
  versus spot at entry, which is recorded as a note on the plan, never refused.
* **Buys go first, always.** `entry_sequence` is an explicit ordering, not a comprehension over
  `legs`, for the reason Bot 4 gives: sell first and the broker sees a naked short and rejects
  it on margin before the hedge exists.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Optional

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.strike import parse_strike
from icici_breeze_backend.app.domain.bots import CasBingoConfig, ReasonCode
from icici_breeze_backend.app.services.bots.cas_bingo import market
from icici_breeze_backend.app.services.bots.scalping.margin import margin_for_mixed_legs
from icici_breeze_backend.app.services.bots.scalping.momentum_bot import Quote, _row_quote

_logger = logging.getLogger(__name__)

# (family, side). `side` is the right a spread trades; the strangle trades both.
STRUCTURES: dict[str, tuple[str, str]] = {
    "bear_call_credit": ("credit", "call"),
    "bull_put_credit": ("credit", "put"),
    "bull_call_debit": ("debit", "call"),
    "bear_put_debit": ("debit", "put"),
    "long_strangle": ("strangle", "both"),
}

STRUCTURE_LABEL = {
    "bear_call_credit": "Bear call credit spread",
    "bull_put_credit": "Bull put credit spread",
    "bull_call_debit": "Bull call debit spread",
    "bear_put_debit": "Bear put debit spread",
    "long_strangle": "Long strangle",
}

# Manual sheet order: both credit spreads, both debit spreads, then the strangle.
MANUAL_ORDER = (
    "bull_put_credit",
    "bear_call_credit",
    "bull_call_debit",
    "bear_put_debit",
    "long_strangle",
)

# Sizing walks down from an estimate rather than probing every lot count (iron fly's rule),
# capped lower than Bot 4's three: the manual sheet prices two credit spreads per index, and
# every call here is one the ICICI per-minute budget cannot spend on a square-off.
_MAX_SIZING_CALLS = 2


def structure_for(strategy: str, right: str) -> str:
    """The structure a trigger implies. Credit: the right is the side being SOLD, so a call
    means a bear call spread. Debit: the right is the direction being bought."""
    if strategy == "credit_spread":
        return "bear_call_credit" if right == "call" else "bull_put_credit"
    if strategy == "debit_spread":
        return "bull_call_debit" if right == "call" else "bear_put_debit"
    return "long_strangle"


@dataclass(frozen=True)
class PlanLeg:
    right: str  # "call" | "put"
    strike: float
    action: str  # cfg.BUY | cfg.SELL
    quote: Quote

    @property
    def is_short(self) -> bool:
        return self.action == cfg.SELL


@dataclass(frozen=True)
class Plan:
    index_code: str
    expiry_display: str
    structure: str
    legs: tuple[PlanLeg, ...]
    lot_size: int
    lots: int
    reference: float  # the level the distances were measured from
    reference_kind: str  # "open" | "spot"
    spot: Optional[float]
    # Per unit, at the touch: credit received is positive, debit paid negative.
    net_premium_per_unit: float
    # Credit: the broker's margin for both legs. Debit/strangle: the premium to be paid.
    margin_required: float
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def family(self) -> str:
        return STRUCTURES[self.structure][0]

    @property
    def is_credit(self) -> bool:
        return self.family == "credit"

    @property
    def quantity(self) -> int:
        return self.lot_size * self.lots

    @property
    def exchange_code(self) -> str:
        return market.INDEX_EXCHANGE[self.index_code]

    def entry_sequence(self) -> list[PlanLeg]:
        """Buys first (CE before PE for the strangle), then sells. Not cosmetic -- see above."""
        buys = sorted((l for l in self.legs if not l.is_short), key=lambda l: l.right != "call")
        return buys + [l for l in self.legs if l.is_short]

    def as_legs(self) -> list[dict[str, Any]]:
        return [
            {
                "stock_code": self.index_code,
                "exchange_code": self.exchange_code,
                "right": leg.right,
                "strike_price": leg.strike,
                "expiry_display": self.expiry_display,
                "action": leg.action,
                "lots": self.lots,
                "lot_size": self.lot_size,
                "quantity": self.quantity,
            }
            for leg in self.legs
        ]

    def summary(self) -> dict[str, Any]:
        """The wire shape for the manual sheet and the cycle's detail blob."""
        premium = round(self.net_premium_per_unit * self.quantity, 2)
        return {
            "index_code": self.index_code,
            "index_label": market.INDEX_LABEL[self.index_code],
            "expiry_display": self.expiry_display,
            "structure": self.structure,
            "label": STRUCTURE_LABEL[self.structure],
            "family": self.family,
            "legs": [
                {
                    "right": l.right,
                    "strike_price": l.strike,
                    "action": l.action,
                    "bid": l.quote.bid,
                    "ask": l.quote.ask,
                }
                for l in self.entry_sequence()
            ],
            "lots": self.lots,
            "lot_size": self.lot_size,
            "quantity": self.quantity,
            "reference": self.reference,
            "reference_kind": self.reference_kind,
            "spot": self.spot,
            "net_premium_per_unit": self.net_premium_per_unit,
            "net_premium_inr": premium,
            "margin_required": round(self.margin_required, 2),
            "notes": list(self.notes),
        }


Problem = tuple[str, str]


def _leg(row: Optional[dict[str, Any]], right: str, action: str) -> Optional[PlanLeg]:
    if row is None:
        return None
    strike = parse_strike(row.get("strike_price"))
    quote = _row_quote(row)
    if strike is None or not quote.priceable:
        return None
    return PlanLeg(right=right, strike=float(strike), action=action, quote=quote)


def _strikes(
    structure: str,
    config: CasBingoConfig,
    calls: list[dict[str, Any]],
    puts: list[dict[str, Any]],
    reference: float,
) -> tuple[Optional[tuple[PlanLeg, ...]], Optional[Problem]]:
    """Resolve the legs for one structure against `reference`. Distances are % of reference,
    snapped away from it (market.pick_strike)."""
    def up(pct: float) -> float:
        return reference * (1 + pct / 100.0)

    def down(pct: float) -> float:
        return reference * (1 - pct / 100.0)

    c, d, s = config.credit, config.debit, config.strangle
    if structure == "bear_call_credit":
        sell = _leg(market.pick_strike(calls, up(c.inner_pct), outward_up=True), "call", cfg.SELL)
        buy = _leg(market.pick_strike(calls, up(c.outer_pct), outward_up=True), "call", cfg.BUY)
    elif structure == "bull_put_credit":
        sell = _leg(market.pick_strike(puts, down(c.inner_pct), outward_up=False), "put", cfg.SELL)
        buy = _leg(market.pick_strike(puts, down(c.outer_pct), outward_up=False), "put", cfg.BUY)
    elif structure == "bull_call_debit":
        buy = _leg(market.pick_strike(calls, up(d.inner_pct), outward_up=True), "call", cfg.BUY)
        sell = _leg(market.pick_strike(calls, up(d.outer_pct), outward_up=True), "call", cfg.SELL)
    elif structure == "bear_put_debit":
        buy = _leg(market.pick_strike(puts, down(d.inner_pct), outward_up=False), "put", cfg.BUY)
        sell = _leg(market.pick_strike(puts, down(d.outer_pct), outward_up=False), "put", cfg.SELL)
    else:
        ce = _leg(market.pick_strike(calls, up(s.call_pct), outward_up=True), "call", cfg.BUY)
        pe = _leg(market.pick_strike(puts, down(s.put_pct), outward_up=False), "put", cfg.BUY)
        if ce is None or pe is None:
            return None, (ReasonCode.QUOTE_UNAVAILABLE, "No two-sided quote at the strangle's strikes.")
        return (ce, pe), None

    if buy is None or sell is None:
        return None, (
            ReasonCode.QUOTE_UNAVAILABLE,
            f"No two-sided quote at the {STRUCTURE_LABEL[structure].lower()}'s strikes.",
        )
    if buy.strike == sell.strike:
        # Both distances snapped onto one listed strike: that is no spread at all.
        return None, (
            ReasonCode.NOTHING_ELIGIBLE,
            f"Both legs land on the {int(buy.strike)} strike; widen the outer distance.",
        )
    return (buy, sell), None


def _size_credit(
    proc: Any, user_id: str, plan_args: dict[str, Any], legs: tuple[PlanLeg, ...], budget: float
) -> tuple[int, float, Optional[Problem]]:
    """Largest whole-lot count whose broker margin fits `budget`. (lots, margin, problem).

    Both legs go to `margin_calculator` in one call with the hedge marked BUY, so the exchange
    nets them -- pricing the sold leg alone would charge a naked short's margin for a spread.
    """
    lot = plan_args["lot_size"]

    def margin_at(lots: int) -> Optional[float]:
        return margin_for_mixed_legs(
            proc,
            user_id,
            exchange_code=plan_args["exchange_code"],
            stock_code=plan_args["index_code"],
            expiry_display=plan_args["expiry_display"],
            legs=[(l.right, l.strike, lot * lots, l.action) for l in legs],
        )

    base = margin_at(1)
    if base is None:
        return 0, 0.0, (ReasonCode.MARGIN_LOOKUP_FAILED, "margin_calculator did not price the spread.")
    if base > budget:
        return 0, base, (
            ReasonCode.MARGIN_CAP_TOO_SMALL,
            f"One lot needs {base:,.0f} of margin against a {budget:,.0f} budget.",
        )
    candidate = int(budget // base)
    if candidate <= 1:
        return 1, base, None
    verified = margin_at(candidate)
    if verified is None:
        # The estimate could not be checked. One lot was actually priced; trade that rather
        # than a size nobody quoted.
        return 1, base, None
    if verified <= budget:
        return candidate, verified, None
    # Margin is not exactly linear in lots. The per-lot figure AT this size is a real quote,
    # so rescale by it instead of spending another call stepping down one lot at a time.
    per_lot = verified / candidate
    scaled = max(1, int(budget // per_lot))
    return scaled, (base if scaled == 1 else round(per_lot * scaled, 2)), None


def build_plan(
    proc: Any,
    user_id: str,
    config: CasBingoConfig,
    *,
    index_code: str,
    expiry_display: str,
    structure: str,
    day_open: Optional[float],
    spot: Optional[float] = None,
    calls: Optional[list[dict[str, Any]]] = None,
    puts: Optional[list[dict[str, Any]]] = None,
) -> tuple[Optional[Plan], Optional[Problem]]:
    """Price and size one structure. `calls`/`puts` may be passed in so the manual sheet
    prices five structures off one chain read per side."""
    family = STRUCTURES[structure][0]
    calls = calls if calls is not None else market.chain_rows(proc, user_id, index_code, expiry_display, "call")
    puts = puts if puts is not None else market.chain_rows(proc, user_id, index_code, expiry_display, "put")
    if not calls or not puts:
        return None, (ReasonCode.CHAIN_NOT_READY, f"The {expiry_display} chain is not ready on both sides.")

    spot = spot or market.index_spot(index_code) or market.spot_from(calls) or market.spot_from(puts)
    if family == "credit":
        if not day_open:
            return None, (
                ReasonCode.DAY_OPEN_UNAVAILABLE,
                "The day's open is not known yet, so credit strikes cannot be placed.",
            )
        reference, reference_kind = float(day_open), "open"
    else:
        if not spot:
            return None, (ReasonCode.QUOTE_UNAVAILABLE, "No live index level to measure from.")
        reference, reference_kind = float(spot), "spot"

    legs, problem = _strikes(structure, config, calls, puts, reference)
    if legs is None:
        return None, problem

    lot = market.lot_size(proc, index_code, expiry_display)
    if lot <= 0:
        return None, (ReasonCode.CHAIN_NOT_READY, "Lot size unavailable from the scrip master.")

    buys = [l for l in legs if not l.is_short]
    sells = [l for l in legs if l.is_short]
    # At the touch: a buy pays the ask, a sell receives the bid.
    net = round(sum(float(l.quote.bid) for l in sells) - sum(float(l.quote.ask) for l in buys), 2)

    notes: list[str] = []
    if family == "credit":
        if net <= 0:
            return None, (
                ReasonCode.NOTHING_ELIGIBLE,
                f"The spread collects no credit at the touch ({net:+.2f} per unit).",
            )
        lots, margin, problem = _size_credit(
            proc,
            user_id,
            {
                "lot_size": lot,
                "exchange_code": market.INDEX_EXCHANGE[index_code],
                "index_code": index_code,
                "expiry_display": expiry_display,
            },
            legs,
            config.credit.margin_lakhs * 100_000.0,
        )
        if problem is not None:
            return None, problem
        short = sells[0]
        if spot and (
            (short.right == "call" and short.strike < spot)
            or (short.right == "put" and short.strike > spot)
        ):
            notes.append(
                f"The sold {int(short.strike)} {'CE' if short.right == 'call' else 'PE'} is in the "
                f"money against spot {spot:,.2f} -- strikes are measured from the open "
                f"({reference:,.2f}), so this is the reversal bet."
            )
    else:
        debit = -net
        if debit <= 0:
            return None, (
                ReasonCode.NOTHING_ELIGIBLE,
                f"The {STRUCTURE_LABEL[structure].lower()} prices at no debit ({net:+.2f}); the "
                f"quotes look crossed.",
            )
        budget = config.debit.premium_budget_inr if family == "debit" else config.strangle.premium_budget_inr
        lots = int(math.floor(budget / (debit * lot)))
        if lots < 1:
            return None, (
                ReasonCode.OUTLAY_BELOW_ONE_LOT,
                f"One lot costs {debit * lot:,.0f} against a {budget:,.0f} premium budget.",
            )
        margin = round(debit * lot * lots, 2)

    return (
        Plan(
            index_code=index_code,
            expiry_display=expiry_display,
            structure=structure,
            legs=legs,
            lot_size=lot,
            lots=lots,
            reference=round(reference, 2),
            reference_kind=reference_kind,
            spot=round(float(spot), 2) if spot else None,
            net_premium_per_unit=net,
            margin_required=float(margin),
            notes=tuple(notes),
        ),
        None,
    )
