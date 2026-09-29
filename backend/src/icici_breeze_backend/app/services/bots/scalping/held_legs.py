"""What a live multi-leg bot cycle actually holds, and how a close that stuck is retried.

B-01 (docs/bug-audit-2026-09-28.md). A cycle's `legs` used to be written once, from the plan,
and never again. When a close or an entry unwind left something open, the exit loop re-ran
the close over the *planned* legs: it bought back shorts that were already bought back
(opening new longs) and sold wings that were already sold or never bought (opening new naked
shorts). The invariant this module keeps is:

    **`cycle.legs` is what the bot holds right now**, rewritten every time the broker answers.

Shared by Bot 4 (the iron fly) and CAS Bingo. Bot 3 holds one leg and already rewrites it
after a partial (`momentum_bot._record_live_entry`).

Four rules, each closing a way the old code opened a position while trying to close one:

* **Remaining size comes from `filled_quantity`, not `ok`.** A close that filled 50 of 75 is
  25 still open, not 75.
* **A long is not sold while the short it covers is still open.** Shorts go first so this is
  normally moot; when a short sticks, its wing is held back with it.
* **A retry is checked against the broker first.** The stuck-leg alert tells the user to look
  at the Order Book; a leg they close by hand must not be closed a second time. An unreadable
  positions call sends nothing (B-11: unreadable is not flat).
* **Retries are bounded.** After `MAX_CLOSE_RETRIES` the bot stops sending orders and only
  watches the broker, closing the row once the user has flattened what is left.
"""
from __future__ import annotations

import datetime
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import icici_breeze_backend.app.core.config as cfg

_logger = logging.getLogger(__name__)

# Seconds before each retry of a close that stuck. The last value repeats as the broker-watch
# interval once orders stop.
RETRY_BACKOFF_SECONDS = (30, 60, 120, 300)
# Automatic close attempts after the one that first stuck. Past this the market has refused
# the same leg five times; a sixth order is not what changes that.
MAX_CLOSE_RETRIES = len(RETRY_BACKOFF_SECONDS)


def leg_key(leg: dict[str, Any]) -> tuple[str, float]:
    return (str(leg.get("right") or "call"), float(leg.get("strike_price") or 0))


def is_short(leg: dict[str, Any]) -> bool:
    return leg.get("action") == cfg.SELL


