"""Placing a ticket: one order at a time, in the safe sequence (plan section 4).

Every order goes through `scalping/live.place_and_confirm`, the path the bots trade on: sliced
under the freeze quantity, confirmed off the WS order feed with a REST backstop, cancelled and
re-priced on a timeout, and stopped dead on an answer that says nothing (#54, #55). Calls are
serialized by the pacer underneath (#24); nothing here runs two orders at once.

**Order and stopping.** The sequence is `orders.safe_sequence`: new longs, buy-backs, new shorts,
old longs. At every step no short is without its wing, so stopping at the first order that does
not complete always leaves a hedged position -- the executor never pushes on past a failure. A
step whose outcome is unknown (a failed cancel, a lost answer) stops everything and alerts.

**Prices.** Opening orders need a live quote and get a bounded limit at the touch; closing orders
fall back to a REST quote and step further through the touch on each retry, because an exit has
to complete (#58). Nothing is ever a raw market order.

**The ledger** books each fill the moment it lands, at the broker's traded price with its charges,
so a stop at step 3 leaves the ledger agreeing with the broker for steps 1-2. The execution row
is written before each order goes out and after it settles.

**Read-only licence mode** blocks a ticket that opens anything; a ticket that only closes runs
(#57). The route enforces it.
"""
from __future__ import annotations

import dataclasses
import datetime
import logging
import threading
from typing import Any, Callable, Optional

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.repositories import condor as repo
from icici_breeze_backend.app.services.bots.charges import load_charges
from icici_breeze_backend.app.services.condor import campaigns, tickets
from icici_breeze_backend.app.services.condor import orders as order_ops
from icici_breeze_backend.app.services.condor.model import OrderLeg

_logger = logging.getLogger(__name__)

FILL_TIMEOUT_SECONDS = 5.0
OPEN_ATTEMPTS = 2
CLOSE_ATTEMPTS = 3
ENTRY_TOLERANCE_PCT = 1.0
EXIT_BAND_PCT = 1.0
_FMT = "%d-%b-%Y"

_running: set[str] = set()
_lock = threading.Lock()


class Refused(ValueError):
    pass


def _fill_kind(ticket_kind: str, o: OrderLeg) -> str:
    if ticket_kind not in ("suggested_roll", "suggested_entry", "suggested_close"):
        return "manual_adjust"
    if ticket_kind == "suggested_entry":
        return "entry"
    if ticket_kind == "suggested_close":
        return "exit"
    return "roll_open" if o.opening else "roll_close"


def _step(i: int, o: OrderLeg, cycle_expiry: str) -> dict[str, Any]:
    return {
        "seq": i + 1, "action": o.action, "strike": o.strike, "right": o.right, "quantity": o.quantity,
        "opening": o.opening, "expiry": o.expiry.strftime(_FMT) if o.expiry else cycle_expiry,
        "status": "pending", "order_id": None, "filled": 0, "avg_price": None, "error": None,
    }


def plan(proc: Any, campaign: repo.Campaign, raw: list[dict[str, Any]]) -> tuple[list[OrderLeg], tickets.Ctx]:
    """The canonical, sequenced orders a ticket executes as, or Refused."""
    ctx = tickets.context(proc, campaign)
    return _plan(ctx, raw)


def _plan(ctx: "tickets.Ctx", raw: list[dict[str, Any]]) -> tuple[list[OrderLeg], "tickets.Ctx"]:
    canonical = tickets.normalize(ctx, raw)
    if not canonical:
        raise Refused("The ticket has no orders, or they cancel out.")
    for exp, pos in tickets.target_position(ctx, canonical).items():
        if order_ops.unhedged_rights(pos):
            raise Refused("This ticket would leave a short with no long to cap it. That is the one thing a ticket may not do.")
    sequenced: list[OrderLeg] = []
    for exp in [None] + sorted({o.expiry for o in canonical if o.expiry}):
        sequenced += order_ops.safe_sequence([o for o in canonical if o.expiry == exp], ctx.market.spot)
    return sequenced, ctx


def opens_anything(sequenced: list[OrderLeg]) -> bool:
    return any(o.opening for o in sequenced)


