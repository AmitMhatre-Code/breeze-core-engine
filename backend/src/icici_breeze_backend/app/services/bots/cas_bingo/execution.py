"""CAS Bingo's order handling (docs/bots-cas-bingo-plan.md sections 7 and 9).

Entries, exits, settlement and the liquidation buy-backs, in both Simulation (paper fills at
the touch, `scalping/paper.py`) and Autonomous/Manual (real orders through
`scalping/live.place_and_confirm`, one at a time behind the serialized pacer -- #24).

The live rules, each borrowed from Bot 4 for the reason Bot 4 gives:

* **The intent row goes in before any order** (`detail.pending`), so a crash between an order
  going out and the row being written is a question `guards.reconcile_pending_cycles` can
  answer at startup, not a position nobody knows about.
* **Buys first; a sell that fails unwinds the buy.** The user confirmed the unwind (not "keep
  the long and alert"). A partial on any leg counts as a failure: a spread with 75 units bought
  and 50 sold is not the structure any exit rule here describes.
* **A failed unwind or exit is never retried silently.** The row stays open, the bot is
  disarmed, and the user is told exactly which legs are live.

An open position is managed the way it was OPENED (`cycle.paper`), never by the bot's current
mode -- a real spread must not be "closed" at simulated prices because the user flipped the
card back to Simulation.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.db.bots_migrate import BOT_CAS_BINGO
from icici_breeze_backend.app.domain.bots import CasBingoConfig, ReasonCode
from icici_breeze_backend.app.services.bots.cas_bingo import liquidation as liq
from icici_breeze_backend.app.services.bots.cas_bingo import market
from icici_breeze_backend.app.services.bots.cas_bingo.plan import Plan, PlanLeg
from icici_breeze_backend.app.services.bots.charges import ChargesModel
from icici_breeze_backend.app.services.bots.scalping.momentum_bot import Quote
from icici_breeze_backend.app.services.bots.scalping.paper import simulate_buy, simulate_sell

_logger = logging.getLogger(__name__)

# Liquidation: after the first round of buy-backs, at most this many more rounds of
# "re-read the limits, buy back the next candidate" before giving up (plan section 9.5).
_MAX_EXTRA_LIQUIDATION_ROUNDS = 2
# An exit gets more attempts than an entry: there is a position behind it.
_EXIT_ATTEMPTS = 3


@dataclass
class EntryOutcome:
    opened: bool
    reason_code: str
    reason_text: str
    # True when the answer ends this index's day (a genuine "no"), False when it is worth
    # asking again on a later pass (a chain still warming, a margin call that blipped).
    terminal: bool = True
    cycle_id: Optional[str] = None
    liquidation: Optional[dict[str, Any]] = None
    detail: dict[str, Any] = field(default_factory=dict)


def exits_for(config: CasBingoConfig, family: str) -> tuple[float, float]:
    """(target_pct, stop_loss_pct) for a structure family."""
    block = {"credit": config.credit, "debit": config.debit}.get(family, config.strangle)
    return float(block.target_pct), float(block.stop_loss_pct)


# --------------------------------------------------------------------------------------
# Valuation -- at prices the position could actually be closed at
# --------------------------------------------------------------------------------------


def _key(leg: dict[str, Any]) -> tuple[str, float]:
    return (str(leg.get("right") or "call"), float(leg.get("strike_price") or 0))


def leg_quotes(proc: Any, user_id: str, cycle: Any) -> dict[tuple[str, float], Quote]:
    detail = cycle.detail or {}
    index_code = str(detail.get("index_code") or (cycle.legs or [{}])[0].get("stock_code") or "NIFTY")
    quotes: dict[tuple[str, float], Quote] = {}
    for leg in cycle.legs or []:
        quotes[_key(leg)] = market.live_quote(
            proc, user_id, index_code, str(leg.get("expiry_display") or ""),
            float(leg.get("strike_price") or 0), str(leg.get("right") or "call"),
        )
    return quotes


def close_value_per_unit(
    legs: list[dict[str, Any]], quotes: dict[tuple[str, float], Quote]
) -> Optional[float]:
    """What closing yields per unit: longs sold at the bid, shorts bought back at the ask.
    Mid would report a profit the exit then fails to realise (Bot 4's lesson)."""
    total = 0.0
    for leg in legs:
        quote = quotes.get(_key(leg))
        if quote is None:
            return None
        if leg.get("action") == cfg.SELL:
            if not quote.ask:
                return None
            total -= float(quote.ask)
        else:
            if not quote.bid:
                return None
            total += float(quote.bid)
    return round(total, 2)


def evaluate_exit(
    config: CasBingoConfig, cycle: Any, close_value: Optional[float]
) -> Optional[tuple[str, str]]:
    """The bot's own exit verdict against the net entry premium, or None to hold.

    One formula for every family: P&L per unit = net entry (credit +, debit -) + close value.
    Credit targets are a share of the credit captured; debit/strangle targets a gain on the
    debit. Stops are a loss as a share of the same premium.
    """
    if close_value is None:
        return None
    detail = cycle.detail or {}
    net_entry = float(detail.get("net_entry_per_unit") or 0)
    base = abs(net_entry)
    if base <= 0:
        return None
    quantity = int(((cycle.legs or [{}])[0] or {}).get("quantity") or 0)
    pnl = net_entry + close_value
    target_pct, stop_pct = exits_for(config, str(detail.get("family") or ""))
    if pnl >= base * target_pct / 100.0:
        return (
            ReasonCode.TARGET_REACHED,
            f"Up {pnl * quantity:,.0f} ({100 * pnl / base:.0f}% of the {base:.2f} premium); "
            f"booking at the {target_pct:.0f}% target.",
        )
    if pnl <= -base * stop_pct / 100.0:
        return (
            ReasonCode.STOP_LOSS,
            f"Down {abs(pnl) * quantity:,.0f} ({100 * abs(pnl) / base:.0f}% of the "
            f"{base:.2f} premium) against a {stop_pct:.0f}% stop.",
        )
    return None


def settle_value_per_unit(legs: list[dict[str, Any]], level: float) -> float:
    """Intrinsic value per unit at `level`: what an expired position is worth."""
    total = 0.0
    for leg in legs:
        strike = float(leg.get("strike_price") or 0)
        intrinsic = max(0.0, level - strike) if leg.get("right") == "call" else max(0.0, strike - level)
        total += -intrinsic if leg.get("action") == cfg.SELL else intrinsic
    return round(total, 2)


# --------------------------------------------------------------------------------------
# Entry
# --------------------------------------------------------------------------------------


def _entry_detail(plan: Plan, extra: dict[str, Any]) -> dict[str, Any]:
    return {
        "index_code": plan.index_code,
        "expiry_display": plan.expiry_display,
        "family": plan.family,
        "plan": plan.summary(),
        **extra,
    }


def open_paper(
    user_id: str, run_id: str, plan: Plan, charges: ChargesModel, extra: dict[str, Any]
) -> EntryOutcome:
    from icici_breeze_backend.app.repositories import bots as repo

    qty = plan.quantity
    fills: list[tuple[PlanLeg, float]] = []
    entry_charges = 0.0
    failed: Optional[PlanLeg] = None
    for leg in plan.entry_sequence():
        fn = simulate_sell if leg.is_short else simulate_buy
        fill = fn(leg.quote.bid, leg.quote.ask, qty, charges)
        if fill is None:
            failed = leg
            break
        fills.append((leg, fill.price))
        entry_charges += charges.leg_charges(
            fill.price, qty, is_buy=not leg.is_short, exchange_code=plan.exchange_code
        )

    detail = _entry_detail(plan, extra)
    if failed is not None:
        # Recorded, not skipped: the one-entry-per-day rule counts this attempt, and the run
        # log should show a failed attempt rather than a silent gap.
        cycle = repo.open_cycle(
            user_id, BOT_CAS_BINGO, run_id,
            structure=plan.structure, legs=plan.as_legs(), lots=plan.lots,
            entry_value=0.0, paper=True, detail={**detail, "aborted": True},
        )
        text = f"No two-sided quote on the {int(failed.strike)} {failed.right} at entry; nothing simulated."
        repo.close_cycle(
            cycle.id, exit_reason_code=ReasonCode.ENTRY_UNFILLED, exit_reason_text=text,
            gross_pnl=0.0, friction=0.0,
        )
        return EntryOutcome(False, ReasonCode.ENTRY_UNFILLED, text, cycle_id=cycle.id)

    net = round(sum(p if l.is_short else -p for l, p in fills), 2)
    detail.update(
        {
            "net_entry_per_unit": net,
            "entry_charges": round(entry_charges, 2),
            "entry_fills": [
                {"right": l.right, "strike": l.strike, "action": l.action, "price": p}
                for l, p in fills
            ],
        }
    )
    cycle = repo.open_cycle(
        user_id, BOT_CAS_BINGO, run_id,
        structure=plan.structure, legs=plan.as_legs(), lots=plan.lots,
        entry_value=round(net * qty, 2), paper=True, detail=detail,
    )
    text = f"Simulated {plan.summary()['label'].lower()} on {market.INDEX_LABEL[plan.index_code]}, {plan.lots} lot(s), net {net * qty:+,.0f}."
    _logger.info("cas bingo [SIM]: %s", text)
    return EntryOutcome(True, ReasonCode.ORDERS_PLACED, text, cycle_id=cycle.id)


def _order(plan_or_cycle_leg: dict[str, Any], *, action: str, quantity: int) -> Any:
    from icici_breeze_backend.app.services.bots.scalping import live

    return live.LegOrder(
        stock_code=str(plan_or_cycle_leg["stock_code"]),
        exchange_code=str(plan_or_cycle_leg["exchange_code"]),
        right=str(plan_or_cycle_leg["right"]),
        strike_price=float(plan_or_cycle_leg["strike_price"]),
        expiry_display=str(plan_or_cycle_leg["expiry_display"]),
        action=action,
        quantity=int(quantity),
    )


def _leg_dict(plan: Plan, leg: PlanLeg) -> dict[str, Any]:
    return {
        "stock_code": plan.index_code,
        "exchange_code": plan.exchange_code,
        "right": leg.right,
        "strike_price": leg.strike,
        "expiry_display": plan.expiry_display,
        "action": leg.action,
    }


def open_live(
    proc: Any,
    user_id: str,
    run_id: str,
    plan: Plan,
    config: CasBingoConfig,
    charges: ChargesModel,
    extra: dict[str, Any],
) -> EntryOutcome:
    from icici_breeze_backend.app.repositories import bots as repo
    from icici_breeze_backend.app.services.bots.scalping import guards, live

    if guards.has_unresolved_intent(user_id, BOT_CAS_BINGO):
        return EntryOutcome(
            False, ReasonCode.ORDER_REJECTED,
            "An earlier order is still unreconciled; not entering on top of it.",
        )

    qty = plan.quantity
    detail = _entry_detail(plan, extra)
    cycle = repo.open_cycle(
        user_id, BOT_CAS_BINGO, run_id,
        structure=plan.structure, legs=plan.as_legs(), lots=plan.lots,
        entry_value=None, paper=False, detail={**detail, "pending": True, "order_ids": []},
    )

    placed: list[tuple[PlanLeg, int, float, str]] = []  # (leg, qty filled, price, order id)
    order_ids: list[str] = []
    failure: Optional[tuple[PlanLeg, str]] = None
    tol = config.execution.entry_limit_tolerance_pct
    for leg in plan.entry_sequence():
        # Marketable limits that do not chase on retry: an entry that does not fill is a trade
        # not taken, which costs nothing (Bot 4's `_entry_ladder`).
        ladder = (
            live.exit_price_ladder(float(leg.quote.bid or 0), tol, widen=0.0)
            if leg.is_short
            else live.entry_price_ladder(float(leg.quote.ask or 0), tol)
        )
        result = live.place_and_confirm(
            proc, user_id, _order(_leg_dict(plan, leg), action=leg.action, quantity=qty),
            price_for_attempt=ladder,
            timeout_seconds=config.execution.entry_fill_timeout_seconds,
            attempts=max(1, config.execution.entry_retries),
        )
        if result.order_id:
            order_ids.append(result.order_id)
        if result.cancel_failed:
            repo.mark_cycle_placed(
                cycle.id, order_ids=order_ids,
                detail={**detail, "cancel_failed": True, "filled_legs": len(placed)},
            )
            guards.disarm_bot(user_id, BOT_CAS_BINGO, result.error or "An order could not be cancelled.")
            alert_stuck(user_id, "an order could not be cancelled mid-entry", result.error)
            return EntryOutcome(False, ReasonCode.ORDER_REJECTED, result.error or "Cancel failed.", cycle_id=cycle.id)
        if result.filled_quantity > 0:
            placed.append((leg, int(result.filled_quantity), float(result.average_price or 0.0), str(result.order_id or "")))
        if not result.ok:
            failure = (leg, result.error or "The leg did not fill.")
            break

    if failure is not None:
        return _abort_live_entry(proc, user_id, config, cycle, plan, detail, placed, order_ids, failure, charges)

    net = round(sum(p if l.is_short else -p for l, _q, p, _o in placed), 2)
    entry_charges = round(
        sum(
            charges.leg_charges(p, q, is_buy=not l.is_short, exchange_code=plan.exchange_code)
            for l, q, p, _o in placed
        ),
        2,
    )
    detail.update(
        {
            "net_entry_per_unit": net,
            "entry_charges": entry_charges,
            "entry_fills": [
                {"right": l.right, "strike": l.strike, "action": l.action, "price": p, "quantity": q, "order_id": o}
                for l, q, p, o in placed
            ],
        }
    )
    repo.mark_cycle_placed(cycle.id, order_ids=order_ids, detail=detail)
    # `entry_value` was unknown until the fills came back.
    _set_entry_value(cycle.id, round(net * qty, 2))
    text = f"Placed {plan.summary()['label'].lower()} on {market.INDEX_LABEL[plan.index_code]}, {plan.lots} lot(s), net {net * qty:+,.0f}."
    _logger.info("cas bingo [LIVE]: %s", text)
    return EntryOutcome(True, ReasonCode.ORDERS_PLACED, text, cycle_id=cycle.id)


def _set_entry_value(cycle_id: str, value: float) -> None:
    from icici_breeze_backend.app.repositories import bots as repo

    with repo._connect() as conn:
        conn.execute("UPDATE bot_cycles SET entry_value = ? WHERE id = ?", (value, cycle_id))
        conn.commit()


def _abort_live_entry(proc, user_id, config, cycle, plan, detail, placed, order_ids, failure, charges) -> EntryOutcome:
    """Unwind whatever filled -- shorts first, then longs -- and close the row as abandoned."""
    from icici_breeze_backend.app.repositories import bots as repo
    from icici_breeze_backend.app.services.bots.scalping import guards

    failed_leg, failure_text = failure
    unwound, stuck, realized, unwind_charges = [], [], 0.0, 0.0
    for leg, q, price, _oid in sorted(placed, key=lambda f: not f[0].is_short):
        result, fill_price = _close_leg(proc, user_id, config, _leg_dict(plan, leg), leg.is_short, q, None)
        if result.ok:
            unwound.append(leg)
            # A short bought back costs; a long sold returns.
            realized += (price - fill_price) * q if leg.is_short else (fill_price - price) * q
            unwind_charges += charges.leg_charges(price, q, is_buy=not leg.is_short, exchange_code=plan.exchange_code)
            unwind_charges += charges.leg_charges(fill_price, q, is_buy=leg.is_short, exchange_code=plan.exchange_code)
        else:
            stuck.append((leg, q))

    detail = {
        **detail,
        "aborted": True,
        "failed_leg": {"right": failed_leg.right, "strike": failed_leg.strike, "action": failed_leg.action},
        "failure": failure_text,
        "unwound_legs": len(unwound),
        "stuck_legs": [{"right": l.right, "strike": l.strike, "quantity": q} for l, q in stuck],
    }
    if stuck:
        repo.mark_cycle_placed(cycle.id, order_ids=order_ids, detail=detail)
        guards.disarm_bot(user_id, BOT_CAS_BINGO, "An entry could not be unwound cleanly.")
        alert_stuck(
            user_id, "an entry failed and could not be fully unwound",
            "; ".join(f"{l.right} {int(l.strike)} x{q} still open" for l, q in stuck),
        )
        return EntryOutcome(False, ReasonCode.ORDER_REJECTED, failure_text, cycle_id=cycle.id)

    code = ReasonCode.ENTRY_PARTIAL_UNWOUND if placed else ReasonCode.ENTRY_UNFILLED
    text = (
        f"The {int(failed_leg.strike)} {failed_leg.right} {failed_leg.action.lower()} did not fill "
        f"({failure_text}); the {len(unwound)} leg(s) that had filled were unwound."
        if placed
        else f"The first leg did not fill ({failure_text}); nothing was traded."
    )
    repo.mark_cycle_placed(cycle.id, order_ids=order_ids, detail=detail)
    repo.close_cycle(
        cycle.id, exit_reason_code=code, exit_reason_text=text,
        gross_pnl=round(realized, 2), friction=round(unwind_charges, 2), detail=detail,
    )
    _logger.warning("cas bingo [LIVE]: %s", text)
    return EntryOutcome(False, code, text, cycle_id=cycle.id)


def _close_leg(proc, user_id, config, leg: dict[str, Any], is_short: bool, quantity: int, quote: Optional[Quote]):
    """Buy a short back at a widening ask ladder, or sell a long down a widening bid one."""
    from icici_breeze_backend.app.services.bots.scalping import live

    if quote is None:
        quote = market.live_quote(
            proc, user_id, str(leg["stock_code"]), str(leg["expiry_display"]),
            float(leg["strike_price"]), str(leg["right"]),
        )
    band = config.execution.exit_limit_band_pct
    if is_short:
        action, ladder = cfg.BUY, live.buyback_price_ladder(float(quote.ask or 0.05), band)
    else:
        action, ladder = cfg.SELL, live.exit_price_ladder(float(quote.bid or 0.05), band)
    result = live.place_and_confirm(
        proc, user_id, _order(leg, action=action, quantity=quantity),
        price_for_attempt=ladder,
        timeout_seconds=config.execution.entry_fill_timeout_seconds,
        attempts=_EXIT_ATTEMPTS,
    )
    return result, float(result.average_price or 0.0)


# --------------------------------------------------------------------------------------
# Exit and settlement
# --------------------------------------------------------------------------------------


def close_paper(cycle: Any, quotes: dict[tuple[str, float], Quote], verdict: tuple[str, str], charges: ChargesModel) -> None:
    from icici_breeze_backend.app.repositories import bots as repo

    legs = cycle.legs or []
    detail = dict(cycle.detail or {})
    qty = int((legs[0] or {}).get("quantity") or 0) if legs else 0
    close_value = close_value_per_unit(legs, quotes)
    if close_value is None or qty <= 0:
        _logger.warning("cas bingo [SIM]: cannot price the exit of cycle %s; leaving it open", cycle.id)
        return
    exchange = str((legs[0] or {}).get("exchange_code") or cfg.NFO)
    exit_charges = 0.0
    for leg in legs:
        quote = quotes.get(_key(leg)) or Quote(None, None, None)
        is_buy = leg.get("action") == cfg.SELL  # closing reverses each leg
        price = (quote.ask if is_buy else quote.bid) or 0.0
        exit_charges += charges.leg_charges(price, qty, is_buy=is_buy, exchange_code=exchange)
    net_entry = float(detail.get("net_entry_per_unit") or 0)
    detail["exit"] = {"close_value_per_unit": close_value, "charges": round(exit_charges, 2)}
    repo.close_cycle(
        cycle.id,
        exit_reason_code=verdict[0], exit_reason_text=verdict[1],
        exit_value=round(close_value * qty, 2),
        gross_pnl=round((net_entry + close_value) * qty, 2),
        friction=round(float(detail.get("entry_charges") or 0) + exit_charges, 2),
        detail=detail,
    )


def close_live(
    proc: Any, user_id: str, config: CasBingoConfig, cycle: Any,
    quotes: dict[tuple[str, float], Quote], verdict: tuple[str, str], charges: ChargesModel,
) -> None:
    """Shorts bought back first, then longs sold -- selling the hedge first would leave a naked
    short for the life of one order."""
    from icici_breeze_backend.app.repositories import bots as repo
    from icici_breeze_backend.app.services.bots.scalping import guards

    legs = cycle.legs or []
    detail = dict(cycle.detail or {})
    exchange = str((legs[0] or {}).get("exchange_code") or cfg.NFO)
    closed, stuck = [], []
    close_value = 0.0
    exit_charges = 0.0
    for leg in sorted(legs, key=lambda l: l.get("action") != cfg.SELL):
        qty = int(leg.get("quantity") or 0)
        if qty <= 0:
            continue
        is_short = leg.get("action") == cfg.SELL
        result, price = _close_leg(proc, user_id, config, leg, is_short, qty, quotes.get(_key(leg)))
        if not result.ok:
            stuck.append({"right": leg.get("right"), "strike": leg.get("strike_price"), "quantity": qty, "error": result.error})
            continue
        close_value += -price if is_short else price
        exit_charges += charges.leg_charges(price, qty, is_buy=is_short, exchange_code=exchange)
        closed.append({"right": leg.get("right"), "strike": leg.get("strike_price"), "price": price, "order_id": result.order_id})

    if stuck:
        detail["exit_partial"] = {"closed": closed, "stuck": stuck}
        repo.update_cycle_detail(cycle.id, detail)
        guards.disarm_bot(user_id, BOT_CAS_BINGO, "A position could not be fully closed.")
        alert_stuck(
            user_id, "a position could not be fully closed",
            "; ".join(f"{s['right']} {int(float(s['strike']))} x{s['quantity']}" for s in stuck),
        )
        return

    qty = int((legs[0] or {}).get("quantity") or 0)
    net_entry = float(detail.get("net_entry_per_unit") or 0)
    detail["exit"] = {"close_value_per_unit": round(close_value, 2), "charges": round(exit_charges, 2), "legs": closed}
    repo.close_cycle(
        cycle.id,
        exit_reason_code=verdict[0], exit_reason_text=verdict[1],
        exit_value=round(close_value * qty, 2),
        gross_pnl=round((net_entry + close_value) * qty, 2),
        friction=round(float(detail.get("entry_charges") or 0) + exit_charges, 2),
        detail=detail,
    )


def settle(cycle: Any, level: float) -> None:
    """Close an expired position at intrinsic value against `level`, flagged as an estimate:
    the exchange publishes the official settlement price after the auction, and nothing here
    trades. No exit charges -- nothing was sold or bought back."""
    from icici_breeze_backend.app.repositories import bots as repo

    legs = cycle.legs or []
    detail = dict(cycle.detail or {})
    qty = int((legs[0] or {}).get("quantity") or 0) if legs else 0
    value = settle_value_per_unit(legs, level)
    net_entry = float(detail.get("net_entry_per_unit") or 0)
    detail["exit"] = {"settled_at_level": level, "close_value_per_unit": value, "estimate": True}
    repo.close_cycle(
        cycle.id,
        exit_reason_code=ReasonCode.EXPIRED_SETTLED,
        exit_reason_text=(
            f"Expired with neither exit hit. Estimated at intrinsic value against the index at "
            f"{level:,.2f}; the official settlement price may differ."
        ),
        exit_value=round(value * qty, 2),
        gross_pnl=round((net_entry + value) * qty, 2),
        friction=round(float(detail.get("entry_charges") or 0), 2),
        detail=detail,
    )


# --------------------------------------------------------------------------------------
# Liquidation and the shared entry pipeline
# --------------------------------------------------------------------------------------


def execute_buybacks(
    proc: Any, user_id: str, *, index_code: str, expiry_display: str,
    buybacks: list[liq.BuyBack], config: CasBingoConfig,
) -> list[dict[str, Any]]:
    """Buy each planned short back, never above its cap. Serialized, one at a time (#24).
    A partial is kept: margin was released for the units that did fill."""
    from icici_breeze_backend.app.services.bots.scalping import live

    out: list[dict[str, Any]] = []
    band = config.execution.exit_limit_band_pct
    for b in buybacks:
        ladder = live.buyback_price_ladder(b.ask, band)
        cap = float(b.cap_price)
        result = live.place_and_confirm(
            proc, user_id,
            live.LegOrder(
                stock_code=index_code, exchange_code=market.INDEX_EXCHANGE[index_code],
                right=b.right, strike_price=b.strike, expiry_display=expiry_display,
                action=cfg.BUY, quantity=b.quantity,
            ),
            # The cap is what makes a liquidation profitable by construction: the ladder may
            # step up toward it, never past it.
            price_for_attempt=lambda attempt, _l=ladder, _c=cap: min(_c, _l(attempt)),
            timeout_seconds=config.execution.entry_fill_timeout_seconds,
            attempts=max(1, config.execution.entry_retries),
        )
        out.append(
            {
                **b.summary(),
                "filled_quantity": int(result.filled_quantity),
                "fill_price": result.average_price,
                "order_id": result.order_id,
                "error": result.error,
            }
        )
        if result.cancel_failed:
            break
    return out


def _available(proc: Any, user_id: str) -> Optional[float]:
    from icici_breeze_backend.app.services.bots.expiry_index_writer import _available_margin

    return _available_margin(proc, user_id)


def enter(
    proc: Any,
    user_id: str,
    config: CasBingoConfig,
    run_id: str,
    plan: Plan,
    *,
    live: bool,
    charges: ChargesModel,
    extra: dict[str, Any],
) -> EntryOutcome:
    """Margin check, liquidation if short, then the entry. Shared by the loop and the manual
    sheet so the two cannot drift."""
    available = _available(proc, user_id)
    if available is None:
        return EntryOutcome(
            False, ReasonCode.MARGIN_LOOKUP_FAILED, "Could not read available margin from the broker.",
            terminal=False,
        )
    required = float(plan.margin_required)
    liquidation_summary: Optional[dict[str, Any]] = None
    shortfall = required - available
    if shortfall > 0:
        if not config.liquidation.enabled:
            return EntryOutcome(
                False, ReasonCode.MARGIN_INSUFFICIENT,
                f"Needs {required:,.0f}, {available:,.0f} free, and liquidation is switched off.",
            )
        common = dict(
            index_code=plan.index_code, expiry_display=plan.expiry_display,
            min_captured_pct=config.liquidation.min_captured_pct,
            safety_buffer_pct=config.liquidation.safety_buffer_pct,
            lot_size=plan.lot_size, spot=plan.spot,
        )
        lplan = liq.plan_for(proc, user_id, shortfall=shortfall, **common)
        liquidation_summary = {"plan": lplan.summary(), "rounds": []}
        if not lplan.covered:
            return EntryOutcome(
                False, ReasonCode.LIQUIDATION_INSUFFICIENT,
                f"Needs {required:,.0f}, {available:,.0f} free. {lplan.note}",
                liquidation=liquidation_summary,
            )
        if live:
            buybacks = lplan.buybacks
            for round_no in range(1 + _MAX_EXTRA_LIQUIDATION_ROUNDS):
                results = execute_buybacks(
                    proc, user_id, index_code=plan.index_code, expiry_display=plan.expiry_display,
                    buybacks=buybacks, config=config,
                )
                liquidation_summary["rounds"].append(results)
                available = _available(proc, user_id)
                if available is not None and available >= required:
                    break
                if available is None or round_no == _MAX_EXTRA_LIQUIDATION_ROUNDS:
                    return EntryOutcome(
                        False, ReasonCode.LIQUIDATION_INSUFFICIENT,
                        f"Bought back {sum(len(r) for r in liquidation_summary['rounds'])} short(s) "
                        f"but free margin is still {available or 0:,.0f} against {required:,.0f}. "
                        f"The buy-backs stand; each was profitable.",
                        liquidation=liquidation_summary,
                    )
                again = liq.plan_for(proc, user_id, shortfall=required - available, **common)
                if not again.buybacks:
                    return EntryOutcome(
                        False, ReasonCode.LIQUIDATION_INSUFFICIENT,
                        f"Free margin is {available:,.0f} against {required:,.0f} and no eligible "
                        f"short is left. The buy-backs that filled stand.",
                        liquidation=liquidation_summary,
                    )
                buybacks = again.buybacks
        extra = {**extra, "liquidation": liquidation_summary}

    charges_extra = {**extra, "margin_available": available, "margin_required": required}
    if live:
        outcome = open_live(proc, user_id, run_id, plan, config, charges, charges_extra)
    else:
        outcome = open_paper(user_id, run_id, plan, charges, charges_extra)
    outcome.liquidation = liquidation_summary
    return outcome


def alert_stuck(user_id: str, what: str, detail: Optional[str]) -> None:
    from icici_breeze_backend.app.services.telegram_alerts import _notify

    try:
        _notify(
            user_id,
            "\U0001f6d1 <b>CAS Bingo needs checking</b>\n\n"
            f"The bot stopped because {what}.\n\n{detail or ''}\n\n"
            "<b>Check the Order Book for open legs.</b> The bot has been disarmed and will not "
            "open anything new.",
            kind="cas_bingo_stuck",
        )
    except Exception:  # noqa: BLE001 -- an alert failure must not mask the original problem
        _logger.exception("cas bingo: could not send the stuck-position alert")
