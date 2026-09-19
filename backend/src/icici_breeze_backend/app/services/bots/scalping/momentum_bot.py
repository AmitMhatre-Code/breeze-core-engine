"""Bot 3 -- the momentum long scalper's bot-specific layer (plan section 3).

The shared gate stack in `decide.py` says *whether* to act. This module says *what*: which
contract, how many lots, and -- once a position is open -- what the ladder makes of the
current bid. It owns no timing and no risk policy of its own.

Quotes are read cache-first through `quote_source_router`, which serves them from the
WebSocket during market hours and subscribes the contract on first ask. That matters more
here than anywhere else in the app: the ladder needs a fresh bid every pass (~5s at the
default PB/SL cadence), and a REST poll at that rate would spend the whole account's minute
budget on one position.
"""
from __future__ import annotations

import datetime
import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.strike import parse_strike
from icici_breeze_backend.app.core.timezone import now_ist
from icici_breeze_backend.app.domain.bots import (
    MOMENTUM_ENTRY_SIGNAL,
    MomentumLongScalperConfig,
    ReasonCode,
)
from icici_breeze_backend.app.services.bots.scalping import ladder as ladder_mod
from icici_breeze_backend.app.services.bots.scalping import live
from icici_breeze_backend.app.services.bots.scalping import spreads as spreads_mod
from icici_breeze_backend.app.services.bots.charges import ChargesModel
from icici_breeze_backend.app.services.bots.scalping.paper import (
    SimulatedFill,
    round_trip_pnl,
    simulate_buy,
    simulate_sell,
)

_logger = logging.getLogger(__name__)

INDEX_STOCK_CODE = "NIFTY"
INDEX_EXCHANGE = cfg.NFO


@dataclass(frozen=True)
class Quote:
    bid: Optional[float]
    ask: Optional[float]
    ltp: Optional[float]

    @property
    def priceable(self) -> bool:
        return bool(self.bid and self.bid > 0 and self.ask and self.ask > 0)


@dataclass(frozen=True)
class EntryPlan:
    expiry_display: str
    strike_price: float
    right: str          # "call" | "put"
    quote: Quote
    lot_size: int
    lots: int

    @property
    def quantity(self) -> int:
        return self.lot_size * self.lots

    def as_leg(self) -> dict[str, Any]:
        return {
            "stock_code": INDEX_STOCK_CODE,
            "exchange_code": INDEX_EXCHANGE,
            "right": self.right,
            "strike_price": self.strike_price,
            "expiry_display": self.expiry_display,
            "lots": self.lots,
            "lot_size": self.lot_size,
            "quantity": self.quantity,
            "action": cfg.BUY,
        }


def _f(raw: Any) -> Optional[float]:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def option_expiries(proc: Any) -> list[str]:
    """Every NIFTY option expiry the scrip master knows, as DD-MMM-YYYY.

    Shared with the futures feed, which has no scrip master of its own: local reference data
    holds only CE/PE rows, so `futures_feed.monthly_expiries` derives the futures calendar
    from this options list rather than from a futures universe that is not there.
    """
    from icici_breeze_backend.app.services.reference_data.scrip_master_sql import (
        _expiry_api_to_display,
    )

    try:
        universe = proc.fetch_stock_codes(INDEX_EXCHANGE) or []
    except Exception:  # noqa: BLE001
        _logger.warning("momentum bot: could not read the %s universe", INDEX_EXCHANGE, exc_info=True)
        return []
    out: list[str] = []
    seen: set[str] = set()
    for entry in universe:
        if str(entry.get("stock_code") or "").strip().upper() != INDEX_STOCK_CODE:
            continue
        for raw in entry.get("expiry_dates") or []:
            try:
                display = _expiry_api_to_display(str(raw))
                datetime.datetime.strptime(display, "%d-%b-%Y")
            except (ValueError, TypeError):
                continue
            if display not in seen:
                seen.add(display)
                out.append(display)
    return out


def nearest_expiry(proc: Any, *, today: Optional[datetime.date] = None) -> Optional[str]:
    """The nearest NIFTY option expiry on or after today, as DD-MMM-YYYY.

    Read from the scrip master, never a weekday rule -- SEBI has moved expiry days before,
    which is the same reason `bots/scheduler._expiring_today` reads it too.
    """
    today = today or now_ist().date()
    best: Optional[datetime.date] = None
    for display in option_expiries(proc):
        parsed = datetime.datetime.strptime(display, "%d-%b-%Y").date()
        if parsed >= today and (best is None or parsed < best):
            best = parsed
    return best.strftime("%d-%b-%Y") if best else None


