"""Bot 4 -- the ATM Iron Fly scalper's bot-specific layer (plan section 4).

The shared gate stack in `decide.py` says *whether* to act. This module says *what*: the
four strikes, how many lots fit under the margin ceiling, the order the legs must be sent
in, and what the position's live prices make of the credit it collected.

Three things here are load-bearing and easy to get quietly wrong:

* **Sizing goes through one `margin_calculator` call carrying all four legs**, with the
  wings marked BUY. Anything else -- pricing legs separately, or reusing Bot 2's short-only
  helper -- overstates a hedged structure's margin by the whole netting benefit the hedge
  exists to earn (see `margin.py`).
* **Wings are bought before the shorts are sold.** Selling first presents the broker with two
  naked legs and draws a margin rejection before the hedge exists. Non-negotiable, and the
  reason `entry_sequence()` exists as an explicit ordering rather than a list comprehension.
* **Credit decay is measured at the price the position could actually be unwound at** --
  shorts bought back at the ask, longs sold at the bid -- not at mid. A mid-priced decay
  target books a profit that the exit then fails to realise.
"""
from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass
from typing import Any, Optional

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.strike import parse_strike
from icici_breeze_backend.app.domain.bots import IronFlyScalperConfig, ReasonCode
from icici_breeze_backend.app.services.bots.scalping import spreads as spreads_mod
from icici_breeze_backend.app.services.bots.charges import ChargesModel
from icici_breeze_backend.app.services.bots.scalping.margin import margin_for_mixed_legs
from icici_breeze_backend.app.services.bots.scalping.momentum_bot import (
    INDEX_EXCHANGE,
    INDEX_STOCK_CODE,
    Quote,
    _chain_rows,
    _row_quote,
    _spot_from,
    atm_strike,
    live_quote,
    nearest_expiry,
)
from icici_breeze_backend.app.services.bots.scalping.paper import simulate_buy, simulate_sell

_logger = logging.getLogger(__name__)

# Sizing walks down from an estimate rather than probing every lot count: margin for N lots
# of one structure is very nearly N x the one-lot figure (the netting ratio is unchanged), so
# one call sizes it and one more verifies. This caps the walk when that assumption frays.
_MAX_SIZING_CALLS = 3


@dataclass(frozen=True)
class FlyLeg:
    right: str          # "call" | "put"
    strike: float
    action: str         # cfg.BUY | cfg.SELL
    quote: Quote

    @property
    def is_short(self) -> bool:
        return self.action == cfg.SELL