def start(
    proc: Any,
    campaign: repo.Campaign,
    raw: list[dict[str, Any]],
    *,
    kind: str,
    note: Optional[str] = None,
    warnings: Optional[list[str]] = None,
    run: Callable[[Callable[[], None]], None] = lambda fn: threading.Thread(target=fn, name="condor-exec", daemon=True).start(),
) -> dict[str, Any]:
    if campaign.mode == "paper":
        raise Refused("This is a paper campaign: its actions are simulated by the bot, never placed.")
    sequenced, ctx = plan(proc, campaign, raw)
    with _lock:
        if campaign.id in _running or repo.running_execution(campaign.id):
            raise Refused("A ticket is already executing on this campaign. Wait for it to finish.")
        _running.add(campaign.id)
    try:
        steps = [_step(i, o, ctx.expiry_display) for i, o in enumerate(sequenced)]
        eid = repo.create_execution(campaign.id, kind, steps, note=note, warnings=warnings)
    except Exception:
        with _lock:
            _running.discard(campaign.id)
        raise

    def target() -> None:
        try:
            _run(proc, campaign, eid, sequenced, steps, kind, note)
        except Exception as exc:  # noqa: BLE001 -- recorded on the row and alerted
            _logger.exception("condor: execution %s failed", eid)
            repo.update_execution(eid, steps=steps, status="failed", message=f"Stopped by an internal error: {exc}")
            _alert(campaign, f"Ticket stopped by an internal error: {exc}. Check the card for any difference to assign.")
        finally:
            with _lock:
                _running.discard(campaign.id)

    run(target)
    return repo.get_execution(eid, campaign.id) or {"id": eid}


def _alert(campaign: repo.Campaign, text: str) -> None:
    from icici_breeze_backend.app.services.condor.scheduler import _notify

    expiry = campaign.cycle.expiry if campaign.cycle else "—"
    _notify(campaign.user_id, f"Iron Condor · NIFTY {expiry}\n{text}")


def _price_ladder(proc: Any, user_id: str, o: OrderLeg, expiry_display: str) -> tuple[Optional[Callable[[int], float]], int, Optional[str]]:
    from icici_breeze_backend.app.services.bots.scalping import live as scalp
    from icici_breeze_backend.app.services.bots.scalping.momentum_bot import live_quote

    right = "call" if o.right == "Call" else "put"
    q = live_quote(proc, user_id, expiry_display, o.strike, right)
    bid = q.bid if q.live and q.bid and q.bid > 0 else None
    ask = q.ask if q.live and q.ask and q.ask > 0 else None
    if o.opening:
        if o.action == "Buy" and ask:
            return scalp.entry_price_ladder(ask, ENTRY_TOLERANCE_PCT), OPEN_ATTEMPTS, None
        if o.action == "Sell" and bid:
            return scalp.exit_price_ladder(bid, EXIT_BAND_PCT, widen=0.0), OPEN_ATTEMPTS, None
        return None, 0, "No live quote to open at; a position is never opened on a stand-in price."
    if not (bid if o.action == "Sell" else ask):
        rb, ra = scalp.rest_option_touch(proc, user_id, stock_code="NIFTY", exchange_code=cfg.NFO,
                                         expiry_display=expiry_display, strike_price=o.strike, right=right)
        bid, ask = bid or rb, ask or ra
    if o.action == "Buy" and ask:
        return scalp.buyback_price_ladder(ask, EXIT_BAND_PCT), CLOSE_ATTEMPTS, None
    if o.action == "Sell" and bid:
        return scalp.exit_price_ladder(bid, EXIT_BAND_PCT), CLOSE_ATTEMPTS, None
    return None, 0, "No quote, live or from ICICI, to price this close; nothing was sent."