def _chain_rows(proc: Any, user_id: str, expiry_display: str, right: str) -> list[dict[str, Any]]:
    from icici_breeze_backend.app.services.quote_source_router import (
        fetch_chain_side_icici_response,
    )

    chain = fetch_chain_side_icici_response(
        proc, user_id, INDEX_STOCK_CODE, INDEX_EXCHANGE, expiry_display, right
    )
    if (chain or {}).get("Status") != 200 or not chain.get("Success"):
        return []
    return [r for r in chain["Success"] if isinstance(r, dict)]


def atm_strike(rows: list[dict[str, Any]], spot: float) -> Optional[float]:
    """The listed strike closest to spot, ties going to the lower strike.

    Nearest-to-spot rather than a rounding rule, because the strike ladder is what the
    exchange lists, not what arithmetic implies -- and a strike that is absent from the chain
    cannot be traded however neatly it rounds.
    """
    if spot <= 0:
        return None
    best, best_distance = None, float("inf")
    for row in rows:
        strike = parse_strike(row.get("strike_price"))
        if strike is None:
            continue
        s = float(strike)
        distance = abs(s - spot)
        if distance < best_distance or (distance == best_distance and best is not None and s < best):
            best, best_distance = s, distance
    return best


def _spot_from(rows: list[dict[str, Any]]) -> float:
    for row in rows:
        spot = _f(row.get("spot_price"))
        if spot:
            return spot
    return 0.0


def _row_quote(row: dict[str, Any]) -> Quote:
    return Quote(
        bid=_f(row.get("best_bid_price")),
        ask=_f(row.get("best_offer_price")),
        ltp=_f(row.get("ltp")),
    )


def live_quote(
    proc: Any, user_id: str, expiry_display: str, strike_price: float, right: str
) -> Quote:
    """Cache-first single-contract quote. Serves from the WS feed during market hours."""
    from icici_breeze_backend.app.services.quote_source_router import (
        fetch_quote_icici_response,
    )

    response = fetch_quote_icici_response(
        proc, user_id, INDEX_STOCK_CODE, INDEX_EXCHANGE, expiry_display, right, strike_price
    )
    rows = (response or {}).get("Success") or []
    if (response or {}).get("Status") != 200 or not rows:
        return Quote(None, None, None)
    return _row_quote(rows[0])