def close_sequence(legs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Shorts bought back first, then longs sold -- selling a hedge first leaves a naked short
    for the life of one order."""
    return [l for l in legs if is_short(l)] + [l for l in legs if not is_short(l)]


def entry_price(leg: dict[str, Any], detail: dict[str, Any]) -> float:
    """What the leg was opened at: stamped on the leg once it has been rewritten, otherwise
    looked up in the entry fills. 0.0 when neither knows -- the same basis the whole-structure
    formula used for a cycle adopted without fills."""
    if leg.get("entry_price") is not None:
        return float(leg["entry_price"])
    right, strike = leg_key(leg)
    for fill in detail.get("entry_fills") or []:
        if (
            str(fill.get("right") or "") == right
            and float(fill.get("strike") or 0) == strike
            and fill.get("action") == leg.get("action")
        ):
            return float(fill.get("price") or 0.0)
    # The Long Scalper holds one leg and records its fill as `entry.price`.
    return float((detail.get("entry") or {}).get("price") or 0.0)


def resized(leg: dict[str, Any], quantity: int, *, entry: float) -> dict[str, Any]:
    out = dict(leg)
    out["quantity"] = int(quantity)
    out["entry_price"] = round(float(entry), 2)
    lot_size = int(leg.get("lot_size") or 0)
    if lot_size > 0:
        out["lots"] = int(quantity) // lot_size
    return out


# --------------------------------------------------------------------------------------
# One close pass
# --------------------------------------------------------------------------------------


@dataclass
class ClosePass:
    closed: list[dict[str, Any]] = field(default_factory=list)  # what filled this pass
    remaining: list[dict[str, Any]] = field(default_factory=list)  # still held, at its size
    stuck: list[dict[str, Any]] = field(default_factory=list)  # `remaining`, with the reason
    gross: float = 0.0  # realised this pass, leg by leg against each leg's entry price
    charges: float = 0.0  # exit charges this pass
    # Buy-backs minus sales per unit, over the legs closed this pass: the fly's familiar
    # "cost to close" figure when the whole structure goes in one pass.
    cost_per_unit: float = 0.0
    # An order could not be cancelled and may still fill. No further order may go out for
    # this cycle; retrying around it is how a close turns into an opposite position.
    cancel_failed: bool = False
    # A close got no usable answer and the order book could not say whether it went in
    # (B-21). Same consequence as a failed cancel: an order may be live that nothing tracks.
    outcome_unknown: bool = False

    @property
    def halted(self) -> bool:
        """No more orders may go out for this cycle; only the broker watch continues."""
        return self.cancel_failed or self.outcome_unknown


def run_close(
    legs: list[dict[str, Any]],
    detail: dict[str, Any],
    close_leg: Callable[[dict[str, Any], int], Any],
    charges_for: Callable[[float, int, bool], float],
) -> ClosePass:
    """Close `legs`, shorts first, and report exactly what is still held afterwards.

    `close_leg(leg, quantity)` places one closing order and returns a `live.FillResult`.
    `charges_for(price, quantity, is_buy)` prices one fill's charges.
    """
    out = ClosePass()
    open_short_rights: set[str] = set()
    for leg in close_sequence(legs):
        qty = int(leg.get("quantity") or 0)
        if qty <= 0:
            continue
        short = is_short(leg)
        right = str(leg.get("right") or "call")
        entry = entry_price(leg, detail)

        if not short and right in open_short_rights:
            out.remaining.append(resized(leg, qty, entry=entry))
            out.stuck.append({
                "right": right, "strike": leg_key(leg)[1], "quantity": qty,
                "error": "Held back: the short it covers is still open.",
            })
            continue

        result = close_leg(leg, qty)
        filled = max(0, min(qty, int(getattr(result, "filled_quantity", 0) or 0)))
        if getattr(result, "cancel_failed", False):
            out.cancel_failed = True
        if getattr(result, "outcome_unknown", False):
            out.outcome_unknown = True
        if filled > 0:
            price = float(getattr(result, "average_price", 0.0) or 0.0)
            # Closing reverses the leg: a short bought back costs, a long sold returns.
            out.gross += (entry - price) * filled if short else (price - entry) * filled
            out.charges += charges_for(price, filled, short)
            out.cost_per_unit += price if short else -price
            out.closed.append({
                "right": right, "strike": leg_key(leg)[1], "action": leg.get("action"),
                "quantity": filled, "price": price,
                "order_id": getattr(result, "order_id", None),
            })
        left = qty - filled
        if left > 0:
            out.remaining.append(resized(leg, left, entry=entry))
            out.stuck.append({
                "right": right, "strike": leg_key(leg)[1], "quantity": left,
                "error": getattr(result, "error", None) or "The close did not fill.",
            })
            if short:
                open_short_rights.add(right)

    out.gross = round(out.gross, 2)
    out.charges = round(out.charges, 2)
    out.cost_per_unit = round(out.cost_per_unit, 2)
    return out


def stuck_text(stuck: list[dict[str, Any]]) -> str:
    return "; ".join(
        f"{s['right']} {int(float(s['strike']))} x{s['quantity']} still open" for s in stuck
    )


# --------------------------------------------------------------------------------------
# Persisting a close that stuck, and finishing one that did not
# --------------------------------------------------------------------------------------


def record_stuck(
    cycle_id: str, detail: dict[str, Any], result: ClosePass, *, now: Optional[float] = None
) -> dict[str, Any]:
    """Write what is still held, bank what closed, and schedule the next attempt.

    Returns the detail as written. Legs and detail go in one write -- see
    `repo.update_cycle_holdings`.
    """
    from icici_breeze_backend.app.repositories import bots as repo

    now = time.time() if now is None else now
    detail = dict(detail)
    _bank(detail, result)
    detail["exit_partial"] = {"closed": result.closed, "stuck": result.stuck}
    attempts = int(detail.get("unwind_attempts") or 0) + 1
    detail["unwinding"] = True
    detail["unwind_attempts"] = attempts
    if result.cancel_failed:
        detail["unwind_orders_halted"] = "cancel_failed"
    elif result.outcome_unknown:
        detail["unwind_orders_halted"] = "outcome_unknown"
    elif attempts > MAX_CLOSE_RETRIES:
        detail["unwind_orders_halted"] = "attempts"
    detail["unwind_next_at"] = now + RETRY_BACKOFF_SECONDS[
        min(attempts, len(RETRY_BACKOFF_SECONDS)) - 1
    ]
    repo.update_cycle_holdings(cycle_id, result.remaining, detail)
    return detail


def _bank(detail: dict[str, Any], result: ClosePass) -> None:
    """Add this pass's closed legs to the running totals the final booking sums."""
    detail["realized_gross"] = round(float(detail.get("realized_gross") or 0) + result.gross, 2)
    detail["realized_charges"] = round(
        float(detail.get("realized_charges") or 0) + result.charges, 2
    )
    if result.closed:
        detail["closed_legs"] = list(detail.get("closed_legs") or []) + result.closed


def totals_after(detail: dict[str, Any], result: ClosePass) -> tuple[float, float]:
    """(gross, exit charges) for the whole cycle: earlier passes plus this one."""
    return (
        round(float(detail.get("realized_gross") or 0) + result.gross, 2),
        round(float(detail.get("realized_charges") or 0) + result.charges, 2),
    )


def close_outlay(detail: dict[str, Any], result: ClosePass) -> float:
    """Rupees paid to close, across every pass: buy-backs minus sales. Equals the old
    `cost_per_unit x quantity` when the whole structure closes in one pass."""
    total = 0.0
    for c in list(detail.get("closed_legs") or []) + result.closed:
        amount = float(c.get("price") or 0) * int(c.get("quantity") or 0)
        total += amount if c.get("action") == cfg.SELL else -amount
    return round(total, 2)


def remember_exit(detail: dict[str, Any], code: str, text: str) -> None:
    """Keep the reason the close began. Later passes run under `closing_remainder`; the
    cycle is still booked as the stop (or target) that started it."""
    detail.setdefault("exit_decision", {"code": code, "text": text})


def exit_reason(detail: dict[str, Any], code: str, text: str) -> tuple[str, str]:
    saved = detail.get("exit_decision") or {}
    return str(saved.get("code") or code), str(saved.get("text") or text)


def retry_note() -> str:
    minutes = sum(RETRY_BACKOFF_SECONDS) / 60.0
    return (
        f"The bot will try to close these legs up to {MAX_CLOSE_RETRIES} more times over "
        f"about {minutes:.0f} minutes. It checks your positions before each try, so a leg you "
        "close yourself is not closed twice. After that it stops sending orders and waits "
        "for you."
    )


def halted_now(before: dict[str, Any], after: dict[str, Any]) -> bool:
    return bool(after.get("unwind_orders_halted")) and not before.get("unwind_orders_halted")


def alert_resolved(
    user_id: str, bot_name: str, what: str, *, kind: str, still_disarmed: bool = True
) -> None:
    """Tell the user on Telegram that legs they were alerted about are no longer open.

    The stuck alert sends them to the Order Book. Nobody is watching the bot live, so the
    all-clear has to arrive on the same channel, or the user is left checking a position that
    is already gone.
    """
    from icici_breeze_backend.app.services.telegram_alerts import _notify

    tail = (
        "The bot is still disarmed and will not open anything new until you arm it again."
        if still_disarmed
        else "Nothing is left open, so the bot can trade again."
    )
    try:
        _notify(
            user_id, f"\u2705 *{bot_name}: leftover legs closed*\n\n{what}\n\n{tail}", kind=kind,
        )
    except Exception:  # noqa: BLE001 -- an alert failure must not undo a close that happened
        _logger.exception("bots: could not send the leftover-legs-closed alert")


def closed_outside_text() -> str:
    return (
        "The legs the bot was still trying to close no longer show in your positions, so it "
        "has stopped managing them. The cycle's P&L covers only the legs the bot closed itself."
    )


def is_unwinding(cycle: Any) -> bool:
    return bool((getattr(cycle, "detail", None) or {}).get("unwinding"))


def still_disarmed(detail: dict[str, Any]) -> bool:
    """Whether the bot was switched off on the way into this unwind.

    A close or entry unwind that sticks disarms the bot. An unwind `order_intents` started
    for a crashed entry does not -- unless the resolver later gave up and disarmed it.
    """
    if detail.get("unwind_from_recovery"):
        return bool(detail.get("resolve_disarmed"))
    return True


def remainder_verdict(cycle: Any, *, now: Optional[float] = None) -> tuple[str, str]:
    """The exit verdict for a cycle already on its way out. Always an exit: the decision was
    taken when the first close went out; what is left is only whether this pass may act."""
    from icici_breeze_backend.app.domain.bots import ReasonCode

    now = time.time() if now is None else now
    detail = getattr(cycle, "detail", None) or {}
    left = stuck_text(
        [
            {"right": l.get("right"), "strike": l.get("strike_price"), "quantity": l.get("quantity")}
            for l in (getattr(cycle, "legs", None) or [])
        ]
    )
    if detail.get("unwind_orders_halted"):
        return (
            ReasonCode.CLOSING_REMAINDER,
            f"Stopped sending orders for {left}; waiting for them to be closed by hand.",
        )
    wait = float(detail.get("unwind_next_at") or 0) - now
    when = "now" if wait <= 0 else f"in {wait:.0f}s"
    return (ReasonCode.CLOSING_REMAINDER, f"Retrying the close of {left} {when}.")


# --------------------------------------------------------------------------------------
# The retry gate: due, checked against the broker, not halted
# --------------------------------------------------------------------------------------


@dataclass
class Retry:
    legs: list[dict[str, Any]]  # what to send closes for now
    detail: dict[str, Any]  # the cycle's detail as it now stands


def legs_to_retry(
    proc: Any, user_id: str, cycle: Any, *, now: Optional[float] = None
) -> Optional[Retry]:
    """What this pass may send for an unwinding cycle, or None to send nothing.

    `Retry.legs == []` means the broker shows none of it left: the caller closes the cycle as
    closed outside the bot (`close_flat`).
    """
    from icici_breeze_backend.app.repositories import bots as repo

    now = time.time() if now is None else now
    detail = dict(cycle.detail or {})
    if now < float(detail.get("unwind_next_at") or 0):
        return None
    legs = list(cycle.legs or [])
    held = broker_holdings(proc, user_id, legs)
    if held is None:
        # Asked again after the shortest wait, not every two-second pass: this is a REST
        # call against the same per-minute budget the orders need.
        detail["unwind_next_at"] = now + RETRY_BACKOFF_SECONDS[0]
        repo.update_cycle_detail(cycle.id, detail)
        _logger.warning(
            "bots: positions unreadable; not retrying the close of cycle %s this pass", cycle.id
        )
        return None

    clamped = clamp_to_broker(legs, held)
    if clamped != legs:
        gone = _shrinkage(legs, clamped)
        detail["closed_outside"] = list(detail.get("closed_outside") or []) + gone
        repo.update_cycle_holdings(cycle.id, clamped, detail)
        _logger.warning(
            "bots: cycle %s -- broker shows %s already closed; not closing it again",
            cycle.id, stuck_text(gone),
        )
    if clamped and detail.get("unwind_orders_halted"):
        detail["unwind_next_at"] = now + RETRY_BACKOFF_SECONDS[-1]
        repo.update_cycle_detail(cycle.id, detail)
        return None
    return Retry(legs=clamped, detail=detail)


def close_flat(cycle_id: str, detail: dict[str, Any], *, entry_charges: float) -> None:
    """Close a cycle whose remaining legs the broker no longer shows."""
    from icici_breeze_backend.app.domain.bots import ReasonCode
    from icici_breeze_backend.app.repositories import bots as repo

    gross = round(float(detail.get("realized_gross") or 0), 2)
    repo.close_cycle(
        cycle_id,
        exit_reason_code=ReasonCode.CLOSED_OUTSIDE_BOT,
        exit_reason_text=(
            "The legs the bot could not close were closed outside it. P&L covers only the "
            "legs the bot closed itself."
        ),
        gross_pnl=gross,
        friction=round(float(entry_charges) + float(detail.get("realized_charges") or 0), 2),
        detail=detail,
    )


def _parse_expiry(text: str) -> Optional[datetime.date]:
    try:
        return datetime.datetime.strptime(str(text).strip(), "%d-%b-%Y").date()
    except (TypeError, ValueError):
        return None


def broker_holdings(
    proc: Any, user_id: str, legs: list[dict[str, Any]]
) -> Optional[dict[tuple[str, float, str], int]]:
    """Units held per (right, strike, action) on the legs' underlying and expiry, or None when
    positions cannot be read."""
    if not legs:
        return {}
    from icici_breeze_backend.app.services.portfolio_margin_netting import (
        positions_for_underlying,
    )

    first = legs[0]
    positions = positions_for_underlying(
        proc, user_id, str(first.get("stock_code") or ""),
        str(first.get("exchange_code") or cfg.NFO),
    )
    if not positions.available:
        return None
    want = str(first.get("expiry_display") or "")
    want_date = _parse_expiry(want)
    held: dict[tuple[str, float, str], int] = {}
    for row in positions.rows:
        expiry = str(row.get("expiry_date") or "")
        same = (
            _parse_expiry(expiry) == want_date
            if want_date is not None
            else expiry.strip().lower() == want.strip().lower()
        )
        if not same:
            continue
        try:
            qty = abs(int(float(row.get("quantity") or 0)))
            strike = float(row.get("strike_price") or 0)
        except (TypeError, ValueError):
            continue
        raw_right = str(row.get("right") or "").strip().lower()
        right = "call" if raw_right in ("call", "ce", "c") else "put"
        action = cfg.SELL if str(row.get("action") or "").strip().lower() == "sell" else cfg.BUY
        key = (right, strike, action)
        held[key] = held.get(key, 0) + qty
    return held


def clamp_to_broker(
    legs: list[dict[str, Any]], held: dict[tuple[str, float, str], int]
) -> list[dict[str, Any]]:
    """Each leg cut to what the broker still shows on that side. Never grown: units beyond the
    bot's own remainder belong to something else."""
    out: list[dict[str, Any]] = []
    for leg in legs:
        right, strike = leg_key(leg)
        have = held.get((right, strike, str(leg.get("action") or "")), 0)
        qty = min(int(leg.get("quantity") or 0), have)
        if qty <= 0:
            continue
        if qty == int(leg.get("quantity") or 0):
            out.append(leg)
            continue
        cut = {**leg, "quantity": qty}
        lot_size = int(leg.get("lot_size") or 0)
        if lot_size > 0:
            cut["lots"] = qty // lot_size
        out.append(cut)
    return out


def _shrinkage(
    before: list[dict[str, Any]], after: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    left = {(leg_key(l), l.get("action")): int(l.get("quantity") or 0) for l in after}
    gone = []
    for leg in before:
        k = (leg_key(leg), leg.get("action"))
        diff = int(leg.get("quantity") or 0) - left.get(k, 0)
        if diff > 0:
            gone.append({"right": k[0][0], "strike": k[0][1], "quantity": diff})
    return gone


# --------------------------------------------------------------------------------------
# Rows left stuck by the code before B-01 was fixed
# --------------------------------------------------------------------------------------


def repair_legacy_rows() -> int:
    """Rewrite open live cycles that stuck before this module existed. Returns how many.

    Those rows still carry the planned legs, with what was actually left recorded only in
    `detail.exit_partial.stuck` or `detail.stuck_legs`. Rebuilt from that record and marked
    `unwinding` with no wait, so the next pass checks the broker and closes only what is
    still there. The recorded quantities can overstate what is open (a partly filled close
    was recorded at full size); the broker check is what cuts them down. Idempotent.
    """
    from icici_breeze_backend.app.db.bots_migrate import BOT_CAS_BINGO, BOT_IRON_FLY_SCALPER
    from icici_breeze_backend.app.repositories import bots as repo

    repaired = 0
    for user_id, bot_type in repo.bots_with_open_live_cycles():
        if bot_type not in (BOT_IRON_FLY_SCALPER, BOT_CAS_BINGO):
            continue
        for cycle in repo.open_cycles(user_id, bot_type):
            detail = dict(cycle.detail or {})
            if cycle.paper or detail.get("unwinding") or detail.get("pending"):
                continue
            stuck = (detail.get("exit_partial") or {}).get("stuck") or detail.get("stuck_legs")
            if not stuck:
                continue
            by_key = {leg_key(l): l for l in cycle.legs or []}
            legs = []
            for s in stuck:
                base = by_key.get((str(s.get("right") or "call"), float(s.get("strike") or 0)))
                if base is not None and int(s.get("quantity") or 0) > 0:
                    legs.append(
                        resized(base, int(s["quantity"]), entry=entry_price(base, detail))
                    )
            detail.update({"unwinding": True, "unwind_attempts": 1, "unwind_next_at": 0})
            repo.update_cycle_holdings(cycle.id, legs, detail)
            repaired += 1
            _logger.warning(
                "bots: rebuilt stuck cycle %s (%s) to its leftover legs: %s",
                cycle.id, bot_type, stuck_text(stuck),
            )
    return repaired