def _run(
    proc: Any, campaign: repo.Campaign, eid: str, sequenced: list[OrderLeg], steps: list[dict[str, Any]],
    kind: str, note: Optional[str],
) -> None:
    from icici_breeze_backend.app.services.bots.scalping import live as scalp

    charges = load_charges()
    cycle = campaign.cycle
    cycle_id, cycle_expiry = cycle.id, cycle.expiry
    status, message = "completed", f"All {len(steps)} order(s) filled."
    for o, st in zip(sequenced, steps):
        if o.expiry is not None and st["expiry"] != cycle_expiry:
            # A time roll's second half: the old cycle must be flat before the next opens.
            held = campaigns.ledger_position(repo.list_fills(campaign.id), cycle_expiry)
            if held:
                status, message = "stopped", "The old cycle is not flat, so the next cycle was not opened."
                break
            repo.close_cycle(cycle_id, "time_roll")
            cycle_id, cycle_expiry = repo.open_cycle(campaign.id, st["expiry"]), st["expiry"]
        ladder, attempts, why = _price_ladder(proc, campaign.user_id, o, st["expiry"])
        if ladder is None:
            st.update(status="not_sent", error=why)
            status, message = "stopped", f"Step {st['seq']} not sent: {why}"
            break
        st["status"] = "sending"
        repo.update_execution(eid, steps=steps)
        fill = scalp.place_and_confirm(
            proc, campaign.user_id,
            scalp.LegOrder("NIFTY", cfg.NFO, "call" if o.right == "Call" else "put", o.strike,
                           st["expiry"], cfg.BUY if o.action == "Buy" else cfg.SELL, o.quantity),
            price_for_attempt=ladder, timeout_seconds=FILL_TIMEOUT_SECONDS, attempts=attempts,
        )
        st.update(order_id=fill.order_id, filled=int(fill.filled_quantity or 0), avg_price=fill.average_price, error=fill.error)
        if fill.filled_quantity:
            price = float(fill.average_price or 0.0)
            repo.add_fills(campaign.id, cycle_id, [{
                "order_id": fill.order_id, "expiry": st["expiry"], "strike": o.strike, "right": o.right,
                "side": o.action, "quantity": int(fill.filled_quantity), "price": price,
                "charges": charges.leg_charges(price, int(fill.filled_quantity), is_buy=o.action == "Buy"),
                "kind": _fill_kind(kind, o), "note": note,
            }])
        if fill.ok:
            st["status"] = "filled"
            repo.update_execution(eid, steps=steps)
            continue
        why = _reason(fill.error)
        st["error"] = why
        if fill.unaccounted:
            st["status"] = "unaccounted"
            status = "unaccounted"
            message = (f"Step {st['seq']}: {why}. Nothing more was sent. An order may still be working: "
                       "check the Order Book, then assign any difference on the card.")
        else:
            st["status"] = "partial" if fill.partial else "failed"
            status = "stopped"
            message = f"Step {st['seq']} did not complete: {why}. Nothing after it was sent; {_state_after(campaign, cycle_expiry)}"
        break

    repo.update_execution(eid, steps=steps, status=status, message=message)
    _settle(campaign, cycle_id, cycle_expiry, kind, status)
    done = sum(1 for s in steps if s["status"] == "filled")
    repo.add_decision(
        campaign.id, check_kind="manual", action=kind, reason=status,
        text=f"Ticket {status}: {done}/{len(steps)} order(s) filled. {message}",
        snapshot={"execution_id": eid, "steps": steps}, outcome=status, note=note,
    )
    if status != "completed":
        _alert(campaign, message)


def _reason(error: Optional[str]) -> str:
    """The broker path's error, as a clause: its bot-specific tail dropped, no full stop."""
    text = (error or "not filled").strip()
    text = text.replace(" and the cycle abandoned", "").replace("; order cancelled", ", so the order was cancelled")
    return text.rstrip(". ")


def _state_after(campaign: repo.Campaign, cycle_expiry: str) -> str:
    """Say the position is hedged only when it is: the safe order keeps a hedged one hedged,
    but a ticket can start from a position that never was."""
    held = campaigns.ledger_position(repo.list_fills(campaign.id), cycle_expiry)
    naked = order_ops.unhedged_rights({k: v[0] for k, v in held.items()})
    if naked:
        return "the position has a short with no wing, as it did before this ticket."
    return "every short still has its wing."


def _settle(campaign: repo.Campaign, cycle_id: int, cycle_expiry: str, kind: str, status: str) -> None:
    """After a ticket: count a tranche, and end a cycle -- and the campaign -- left flat."""
    if status == "completed" and kind in ("add_tranche", "suggested_entry"):
        repo.bump_tranches(cycle_id)
    held = campaigns.ledger_position(repo.list_fills(campaign.id), cycle_expiry)
    if not held and status == "completed":
        repo.close_cycle(cycle_id, kind)
        fresh = repo.get_campaign(campaign.id, campaign.user_id)
        if fresh and fresh.cycle is None:
            repo.close_campaign(campaign.id, campaign.user_id, kind)
    elif status == "completed" and kind == "time_roll":
        repo.bump_tranches(cycle_id)