def plan_entry(
    proc: Any,
    user_id: str,
    config: MomentumLongScalperConfig,
    right: str,
) -> tuple[Optional[EntryPlan], Optional[tuple[str, str]]]:
    """Pick the contract and size it. Returns (plan, None) or (None, (code, text)).

    Sized off the **ask** -- what the position would actually cost -- so the outlay is a real
    ceiling rather than one computed against a price nobody can buy at.
    """
    expiry = nearest_expiry(proc)
    if not expiry:
        return None, (ReasonCode.CHAIN_NOT_READY, "No NIFTY expiry available from the scrip master.")

    rows = _chain_rows(proc, user_id, expiry, right)
    if not rows:
        return None, (ReasonCode.CHAIN_NOT_READY, f"No {right} chain for {expiry} yet.")

    spot = _spot_from(rows)
    strike = atm_strike(rows, spot)
    if strike is None:
        return None, (ReasonCode.QUOTE_UNAVAILABLE, "Could not resolve an ATM strike from the chain.")

    row = next(
        (r for r in rows if (parse_strike(r.get("strike_price")) or -1) == strike), None
    )
    quote = _row_quote(row) if row else Quote(None, None, None)
    if not quote.priceable:
        return None, (
            ReasonCode.QUOTE_UNAVAILABLE,
            f"No two-sided quote on the {int(strike)} {right} yet.",
        )

    # Feed the backtest's spread model from what this deployment actually sees on the
    # contract it actually trades (docs/bots-scalping-plan.md section 8). Throttled inside.
    spreads_mod.record_spread_sample(
        INDEX_STOCK_CODE, expiry, float(strike), right, quote.bid, quote.ask
    )

    try:
        lot_size = int(proc.fetch_lot_size(INDEX_STOCK_CODE, expiry, exchange_code=INDEX_EXCHANGE) or 0)
    except Exception:  # noqa: BLE001
        lot_size = 0
    if lot_size <= 0:
        return None, (ReasonCode.CHAIN_NOT_READY, "Lot size unavailable from the scrip master.")

    cost_per_lot = float(quote.ask) * lot_size
    lots = int(config.premium_outlay_inr // cost_per_lot)
    if lots < 1:
        return None, (
            ReasonCode.OUTLAY_BELOW_ONE_LOT,
            f"One lot costs {cost_per_lot:,.0f} at {quote.ask:.2f}, above the "
            f"{config.premium_outlay_inr:,.0f} outlay.",
        )

    return (
        EntryPlan(
            expiry_display=expiry,
            strike_price=float(strike),
            right=right,
            quote=quote,
            lot_size=lot_size,
            lots=lots,
        ),
        None,
    )


def paper_entry(plan: EntryPlan, charges: ChargesModel) -> Optional[SimulatedFill]:
    return simulate_buy(plan.quote.bid, plan.quote.ask, plan.quantity, charges)


def paper_exit(quote: Quote, quantity: int, charges: ChargesModel) -> Optional[SimulatedFill]:
    return simulate_sell(quote.bid, quote.ask, quantity, charges)


def risk_per_stop(plan: EntryPlan, stop_loss_pts: float) -> float:
    """Rupees lost if this position stops out at its initial stop.

    Recorded on every cycle because the sizing model makes it *vary*: lots are
    floor(outlay / cost) and an ATM option cheapens towards expiry, so the same outlay buys
    roughly 1 lot at 6 days out and 4 at 1 day out. That puts the largest positions where
    gamma is highest, which is a deliberate, accepted trade-off (see `premium_outlay_inr`) --
    but only if it is visible rather than inferred.
    """
    return round(float(stop_loss_pts) * plan.quantity, 2)


def uses_variant(config: MomentumLongScalperConfig) -> bool:
    """True when the bot trades a signal variant (#38) rather than its own momentum signal."""
    return str(getattr(config, "entry_signal", MOMENTUM_ENTRY_SIGNAL)) != MOMENTUM_ENTRY_SIGNAL


def current_signal(
    config: MomentumLongScalperConfig, candles: list, session_vwap: Optional[float]
) -> Any:
    """The entry verdict from whichever signal the bot is set to. The one place that choice is
    made, so the run row, the executor and the live path can never read different signals."""
    from icici_breeze_backend.app.services.bots.scalping.signal import (
        evaluate_momentum,
        evaluate_variant,
    )

    if not uses_variant(config):
        return evaluate_momentum(candles, session_vwap, config.signal)
    from icici_breeze_backend.app.services.index_signal.reader import get_variant_signal

    return evaluate_variant(get_variant_signal(config.entry_signal), config.entry_signal)


def hold_seconds_for(signal: Any) -> Optional[float]:
    """The variant's hold, stamped on the cycle at entry -- so a later change to the bot's
    signal, or the variant's deletion, never changes how an open trade exits."""
    values = getattr(signal, "values", None) or {}
    if values.get("source") != "variant":
        return None
    try:
        minutes = float(values.get("hold_minutes"))
    except (TypeError, ValueError):
        return None
    return minutes * 60.0 if minutes > 0 else None


def cycle_detail(
    plan: EntryPlan, fill: SimulatedFill, state: ladder_mod.LadderState, charges: ChargesModel
) -> dict[str, Any]:
    """What gets persisted with the cycle: enough to resume it and to explain it."""
    return {
        "ladder": state.to_detail(),
        "risk_per_stop_inr": round(fill.price - state.stop_price, 2) * plan.quantity,
        "entry": {
            "price": fill.price,
            "touch_price": fill.touch_price,
            "slippage_per_unit": fill.slippage_per_unit,
            "bid": plan.quote.bid,
            "ask": plan.quote.ask,
            "charges": charges.breakdown(fill.price, fill.quantity, is_buy=True),
        },
    }


def manage_position(
    proc: Any,
    user_id: str,
    config: MomentumLongScalperConfig,
    cycle: Any,
) -> tuple[Optional[ladder_mod.LadderState], bool, Optional[tuple[str, str]], Optional[Quote]]:
    """Advance the ladder against a live bid.

    Returns (state, stop_moved, exit_decision, quote). A None state means the cycle carries
    no resumable ladder -- the caller decides what to do about that rather than this function
    inventing one, because guessing a stop for a real position is worse than saying so.
    """
    leg = (cycle.legs or [{}])[0]
    quote = live_quote(
        proc,
        user_id,
        str(leg.get("expiry_display") or ""),
        float(leg.get("strike_price") or 0),
        str(leg.get("right") or "call"),
    )
    spreads_mod.sample_from_quote(leg, quote.bid, quote.ask)
    state = ladder_mod.LadderState.from_detail((cycle.detail or {}).get("ladder"))
    if state is None:
        return None, False, None, quote
    if quote.bid is None or quote.bid <= 0:
        # No bid means the ladder cannot be advanced this pass. Holding is correct: the
        # shared stale-feed gate is what escalates a feed that stays dark.
        return state, False, None, quote

    state, moved = ladder_mod.advance(state, quote.bid, config.exits)
    verdict = ladder_mod.exit_decision(
        state, quote.bid, time.time(), config.exits,
        hold_seconds=_stored_hold_seconds(cycle),
    )
    return state, moved, verdict, quote


def _stored_hold_seconds(cycle: Any) -> Optional[float]:
    raw = (cycle.detail or {}).get("hold_seconds")
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def close_paper_cycle(
    cycle: Any, quote: Quote, charges: ChargesModel
) -> tuple[Optional[SimulatedFill], Optional[tuple[float, float, float]]]:
    """Simulate the sell and price the round trip. (fill, (gross, friction, net))."""
    entry_price = float(((cycle.detail or {}).get("entry") or {}).get("price") or 0)
    quantity = int((cycle.legs or [{}])[0].get("quantity") or 0)
    if entry_price <= 0 or quantity <= 0:
        return None, None
    fill = paper_exit(quote, quantity, charges)
    if fill is None:
        return None, None
    entry_fill = SimulatedFill(
        price=entry_price,
        quantity=quantity,
        charges=float(((cycle.detail or {}).get("entry") or {}).get("charges", {}).get("total") or 0),
        slippage_per_unit=0.0,
        touch_price=entry_price,
    )
    return fill, round_trip_pnl(entry_fill, fill)


# --------------------------------------------------------------------------------------
# Orchestration -- called by the driver, which owns the clock and the gate stack
# --------------------------------------------------------------------------------------


@dataclass
class PositionContext:
    """What one pass learned about an open position, so it is fetched once, not twice."""

    cycle: Any
    state: Optional[ladder_mod.LadderState]
    stop_moved: bool
    verdict: Optional[tuple[str, str]]
    quote: Quote


def inspect_position(
    proc: Any, user_id: str, config: MomentumLongScalperConfig, bot_type: str
) -> Optional[PositionContext]:
    """Advance the ladder for the open cycle, if there is one.

    Runs *before* the gate stack so the ladder's verdict can be handed to it as one input
    among several -- the stack still decides whether an obligation outranks it.
    """
    from icici_breeze_backend.app.repositories import bots as repo

    open_cycles = repo.open_cycles(user_id, bot_type)
    if not open_cycles:
        return None
    cycle = open_cycles[0]
    state, moved, verdict, quote = manage_position(proc, user_id, config, cycle)
    return PositionContext(cycle=cycle, state=state, stop_moved=moved, verdict=verdict, quote=quote)


def _persist_ladder(cycle: Any, state: ladder_mod.LadderState) -> None:
    """Store the ladder only when its stop has moved -- see `ladder.advance`."""
    from icici_breeze_backend.app.repositories import bots as repo

    detail = dict(cycle.detail or {})
    detail["ladder"] = state.to_detail()
    repo.update_cycle_detail(cycle.id, detail)
    cycle.detail = detail


def execute(
    proc: Any,
    user_id: str,
    bot_type: str,
    config: MomentumLongScalperConfig,
    run_id: str,
    decision: Any,
    context: Optional[PositionContext],
    charges: ChargesModel,
    candles: list,
    session_vwap: Optional[float],
    signal: Any = None,
) -> None:
    """Carry out one decision.

    `signal` is the result the driver already computed for `decide` (see
    `runtime._entry_signal`). Passing it in keeps the executor acting on the *same* verdict
    the run row recorded -- re-evaluating here could read a candle list a tick newer than
    the one the decision was made against. It stays optional so the live path and the tests
    that call this directly can let it evaluate its own.
    """
    from icici_breeze_backend.app.repositories import bots as repo

    if context is not None and context.state is not None and context.stop_moved:
        # Persist before acting on anything else: a crash between the ratchet and the exit
        # must not lose the stop the position had already earned.
        _persist_ladder(context.cycle, context.state)

    # **An open position is managed the way it was opened, not the way the bot is set now.**
    #
    # Routing this on `config.mode` alone was a latent way to strand a real position: a user
    # who moved a holding bot from Live back to Paper would send its exit to
    # `close_paper_cycle`, which marks the cycle closed at a simulated price while the actual
    # position sits at the exchange with nothing managing it. The same hole opens on the
    # exit-only tick (`entries_suspended`), where the bot is switched off entirely and its
    # config reads `paper` by then.
    #
    # The cycle's own `paper` flag is the durable fact about what was actually placed, so it
    # is what decides. Only an *entry* -- where there is no position yet to contradict -- may
    # be routed by the config.
    live_path = context.cycle.paper is False if context is not None else config.mode == "live"

    if live_path:
        _execute_live(proc, user_id, bot_type, config, run_id, decision, context, charges,
                      candles, session_vwap, signal=signal)
        return

    if decision.action == "exit" and context is not None:
        _close(repo, context, decision, charges)
        return

    if decision.action == "enter":
        if signal is None:
            signal = current_signal(config, candles, session_vwap)
        if not signal.fired:
            # Reached only when this evaluated its own signal: when the driver supplies one,
            # `decide` has already turned a no-fire into an `idle` verdict.
            _logger.debug("momentum bot: no signal (%s)", signal.reason)
            return
        _open(proc, repo, user_id, bot_type, config, run_id, signal, charges)


def _close(repo: Any, context: PositionContext, decision: Any, charges: ChargesModel) -> None:
    fill, pnl = close_paper_cycle(context.cycle, context.quote, charges)
    if fill is None or pnl is None:
        # No bid to sell into. Say so rather than closing the row at a made-up price: an
        # unclosable position is a real state and the run log has to show it.
        _logger.warning(
            "momentum bot: cannot price an exit for cycle %s; leaving it open",
            context.cycle.id,
        )
        return
    gross, friction, net = pnl
    detail = dict(context.cycle.detail or {})
    detail["exit"] = {
        "price": fill.price,
        "touch_price": fill.touch_price,
        "slippage_per_unit": fill.slippage_per_unit,
        "charges": charges.breakdown(fill.price, fill.quantity, is_buy=False),
    }
    repo.close_cycle(
        context.cycle.id,
        exit_reason_code=decision.reason_code,
        exit_reason_text=decision.reason_text,
        exit_value=round(fill.value, 2),
        gross_pnl=gross,
        friction=friction,
        detail=detail,
    )
    _logger.info(
        "momentum bot: closed cycle %s -- %s, gross %+.2f, friction %.2f, net %+.2f",
        context.cycle.cycle_no,
        decision.reason_code,
        gross,
        friction,
        net,
    )


def _open(
    proc: Any,
    repo: Any,
    user_id: str,
    bot_type: str,
    config: MomentumLongScalperConfig,
    run_id: str,
    signal: Any,
    charges: ChargesModel,
) -> None:
    plan, problem = plan_entry(proc, user_id, config, signal.right)
    if plan is None:
        code, text = problem or (ReasonCode.INTERNAL_ERROR, "Entry could not be planned.")
        _logger.info("momentum bot: entry skipped -- %s: %s", code, text)
        return

    fill = paper_entry(plan, charges)
    if fill is None:
        _logger.info("momentum bot: entry unpriceable at the touch; skipping")
        return

    state = ladder_mod.open_ladder(fill.price, time.time(), config.exits)
    cycle = repo.open_cycle(
        user_id,
        bot_type,
        run_id,
        structure=f"long_{'ce' if plan.right == 'call' else 'pe'}",
        legs=[plan.as_leg()],
        lots=plan.lots,
        entry_value=round(fill.value, 2),
        paper=True,
        detail={
            **cycle_detail(plan, fill, state, charges),
            "signal": signal.values,
            "hold_seconds": hold_seconds_for(signal),
        },
    )
    _logger.info(
        "momentum bot: opened cycle %s -- %s %d %s x%d lots @ %.2f (stop %.2f)",
        cycle.cycle_no,
        plan.right,
        int(plan.strike_price),
        plan.expiry_display,
        plan.lots,
        fill.price,
        state.stop_price,
    )


# --------------------------------------------------------------------------------------
# Live dispatch. The gate is no longer a constant in this file: a scalper reaches `live`
# only by completing a paper trading day on the settings it will trade with
# (`scalping/evidence.py`), enforced server-side in the PATCH path, and then by the user
# confirming the dialog that shows what that day actually did.
# --------------------------------------------------------------------------------------


def _execute_live(
    proc: Any,
    user_id: str,
    bot_type: str,
    config: MomentumLongScalperConfig,
    run_id: str,
    decision: Any,
    context: Optional["PositionContext"],
    charges: ChargesModel,
    candles: list,
    session_vwap: Optional[float],
    signal: Any = None,
) -> None:
    from icici_breeze_backend.app.repositories import bots as repo
    from icici_breeze_backend.app.services.bots.scalping import guards

    if context is not None and context.state is not None and context.stop_moved:
        _persist_ladder(context.cycle, context.state)

    if decision.action == "exit" and context is not None:
        _close_live(proc, user_id, config, context, decision, charges)
        return

    if decision.action != "enter":
        return

    # An unresolved intent means an order may exist that nothing can account for. Opening a
    # second position on top of that is how one crash becomes two live positions.
    if guards.has_unresolved_intent(user_id, bot_type):
        _logger.warning("momentum bot: an unreconciled order is outstanding; not entering")
        return

    if signal is None:
        signal = current_signal(config, candles, session_vwap)
    if not signal.fired:
        return
    plan, problem = plan_entry(proc, user_id, config, signal.right or "call")
    if plan is None:
        code, text = problem or (ReasonCode.INTERNAL_ERROR, "Entry could not be planned.")
        _logger.info("momentum bot: entry skipped -- %s: %s", code, text)
        return

    # The row goes in BEFORE the order does. A crash in between then leaves a question that
    # `guards.reconcile_pending_cycles` can answer, rather than a silent live position.
    cycle = repo.open_cycle(
        user_id, bot_type, run_id,
        structure=f"long_{'ce' if plan.right == 'call' else 'pe'}",
        legs=[plan.as_leg()], lots=plan.lots, entry_value=None, paper=False,
        detail={"pending": True, "signal": signal.values,
                "hold_seconds": hold_seconds_for(signal),
                "intended_price": plan.quote.ask, "order_ids": []},
    )

    result = live.place_and_confirm(
        proc,
        user_id,
        live.LegOrder(
            stock_code=INDEX_STOCK_CODE, exchange_code=INDEX_EXCHANGE, right=plan.right,
            strike_price=plan.strike_price, expiry_display=plan.expiry_display,
            action=cfg.BUY, quantity=plan.quantity,
        ),
        price_for_attempt=live.entry_price_ladder(
            float(plan.quote.ask or 0), config.execution.entry_limit_tolerance_pct
        ),
        timeout_seconds=config.execution.entry_fill_timeout_seconds,
        attempts=max(1, config.execution.entry_retries),
    )

    if result.cancel_failed:
        # Standing down rather than tidying up: an order believed dead that is not will fill
        # into a position nothing is managing.
        repo.mark_cycle_placed(
            cycle.id, order_ids=[result.order_id or ""], detail={"cancel_failed": True}
        )
        guards.disarm_bot(user_id, bot_type, result.error or "An order could not be cancelled.")
        return

    if not result.ok and not result.partial:
        repo.abandon_cycle(
            cycle.id,
            reason_code=ReasonCode.ENTRY_UNFILLED,
            reason_text=result.error or "The entry limit did not fill.",
        )
        return

    if result.partial:
        # A cancelled partial is a real position, just a smaller one. Adopting it at the
        # filled size is the only option that neither strands it nor pretends the rest
        # exists -- and the ladder works identically on 25 units as on 75.
        _logger.warning(
            "momentum bot [LIVE]: partial fill %d of %d; managing the smaller position",
            result.filled_quantity, plan.quantity,
        )

    filled_qty = int(result.filled_quantity)
    fill_price = float(result.average_price or plan.quote.ask or 0)
    state = ladder_mod.open_ladder(fill_price, time.time(), config.exits)
    detail = {
        "ladder": state.to_detail(),
        "risk_per_stop_inr": round(fill_price - state.stop_price, 2) * filled_qty,
        "entry": {
            "price": fill_price,
            "charges": charges.breakdown(fill_price, filled_qty, is_buy=True),
        },
        "signal": signal.values,
        "hold_seconds": hold_seconds_for(signal),
        # The leg is rewritten to what actually filled, so the exit sells the real size
        # rather than the size that was requested.
        "filled_quantity": filled_qty,
        "partial_fill": result.partial,
    }
    leg_actual = dict(plan.as_leg())
    leg_actual["quantity"] = filled_qty
    repo.replace_cycle_legs(cycle.id, [leg_actual])
    repo.mark_cycle_placed(cycle.id, order_ids=[result.order_id or ""], detail=detail)
    _logger.info(
        "momentum bot [LIVE]: filled %d @ %.2f, stop %.2f (order %s)",
        result.filled_quantity, fill_price, state.stop_price, result.order_id,
    )


def _close_live(
    proc: Any,
    user_id: str,
    config: MomentumLongScalperConfig,
    context: "PositionContext",
    decision: Any,
    charges: ChargesModel,
) -> None:
    from icici_breeze_backend.app.repositories import bots as repo
    from icici_breeze_backend.app.services.telegram_alerts import _notify

    leg = (context.cycle.legs or [{}])[0]
    quantity = int(leg.get("quantity") or 0)
    if quantity <= 0 or not context.quote.bid:
        _logger.warning("momentum bot [LIVE]: cannot price an exit; will retry next pass")
        return

    result = live.place_and_confirm(
        proc,
        user_id,
        live.LegOrder(
            stock_code=INDEX_STOCK_CODE, exchange_code=INDEX_EXCHANGE,
            right=str(leg.get("right") or "call"),
            strike_price=float(leg.get("strike_price") or 0),
            expiry_display=str(leg.get("expiry_display") or ""),
            action=cfg.SELL, quantity=quantity,
        ),
        # An exit must complete -- there is a live position with no stop behind it -- so
        # unlike an entry it steps progressively further through the touch.
        price_for_attempt=live.exit_price_ladder(
            float(context.quote.bid), config.execution.exit_limit_band_pct
        ),
        timeout_seconds=config.execution.entry_fill_timeout_seconds,
        attempts=3,
    )

    if not result.ok:
        # Retried and still not out. Alert and STOP trying: firing more orders into a market
        # that keeps refusing them spends friction and achieves nothing. The position is the
        # user's to close, and they are told so.
        _logger.error("momentum bot [LIVE]: exit failed -- %s", result.error)
        try:
            _notify(
                user_id,
                "\U0001f6d1 <b>Scalping bot could not exit</b>\n\n"
                f"An exit for {leg.get('right')} {int(float(leg.get('strike_price') or 0))} "
                f"did not fill after {result.attempts} attempts.\n\n"
                f"{result.error or ''}\n\n"
                "<b>The position is still open.</b> Close it from the Order Book.",
                kind="scalping_exit_failed",
            )
        except Exception:  # noqa: BLE001
            _logger.exception("momentum bot: could not send the exit-failure alert")
        return

    exit_price = float(result.average_price or context.quote.bid)
    entry_price = float(((context.cycle.detail or {}).get("entry") or {}).get("price") or 0)
    gross = round((exit_price - entry_price) * quantity, 2)
    friction = round(
        float(((context.cycle.detail or {}).get("entry") or {}).get("charges", {}).get("total") or 0)
        + charges.leg_charges(exit_price, quantity, is_buy=False),
        2,
    )
    detail = dict(context.cycle.detail or {})
    detail["exit"] = {"price": exit_price, "order_id": result.order_id}
    repo.close_cycle(
        context.cycle.id,
        exit_reason_code=decision.reason_code,
        exit_reason_text=decision.reason_text,
        exit_value=round(exit_price * quantity, 2),
        gross_pnl=gross, friction=friction, detail=detail,
    )
    _logger.info("momentum bot [LIVE]: closed -- gross %+.2f, friction %.2f", gross, friction)