@dataclass(frozen=True)
class FlyPlan:
    expiry_display: str
    atm_strike: float
    wing_width: float
    legs: tuple[FlyLeg, ...]
    lot_size: int
    lots: int
    margin_required: float

    @property
    def quantity(self) -> int:
        return self.lot_size * self.lots

    def entry_sequence(self) -> list[FlyLeg]:
        """Wings first, then the ATM shorts. See the module docstring -- not cosmetic."""
        return [l for l in self.legs if not l.is_short] + [l for l in self.legs if l.is_short]

    def exit_sequence(self) -> list[FlyLeg]:
        """The reverse: buy back the shorts, then sell the wings.

        Selling the hedges while the shorts are still open inverts the margin mid-unwind --
        the same rejection risk as entry, in the other direction.
        """
        return [l for l in self.legs if l.is_short] + [l for l in self.legs if not l.is_short]

    def as_legs(self) -> list[dict[str, Any]]:
        return [
            {
                "stock_code": INDEX_STOCK_CODE,
                "exchange_code": INDEX_EXCHANGE,
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


def wing_width_for(config: IronFlyScalperConfig, vix: Optional[float]) -> float:
    """The configured width, widened above a VIX threshold when that rule is switched on.

    An unavailable VIX leaves the width alone rather than guessing: widening is a deliberate
    response to known-high volatility, and a missing reading is not that.
    """
    s = config.structure
    if s.widen_above_vix is None or vix is None:
        return s.wing_width_points
    return s.widened_wing_width_points if float(vix) > s.widen_above_vix else s.wing_width_points


def pick_wing(rows: list[dict[str, Any]], target: float, *, outward_up: bool) -> Optional[dict[str, Any]]:
    """The nearest listed strike at or beyond `target`, away from the money.

    Outward, never inward: a narrower wing collects less credit AND requires more margin, so
    snapping inward would degrade the trade on both axes at once.
    """
    best, best_distance = None, float("inf")
    for row in rows:
        strike = parse_strike(row.get("strike_price"))
        if strike is None:
            continue
        s = float(strike)
        if outward_up and s < target:
            continue
        if not outward_up and s > target:
            continue
        if abs(s - target) < best_distance:
            best, best_distance = row, abs(s - target)
    return best


def _leg_from(row: Optional[dict[str, Any]], right: str, action: str) -> Optional[FlyLeg]:
    if row is None:
        return None
    quote = _row_quote(row)
    strike = parse_strike(row.get("strike_price"))
    if strike is None or not quote.priceable:
        return None
    return FlyLeg(right=right, strike=float(strike), action=action, quote=quote)


def build_structure(
    proc: Any, user_id: str, config: IronFlyScalperConfig, vix: Optional[float]
) -> tuple[Optional[tuple[str, float, float, tuple[FlyLeg, ...]]], Optional[tuple[str, str]]]:
    """Resolve expiry, ATM and the four legs. Returns (structure, None) or (None, problem)."""
    expiry = nearest_expiry(proc)
    if not expiry:
        return None, (ReasonCode.CHAIN_NOT_READY, "No NIFTY expiry available from the scrip master.")

    calls = _chain_rows(proc, user_id, expiry, "call")
    puts = _chain_rows(proc, user_id, expiry, "put")
    if not calls or not puts:
        return None, (ReasonCode.CHAIN_NOT_READY, f"Chain for {expiry} is not ready on both sides.")

    spot = _spot_from(calls) or _spot_from(puts)
    atm = atm_strike(calls, spot)
    if atm is None:
        return None, (ReasonCode.QUOTE_UNAVAILABLE, "Could not resolve an ATM strike.")

    width = wing_width_for(config, vix)
    short_ce = _leg_from(next((r for r in calls if (parse_strike(r.get("strike_price")) or -1) == atm), None), "call", cfg.SELL)
    short_pe = _leg_from(next((r for r in puts if (parse_strike(r.get("strike_price")) or -1) == atm), None), "put", cfg.SELL)
    long_ce = _leg_from(pick_wing(calls, atm + width, outward_up=True), "call", cfg.BUY)
    long_pe = _leg_from(pick_wing(puts, atm - width, outward_up=False), "put", cfg.BUY)

    legs = (short_ce, short_pe, long_ce, long_pe)
    if any(leg is None for leg in legs):
        # No fly without four priceable legs: a wing that cannot be priced is a hedge that
        # cannot be established, and a fly missing its hedge is a short straddle in disguise.
        missing = [
            name for name, leg in zip(("short CE", "short PE", "long CE", "long PE"), legs) if leg is None
        ]
        return None, (
            ReasonCode.QUOTE_UNAVAILABLE,
            f"No two-sided quote on: {', '.join(missing)}. Skipping this cycle.",
        )

    for leg in legs:
        spreads_mod.record_spread_sample(
            INDEX_STOCK_CODE, expiry, leg.strike, leg.right, leg.quote.bid, leg.quote.ask
        )
    return (expiry, float(atm), width, tuple(legs)), None  # type: ignore[arg-type]


def size_fly(
    proc: Any,
    user_id: str,
    config: IronFlyScalperConfig,
    expiry: str,
    legs: tuple[FlyLeg, ...],
    lot_size: int,
) -> tuple[int, float, Optional[tuple[str, str]]]:
    """Largest whole-lot fly fitting under the margin ceiling. (lots, margin, problem).

    Never partial-funds: if even `min_lots` exceeds the ceiling the cycle is skipped, exactly
    as Bot 2 does. Sizing down to "whatever fits" would silently trade a different structure
    from the one that was evaluated.
    """
    def margin_at(lots: int) -> Optional[float]:
        return margin_for_mixed_legs(
            proc,
            user_id,
            exchange_code=INDEX_EXCHANGE,
            stock_code=INDEX_STOCK_CODE,
            expiry_display=expiry,
            legs=[(l.right, l.strike, lot_size * lots, l.action) for l in legs],
        )

    base = margin_at(config.min_lots)
    if base is None:
        return 0, 0.0, (ReasonCode.MARGIN_LOOKUP_FAILED, "margin_calculator did not price the fly.")
    if base > config.margin_ceiling_inr:
        return 0, base, (
            ReasonCode.MARGIN_CAP_TOO_SMALL,
            f"{config.min_lots} lot(s) needs {base:,.0f} against a "
            f"{config.margin_ceiling_inr:,.0f} ceiling.",
        )

    per_lot = base / max(1, config.min_lots)
    candidate = max(config.min_lots, int(config.margin_ceiling_inr // per_lot))
    margin = base
    calls_made = 1
    while candidate > config.min_lots and calls_made < _MAX_SIZING_CALLS:
        verified = margin_at(candidate)
        calls_made += 1
        if verified is not None and verified <= config.margin_ceiling_inr:
            return candidate, verified, None
        # Margin is not exactly linear in lots; step down rather than trusting the estimate.
        candidate -= 1
    return config.min_lots, margin, None


def plan_entry(
    proc: Any, user_id: str, config: IronFlyScalperConfig, vix: Optional[float] = None
) -> tuple[Optional[FlyPlan], Optional[tuple[str, str]]]:
    structure, problem = build_structure(proc, user_id, config, vix)
    if structure is None:
        return None, problem
    expiry, atm, width, legs = structure

    try:
        lot_size = int(proc.fetch_lot_size(INDEX_STOCK_CODE, expiry, exchange_code=INDEX_EXCHANGE) or 0)
    except Exception:  # noqa: BLE001
        lot_size = 0
    if lot_size <= 0:
        return None, (ReasonCode.CHAIN_NOT_READY, "Lot size unavailable from the scrip master.")

    lots, margin, problem = size_fly(proc, user_id, config, expiry, legs, lot_size)
    if problem is not None:
        return None, problem

    return (
        FlyPlan(
            expiry_display=expiry,
            atm_strike=atm,
            wing_width=width,
            legs=legs,
            lot_size=lot_size,
            lots=lots,
            margin_required=margin,
        ),
        None,
    )


# --------------------------------------------------------------------------------------
# Paper execution -- the sequence is simulated in full, including its unwind paths
# --------------------------------------------------------------------------------------


@dataclass
class FlyFills:
    filled: list[tuple[FlyLeg, Any]]
    net_credit_per_unit: float
    charges: float
    failed_leg: Optional[FlyLeg] = None

    @property
    def complete(self) -> bool:
        return self.failed_leg is None


def simulate_entry(plan: FlyPlan, charges: ChargesModel) -> FlyFills:
    """Fill the legs in dispatch order, stopping at the first that cannot be priced.

    Paper mode has no broker to reject an order, so the failure this models is the real one
    it *can* see: a leg with no usable quote. The point is that the sequence and its unwind
    paths exist and are exercised, not that a rejection is invented.
    """
    filled: list[tuple[FlyLeg, Any]] = []
    credit = 0.0
    total_charges = 0.0
    for leg in plan.entry_sequence():
        fn = simulate_sell if leg.is_short else simulate_buy
        fill = fn(leg.quote.bid, leg.quote.ask, plan.quantity, charges)
        if fill is None:
            return FlyFills(filled, credit, total_charges, failed_leg=leg)
        filled.append((leg, fill))
        credit += fill.price if leg.is_short else -fill.price
        total_charges += fill.charges
    return FlyFills(filled, round(credit, 2), round(total_charges, 2))


def unwind_reason(fills: FlyFills) -> tuple[str, str]:
    """Why a partially-filled entry was abandoned, and what state it left behind.

    The two cases are genuinely different and must not collapse into one message: wings-only
    is a bounded, harmless leftover, while a single short against both wings is a real
    position -- bounded risk, wrong trade -- that has to be closed at once.
    """
    shorts_on = [leg for leg, _ in fills.filled if leg.is_short]
    if not shorts_on:
        return (
            ReasonCode.ENTRY_UNFILLED,
            "A wing could not be priced; nothing was sold and the filled wings were unwound.",
        )
    return (
        ReasonCode.ENTRY_PARTIAL_UNWOUND,
        f"{len(shorts_on)} of 2 short legs filled before a leg failed; the position was a "
        f"long strangle (bounded risk, wrong trade) and was closed immediately.",
    )


def cost_to_close(legs: list[dict[str, Any]], quotes: dict[str, Quote]) -> Optional[float]:
    """Per unit, at prices the position could actually be unwound at.

    Shorts are bought back at the **ask** and longs sold at the **bid**. Using mid would
    report a decay the exit cannot realise -- booking a profit that evaporates on the way out.
    """
    total = 0.0
    for leg in legs:
        quote = quotes.get(_leg_key(leg))
        if quote is None:
            return None
        if leg.get("action") == cfg.SELL:
            if quote.ask is None:
                return None
            total += float(quote.ask)
        else:
            if quote.bid is None:
                return None
            total -= float(quote.bid)
    return round(total, 2)


def _leg_key(leg: dict[str, Any]) -> str:
    return f"{leg.get('right')}|{leg.get('strike_price')}"


def fetch_leg_quotes(proc: Any, user_id: str, legs: list[dict[str, Any]]) -> dict[str, Quote]:
    """Live quotes for every leg, cache-first. Four reads, no REST during market hours."""
    quotes: dict[str, Quote] = {}
    for leg in legs:
        quote = live_quote(
            proc,
            user_id,
            str(leg.get("expiry_display") or ""),
            float(leg.get("strike_price") or 0),
            str(leg.get("right") or "call"),
        )
        quotes[_leg_key(leg)] = quote
        spreads_mod.sample_from_quote(leg, quote.bid, quote.ask)
    return quotes


def evaluate_exit(
    config: IronFlyScalperConfig,
    *,
    net_credit_per_unit: float,
    close_cost_per_unit: Optional[float],
    quantity: int,
    spot: Optional[float],
    atm_strike_price: float,
) -> Optional[tuple[str, str]]:
    """The fly's own exit verdict, or None to hold.

    Drift is checked FIRST. By the time a short ATM leg has moved this far the tested side is
    expanding on gamma, and waiting for a P&L stop means waiting for a number that arrives
    faster than an exit can be placed.
    """
    if spot is not None and atm_strike_price > 0:
        drift_pct = abs(float(spot) - atm_strike_price) / atm_strike_price * 100.0
        if drift_pct >= config.exits.max_spot_drift_pct:
            return (
                ReasonCode.DRIFT_STOP,
                f"Spot has moved {drift_pct:.2f}% from the {int(atm_strike_price)} centre "
                f"(limit {config.exits.max_spot_drift_pct:.2f}%); closing before gamma does.",
            )

    if close_cost_per_unit is None:
        return None  # cannot price the unwind this pass; the shared stale gate escalates

    profit_per_unit = float(net_credit_per_unit) - float(close_cost_per_unit)
    profit_inr = profit_per_unit * quantity
    credit_inr = float(net_credit_per_unit) * quantity

    if credit_inr > 0 and profit_per_unit >= float(net_credit_per_unit) * config.exits.target_decay_pct / 100.0:
        return (
            ReasonCode.CREDIT_DECAY_TARGET,
            f"Credit decayed {100 * profit_per_unit / net_credit_per_unit:.1f}% "
            f"(+{profit_inr:,.0f}); booking at the {config.exits.target_decay_pct:.0f}% target.",
        )

    limit = config.exits.loss_limit_inr(credit_inr)
    if limit is not None and profit_inr <= -abs(limit):
        return (
            ReasonCode.STOP_LOSS,
            f"Position is down {abs(profit_inr):,.0f} against a {abs(limit):,.0f} limit.",
        )
    return None


def spot_range_pct(candles: list, minutes: int) -> Optional[float]:
    """High-low range over the last `minutes` completed bars, as a percentage.

    Read off the futures candles the signal already builds rather than a second spot buffer:
    over a ten-minute window the futures basis is stable, so the range is the same shape, and
    it costs nothing extra.
    """
    window = [c for c in candles][-max(1, int(minutes)) :]
    if not window:
        return None
    high = max(c.high for c in window)
    low = min(c.low for c in window)
    if low <= 0:
        return None
    return (high - low) / low * 100.0


def reentry_blocked(
    config: IronFlyScalperConfig,
    *,
    now: datetime.datetime,
    last_closed_at: Optional[str],
    candles: list,
) -> Optional[tuple[str, str]]:
    """Both conditions must clear before a new fly. None means go.

    The cooldown alone would re-centre into an ongoing move and get stopped again -- the
    characteristic way a short-premium strategy bleeds. The range test alone can re-fire
    immediately in a chop that keeps clearing the band.
    """
    if last_closed_at:
        try:
            last = datetime.datetime.strptime(str(last_closed_at)[:19], "%Y-%m-%d %H:%M:%S")
            waited = (now.replace(tzinfo=None) - last).total_seconds() / 60.0
            if waited < config.reentry.cooldown_minutes:
                return (
                    ReasonCode.REENTRY_GATE_CLOSED,
                    f"{waited:.0f} of {config.reentry.cooldown_minutes} cooldown minutes "
                    f"elapsed since the last fly closed.",
                )
        except (TypeError, ValueError):
            return (ReasonCode.REENTRY_GATE_CLOSED, "Last close time unreadable; holding off.")

    rng = spot_range_pct(candles, config.reentry.range_window_minutes)
    if rng is None:
        return (ReasonCode.NOT_WARM, "No candle history yet to judge whether spot has settled.")
    if rng > config.reentry.max_range_pct:
        return (
            ReasonCode.REENTRY_GATE_CLOSED,
            f"Spot ranged {rng:.2f}% over the last {config.reentry.range_window_minutes} "
            f"minutes, above the {config.reentry.max_range_pct:.2f}% settle threshold.",
        )
    return None


# --------------------------------------------------------------------------------------
# Orchestration -- called by the driver, which owns the clock and the gate stack
# --------------------------------------------------------------------------------------


@dataclass
class FlyContext:
    cycle: Any
    quotes: dict[str, Quote]
    close_cost: Optional[float]
    verdict: Optional[tuple[str, str]]
    spot: Optional[float]


def _cycle_spot(proc: Any, user_id: str, cycle: Any) -> Optional[float]:
    legs = cycle.legs or []
    if not legs:
        return None
    rows = _chain_rows(proc, user_id, str(legs[0].get("expiry_display") or ""), "call")
    spot = _spot_from(rows)
    return spot or None


def inspect_position(
    proc: Any, user_id: str, config: IronFlyScalperConfig, bot_type: str
) -> Optional[FlyContext]:
    from icici_breeze_backend.app.repositories import bots as repo

    open_cycles = repo.open_cycles(user_id, bot_type)
    if not open_cycles:
        return None
    cycle = open_cycles[0]
    legs = cycle.legs or []
    quotes = fetch_leg_quotes(proc, user_id, legs)
    close_cost = cost_to_close(legs, quotes)
    detail = cycle.detail or {}
    spot = _cycle_spot(proc, user_id, cycle)
    verdict = evaluate_exit(
        config,
        net_credit_per_unit=float(detail.get("net_credit_per_unit") or 0),
        close_cost_per_unit=close_cost,
        quantity=int((legs[0] or {}).get("quantity") or 0) if legs else 0,
        spot=spot,
        atm_strike_price=float(detail.get("atm_strike") or 0),
    )
    return FlyContext(cycle=cycle, quotes=quotes, close_cost=close_cost, verdict=verdict, spot=spot)


def execute(
    proc: Any,
    user_id: str,
    bot_type: str,
    config: IronFlyScalperConfig,
    run_id: str,
    decision: Any,
    context: Optional[FlyContext],
    charges: ChargesModel,
    candles: list,
    now: datetime.datetime,
    vix: Optional[float] = None,
) -> None:
    """Carry out one decision."""
    from icici_breeze_backend.app.repositories import bots as repo

    # An open position is managed the way it was OPENED, not the way the bot is set now --
    # the same invariant `momentum_bot.execute` documents. Routing a real fly's unwind on
    # `config.mode` would let a user who stepped the bot back to Paper (or switched it off,
    # which is the `entries_suspended` tick) close four legs at simulated prices while the
    # actual position stayed at the exchange.
    live_path = context.cycle.paper is False if context is not None else config.mode == "live"

    if decision.action == "exit" and context is not None:
        if live_path:
            _close_live(proc, user_id, config, context, decision, charges)
        else:
            _close(repo, context, decision, charges)
        return

    if decision.action != "enter":
        return

    totals = repo.scalper_day_totals(user_id, bot_type)
    blocked = reentry_blocked(
        config, now=now, last_closed_at=totals.last_closed_at, candles=candles
    )
    if blocked is not None:
        _logger.debug("iron fly: re-entry held -- %s", blocked[1])
        return

    plan, problem = plan_entry(proc, user_id, config, vix)
    if plan is None:
        code, text = problem or (ReasonCode.INTERNAL_ERROR, "Entry could not be planned.")
        _logger.info("iron fly: entry skipped -- %s: %s", code, text)
        return

    if live_path:
        _open_live(proc, user_id, bot_type, config, run_id, plan, charges)
        return

    fills = simulate_entry(plan, charges)
    if not fills.complete:
        # The unwind path. Nothing is left open in paper mode, but the outcome is recorded as
        # a cycle so the run log shows an attempt that failed rather than a silent gap.
        code, text = unwind_reason(fills)
        cycle = repo.open_cycle(
            user_id, bot_type, run_id,
            structure="iron_fly", legs=plan.as_legs(), lots=plan.lots,
            entry_value=0.0, paper=True,
            detail={"aborted": True, "filled_legs": len(fills.filled)},
        )
        repo.close_cycle(
            cycle.id, exit_reason_code=code, exit_reason_text=text,
            gross_pnl=0.0, friction=round(fills.charges, 2),
        )
        _logger.warning("iron fly: %s", text)
        return

    quantity = plan.quantity
    cycle = repo.open_cycle(
        user_id, bot_type, run_id,
        structure="iron_fly",
        legs=plan.as_legs(),
        lots=plan.lots,
        entry_value=round(fills.net_credit_per_unit * quantity, 2),
        paper=True,
        detail={
            "net_credit_per_unit": fills.net_credit_per_unit,
            "net_credit_inr": round(fills.net_credit_per_unit * quantity, 2),
            "atm_strike": plan.atm_strike,
            "wing_width": plan.wing_width,
            "margin_required": plan.margin_required,
            "entry_charges": fills.charges,
            "loss_limit_inr": config.exits.loss_limit_inr(fills.net_credit_per_unit * quantity),
            "entry_fills": [
                {"right": leg.right, "strike": leg.strike, "action": leg.action, "price": fill.price}
                for leg, fill in fills.filled
            ],
        },
    )
    _logger.info(
        "iron fly: opened cycle %s -- %d lots, centre %d, wings +/-%d, credit %.0f, margin %.0f",
        cycle.cycle_no, plan.lots, int(plan.atm_strike), int(plan.wing_width),
        fills.net_credit_per_unit * quantity, plan.margin_required,
    )


def _close(repo: Any, context: FlyContext, decision: Any, charges: ChargesModel) -> None:
    detail = dict(context.cycle.detail or {})
    legs = context.cycle.legs or []
    quantity = int((legs[0] or {}).get("quantity") or 0) if legs else 0
    if context.close_cost is None or quantity <= 0:
        _logger.warning(
            "iron fly: cannot price an unwind for cycle %s; leaving it open", context.cycle.id
        )
        return

    net_credit = float(detail.get("net_credit_per_unit") or 0)
    gross = round((net_credit - context.close_cost) * quantity, 2)
    exit_charges = 0.0
    for leg in legs:
        quote = context.quotes.get(_leg_key(leg))
        if quote is None:
            continue
        # Closing reverses each leg: a short is bought back, a long is sold.
        is_buy = leg.get("action") == cfg.SELL
        price = (quote.ask if is_buy else quote.bid) or 0.0
        exit_charges += charges.leg_charges(price, quantity, is_buy=is_buy)

    detail["exit"] = {
        "close_cost_per_unit": context.close_cost,
        "spot": context.spot,
        "charges": round(exit_charges, 2),
    }
    repo.close_cycle(
        context.cycle.id,
        exit_reason_code=decision.reason_code,
        exit_reason_text=decision.reason_text,
        exit_value=round(context.close_cost * quantity, 2),
        gross_pnl=gross,
        friction=round(float(detail.get("entry_charges") or 0) + exit_charges, 2),
        detail=detail,
    )
    _logger.info(
        "iron fly: closed cycle %s -- %s, gross %+.2f", context.cycle.cycle_no,
        decision.reason_code, gross,
    )


# --------------------------------------------------------------------------------------
# Live dispatch
#
# Four legs, one at a time, wings first (plan section 4.3). Everything below exists because
# a four-leg structure fails differently from Bot 3's single leg:
#
# * **A partial fill on any leg is a failure, not a smaller position.** Bot 3 adopts a
#   partial and manages it -- the ladder works the same on 25 units as on 75. A fly with 75
#   units on one wing and 50 on a short is not a fly at all: the wing no longer covers the
#   short it was bought to cover, and no exit rule in this module describes what it is. So a
#   partial is unwound with everything else.
# * **The unwind has an order, and it is not the entry's reverse by accident.** Shorts are
#   bought back before wings are sold. Selling the hedge while a short is still open leaves,
#   for the duration of one order, exactly the naked short the wings-first rule exists to
#   prevent.
# * **A failed unwind is the worst state this bot can reach** and is never retried silently.
#   It alerts, disarms, and leaves the cycle open for a human, because the alternative --
#   firing more orders at a market that is refusing them -- spends friction to make an
#   unaccounted position worse.
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _LegFill:
    """One leg that actually reached the exchange, at the size it actually filled."""

    leg: FlyLeg
    quantity: int
    price: float
    order_id: str


def _leg_order(plan: FlyPlan, leg: FlyLeg, *, action: str, quantity: int) -> Any:
    from icici_breeze_backend.app.services.bots.scalping import live

    return live.LegOrder(
        stock_code=INDEX_STOCK_CODE,
        exchange_code=INDEX_EXCHANGE,
        right=leg.right,
        strike_price=leg.strike,
        expiry_display=plan.expiry_display,
        action=action,
        quantity=int(quantity),
    )


def _entry_ladder(leg: FlyLeg, config: IronFlyScalperConfig) -> Any:
    """A marketable limit on the correct side, and deliberately not a chasing one.

    An entry that does not fill is a trade not taken, which costs nothing -- so neither side
    widens on retry (`widen=0.0`). Only exits chase, because an exit that does not fill is a
    position with nothing behind it.
    """
    from icici_breeze_backend.app.services.bots.scalping import live

    tol = config.execution.entry_limit_tolerance_pct
    if leg.is_short:
        return live.exit_price_ladder(float(leg.quote.bid or 0), tol, widen=0.0)
    return live.entry_price_ladder(float(leg.quote.ask or 0), tol)


def _place_leg(proc, user_id, plan, leg, config, *, action, quantity, ladder) -> Any:
    from icici_breeze_backend.app.services.bots.scalping import live

    return live.place_and_confirm(
        proc,
        user_id,
        _leg_order(plan, leg, action=action, quantity=quantity),
        price_for_attempt=ladder,
        timeout_seconds=config.execution.entry_fill_timeout_seconds,
        attempts=max(1, config.execution.entry_retries),
    )


def _open_live(
    proc: Any,
    user_id: str,
    bot_type: str,
    config: IronFlyScalperConfig,
    run_id: str,
    plan: FlyPlan,
    charges: ChargesModel,
) -> None:
    """Build the fly for real: wings, then shorts, one order at a time."""
    from icici_breeze_backend.app.repositories import bots as repo
    from icici_breeze_backend.app.services.bots.scalping import guards

    # An unresolved intent means an order exists that nothing can account for. Building a
    # second four-leg structure on top of that is how one crash becomes two positions.
    if guards.has_unresolved_intent(user_id, bot_type):
        _logger.warning("iron fly [LIVE]: an unreconciled order is outstanding; not entering")
        return

    # The row goes in BEFORE any order does, so a crash between `place_order` returning and
    # the row being written leaves a question `reconcile_pending_cycles` can answer rather
    # than four legs nobody knows about.
    cycle = repo.open_cycle(
        user_id, bot_type, run_id,
        structure="iron_fly", legs=plan.as_legs(), lots=plan.lots,
        entry_value=None, paper=False,
        detail={
            "pending": True, "order_ids": [],
            "atm_strike": plan.atm_strike, "wing_width": plan.wing_width,
            "margin_required": plan.margin_required,
        },
    )

    placed: list[_LegFill] = []
    order_ids: list[str] = []
    failed_leg: Optional[FlyLeg] = None
    failure_text = ""

    for leg in plan.entry_sequence():
        result = _place_leg(
            proc, user_id, plan, leg, config,
            action=leg.action, quantity=plan.quantity, ladder=_entry_ladder(leg, config),
        )
        if result.order_id:
            order_ids.append(result.order_id)

        if result.cancel_failed:
            # The one case that must NOT unwind. An order believed dead but still live will
            # fill later; unwinding around it would build a position out of a guess. Freeze
            # everything and hand it to a human.
            repo.mark_cycle_placed(
                cycle.id, order_ids=order_ids,
                detail={"cancel_failed": True, "filled_legs": len(placed)},
            )
            guards.disarm_bot(
                user_id, bot_type, result.error or "An order could not be cancelled."
            )
            _alert_stuck(user_id, "an order could not be cancelled mid-entry", result.error)
            return

        if result.filled_quantity > 0:
            placed.append(
                _LegFill(
                    leg=leg,
                    quantity=int(result.filled_quantity),
                    price=float(result.average_price or 0.0),
                    order_id=str(result.order_id or ""),
                )
            )

        # A partial counts as a failure here even though units did fill -- see the section
        # header. `result.ok` is already full-quantity-only, so this is just naming it.
        if not result.ok:
            failed_leg = leg
            failure_text = result.error or "The leg did not fill."
            break

    if failed_leg is not None:
        _abort_live_entry(
            proc, user_id, bot_type, config, cycle, plan, placed, order_ids,
            failed_leg, failure_text, charges,
        )
        return

    _record_live_entry(repo, cycle, plan, placed, order_ids, config, charges)


def _abort_live_entry(
    proc, user_id, bot_type, config, cycle, plan, placed, order_ids,
    failed_leg, failure_text, charges,
) -> None:
    """Unwind whatever reached the exchange, then close the row as an abandoned attempt."""
    from icici_breeze_backend.app.repositories import bots as repo
    from icici_breeze_backend.app.services.bots.scalping import guards

    shorts_on = [f for f in placed if f.leg.is_short]
    code, text = unwind_reason(
        FlyFills(filled=[(f.leg, None) for f in placed], net_credit_per_unit=0.0, charges=0.0,
                 failed_leg=failed_leg)
    )

    unwound, stuck = _unwind_legs(proc, user_id, config, plan, placed)

    detail = dict(cycle.detail or {})
    detail.update({
        "aborted": True,
        "filled_legs": len(placed),
        "failed_leg": {"right": failed_leg.right, "strike": failed_leg.strike},
        "failure": failure_text,
        "unwound_legs": len(unwound),
        "stuck_legs": [
            {"right": f.leg.right, "strike": f.leg.strike, "quantity": f.quantity} for f in stuck
        ],
    })

    if stuck:
        # Legs are still on and could not be closed. This is the state that must never be
        # tidied away: the row stays OPEN so the exit loop keeps trying to manage it, the bot
        # is disarmed so it opens nothing else, and the user is told exactly what is live.
        repo.mark_cycle_placed(cycle.id, order_ids=order_ids, detail=detail)
        guards.disarm_bot(user_id, bot_type, "An entry could not be unwound cleanly.")
        _alert_stuck(
            user_id,
            "an entry failed and could not be fully unwound",
            "; ".join(
                f"{f.leg.right} {int(f.leg.strike)} x{f.quantity} still open" for f in stuck
            ),
        )
        return

    friction = round(
        sum(
            charges.leg_charges(f.price, f.quantity, is_buy=not f.leg.is_short)
            for f in placed
        )
        * 2,  # each unwound leg paid charges on the way in AND on the way back out
        2,
    )
    repo.close_cycle(
        cycle.id, exit_reason_code=code, exit_reason_text=text,
        gross_pnl=0.0, friction=friction, detail=detail,
    )
    _logger.warning(
        "iron fly [LIVE]: %s (%d leg(s) unwound, %d short(s) had filled)",
        text, len(unwound), len(shorts_on),
    )


def _unwind_legs(proc, user_id, config, plan, placed: list[_LegFill]):
    """Close filled legs, shorts first. Returns `(unwound, stuck)`.

    Shorts first is the whole reason this is not a loop over `placed` in fill order: selling
    a wing while its short is still open leaves a naked short for the life of one order, and
    that is exactly what the wings-first entry rule exists to avoid.
    """
    from icici_breeze_backend.app.services.bots.scalping import live

    ordered = [f for f in placed if f.leg.is_short] + [f for f in placed if not f.leg.is_short]
    unwound: list[_LegFill] = []
    stuck: list[_LegFill] = []
    for fill in ordered:
        quote = live_quote(
            proc, user_id, plan.expiry_display, fill.leg.strike, fill.leg.right
        )
        band = config.execution.exit_limit_band_pct
        if fill.leg.is_short:
            action, ladder = cfg.BUY, live.buyback_price_ladder(
                float(quote.ask or fill.price or 0.05), band
            )
        else:
            action, ladder = cfg.SELL, live.exit_price_ladder(
                float(quote.bid or fill.price or 0.05), band
            )
        result = _place_leg(
            proc, user_id, plan, fill.leg, config,
            action=action, quantity=fill.quantity, ladder=ladder,
        )
        (unwound if result.ok else stuck).append(fill)
    return unwound, stuck


def _record_live_entry(repo, cycle, plan, placed, order_ids, config, charges) -> None:
    """A complete fly. Credit is computed from the prices that actually filled."""
    quantity = plan.quantity
    credit = round(
        sum(f.price if f.leg.is_short else -f.price for f in placed), 2
    )
    entry_charges = round(
        sum(
            charges.leg_charges(f.price, f.quantity, is_buy=not f.leg.is_short)
            for f in placed
        ),
        2,
    )
    detail = dict(cycle.detail or {})
    # `pending` is not set False here -- `mark_cycle_placed` removes the key outright, which
    # is what makes the row stop being a reconciliation question.
    detail.update({
        "net_credit_per_unit": credit,
        "net_credit_inr": round(credit * quantity, 2),
        "entry_charges": entry_charges,
        "loss_limit_inr": config.exits.loss_limit_inr(credit * quantity),
        "entry_fills": [
            {
                "right": f.leg.right, "strike": f.leg.strike, "action": f.leg.action,
                "price": f.price, "quantity": f.quantity, "order_id": f.order_id,
            }
            for f in placed
        ],
    })
    repo.mark_cycle_placed(cycle.id, order_ids=order_ids, detail=detail)
    _logger.info(
        "iron fly [LIVE]: opened cycle %s -- %d lots, centre %d, credit %.0f",
        cycle.cycle_no, plan.lots, int(plan.atm_strike), credit * quantity,
    )


def _close_live(
    proc: Any,
    user_id: str,
    config: IronFlyScalperConfig,
    context: FlyContext,
    decision: Any,
    charges: ChargesModel,
) -> None:
    """Unwind a real fly: buy the shorts back, then sell the wings."""
    from icici_breeze_backend.app.repositories import bots as repo
    from icici_breeze_backend.app.services.bots.scalping import guards, live

    legs = context.cycle.legs or []
    if not legs:
        _logger.warning("iron fly [LIVE]: cycle %s has no legs to close", context.cycle.id)
        return

    detail = dict(context.cycle.detail or {})
    expiry = str((legs[0] or {}).get("expiry_display") or "")
    shorts = [l for l in legs if l.get("action") == cfg.SELL]
    longs = [l for l in legs if l.get("action") != cfg.SELL]

    closed: list[dict[str, Any]] = []
    stuck: list[dict[str, Any]] = []
    exit_cost = 0.0
    exit_charges = 0.0

    for leg in shorts + longs:
        quantity = int(leg.get("quantity") or 0)
        if quantity <= 0:
            continue
        right = str(leg.get("right") or "call")
        strike = float(leg.get("strike_price") or 0)
        quote = context.quotes.get(_leg_key(leg)) or live_quote(
            proc, user_id, expiry, strike, right
        )
        is_short = leg.get("action") == cfg.SELL
        band = config.execution.exit_limit_band_pct
        if is_short:
            action = cfg.BUY
            ladder = live.buyback_price_ladder(float(quote.ask or 0.05), band)
        else:
            action = cfg.SELL
            ladder = live.exit_price_ladder(float(quote.bid or 0.05), band)

        result = live.place_and_confirm(
            proc, user_id,
            live.LegOrder(
                stock_code=INDEX_STOCK_CODE, exchange_code=INDEX_EXCHANGE, right=right,
                strike_price=strike, expiry_display=expiry, action=action, quantity=quantity,
            ),
            price_for_attempt=ladder,
            timeout_seconds=config.execution.entry_fill_timeout_seconds,
            # An exit gets more attempts than an entry: there is a position behind it.
            attempts=3,
        )
        if not result.ok:
            stuck.append({"right": right, "strike": strike, "quantity": quantity,
                          "error": result.error})
            continue
        price = float(result.average_price or 0.0)
        # Closing reverses the leg: a short is bought back (a cost), a long is sold (a credit).
        exit_cost += price if is_short else -price
        exit_charges += charges.leg_charges(price, quantity, is_buy=is_short)
        closed.append({"right": right, "strike": strike, "price": price,
                       "order_id": result.order_id})

    if stuck:
        # Some legs are still on. Do NOT close the row -- the position is real and the exit
        # loop must keep trying on later passes. Alert once and stand the bot down; firing
        # more orders this pass into a market that just refused them buys nothing.
        detail["exit_partial"] = {"closed": closed, "stuck": stuck}
        repo.update_cycle_detail(context.cycle.id, detail)
        guards.disarm_bot(user_id, context.cycle.bot_type, "A fly could not be fully closed.")
        _alert_stuck(
            user_id, "a fly could not be fully closed",
            "; ".join(f"{s['right']} {int(s['strike'])} x{s['quantity']}" for s in stuck),
        )
        return

    quantity = int((legs[0] or {}).get("quantity") or 0)
    net_credit = float(detail.get("net_credit_per_unit") or 0)
    gross = round((net_credit - exit_cost) * quantity, 2)
    detail["exit"] = {
        "close_cost_per_unit": round(exit_cost, 2),
        "spot": context.spot,
        "charges": round(exit_charges, 2),
        "legs": closed,
    }
    repo.close_cycle(
        context.cycle.id,
        exit_reason_code=decision.reason_code,
        exit_reason_text=decision.reason_text,
        exit_value=round(exit_cost * quantity, 2),
        gross_pnl=gross,
        friction=round(float(detail.get("entry_charges") or 0) + exit_charges, 2),
        detail=detail,
    )
    _logger.info(
        "iron fly [LIVE]: closed cycle %s -- %s, gross %+.2f",
        context.cycle.cycle_no, decision.reason_code, gross,
    )


def _alert_stuck(user_id: str, what: str, detail: Optional[str]) -> None:
    """Tell the user a real position needs their hands. Never silent, never retried away."""
    from icici_breeze_backend.app.services.telegram_alerts import _notify

    try:
        _notify(
            user_id,
            "\U0001f6d1 <b>Iron fly needs checking</b>\n\n"
            f"The bot stopped because {what}.\n\n"
            f"{detail or ''}\n\n"
            "<b>Check the Order Book for open legs.</b> The bot has been disarmed and will "
            "not open anything new.",
            kind="scalping_fly_stuck",
        )
    except Exception:  # noqa: BLE001 -- an alert failure must not mask the original problem
        _logger.exception("iron fly: could not send the stuck-position alert")
