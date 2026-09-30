"""Live order dispatch for the scalpers (docs/bots-scalping-plan.md, build-order step 9).

**Gated, but not by a constant in this file.** Step 9's precondition used to be a sentence
in a document -- "a full session of paper evidence on the production instance, which no code
can assert" -- held up by a hardcoded flag a developer flipped by hand. It is now a real
precondition: `scalping/evidence.py` requires a *completed paper trading day on the settings
the bot would trade with*, enforced server-side in the PATCH path, and the user then confirms
a dialog showing what that day actually did. Editing any P&L-bearing setting invalidates the
evidence automatically, because the fingerprint it is counted against just changed.

What live adds over paper, and why each part exists
---------------------------------------------------
Paper fills at the touch and abandons an unfilled entry with nothing to clean up. Live has
none of those luxuries:

* **An unfilled limit is a real order resting at the exchange.** It has to be cancelled
  before the cycle is abandoned, and a cancel can itself fail -- which is the worst state to
  keep trading around, so it stands the bot down rather than being logged and forgotten.
* **A crash between `place_order` returning and the row being written leaves a live position
  nothing knows about.** The cycle row is therefore written BEFORE the order goes out.
* **An exit can be rejected**, leaving a position open with no stop running. Exits re-price
  progressively further through the touch, and when the retries are spent the bot alerts and
  stops trying rather than firing orders into a market that keeps refusing them.

Fill confirmation is the WS order feed with a REST backstop, not the WS alone. That is the
same lesson `strategy_group_lifecycle` learned: a completion path that only listens for an
event gets stuck the one time the event does not arrive.

Every call goes through the serialized pacer. Nothing here dispatches concurrently -- see
design decision #24.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import icici_breeze_backend.app.core.config as cfg

_logger = logging.getLogger(__name__)

# Terminal broker states. `executed_quantity` is what actually decides a fill; these only
# say the order will not change again.
_FILLED = {"executed", "complete", "completed"}
# ICICI reports an order that traded partly before it died with its own two strings
# (`cfg.PARTIAL_EXECUTED_CANCELED` / `_EXPIRED`, lower-cased here). Missing them left such an
# order "live" until the timeout, then read the cancel of a dead order as a failed cancel
# (B-53). Unknown statuses stay non-terminal, as `order_notifications` treats them.
_DEAD = {
    "cancelled", "canceled", "rejected", "expired",
    "partially executed and cancelled", "partially executed and expired",
}


@dataclass
class FillResult:
    order_id: Optional[str] = None
    filled_quantity: int = 0
    requested_quantity: int = 0
    average_price: Optional[float] = None
    error: Optional[str] = None
    cancelled: bool = False
    # True when an order was placed and could NOT be cancelled. The caller must stand the
    # bot down: an order you believe is dead but is not will fill later, into a position
    # nothing is managing.
    cancel_failed: bool = False
    # True when `place_order` gave no usable answer (transport error, garbled body, no order
    # id) AND the order book could not settle whether the order went in (B-21). Same
    # consequence as `cancel_failed`: an order may exist that nothing is tracking, so no
    # further order may go out for this position until `order_intents` has answered.
    outcome_unknown: bool = False
    attempts: int = 0

    @property
    def unaccounted(self) -> bool:
        """An order may be live that this result cannot describe."""
        return self.cancel_failed or self.outcome_unknown

    @property
    def ok(self) -> bool:
        """Fully filled as asked.

        A PARTIAL is deliberately not ok: the caller asked for a size and did not get it, and
        letting 25 of 75 read as success is how a bot ends up believing it holds a position
        it does not. Callers read `filled_quantity` to decide what to do with the remainder --
        for an entry that is a real, smaller position, not something to walk away from.
        """
        return (
            self.error is None
            and self.filled_quantity > 0
            and self.filled_quantity >= self.requested_quantity
        )

    @property
    def partial(self) -> bool:
        return 0 < self.filled_quantity < self.requested_quantity


class _FillTracker:
    """Watches the WS order feed for the orders this module has placed.

    Registered once, for the life of the process. Keyed by order id, which
    `OrderNotification` documents as the same identifier `place_order` returns -- that
    identity is the whole reason a bot can tell its own fill from a manual trade.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._states: dict[str, dict[str, Any]] = {}
        self._registered = False

    def ensure_registered(self) -> None:
        if self._registered:
            return
        from icici_breeze_backend.app.services import ws_tick_pipeline

        ws_tick_pipeline.register_order_notification_listener(self._on_notification)
        self._registered = True

    def watch(self, order_id: str) -> None:
        """Register interest. The value stays EMPTY until a notification actually arrives.

        Empty rather than a zero-filled placeholder, and the distinction matters: callers
        treat a falsy state as "nothing heard yet" and fall back to the REST backstop. A
        placeholder reading `executed: 0` is indistinguishable from a real unfilled report,
        and would clobber the REST answer on every pass -- which is exactly the bug that
        made the backstop silently useless the first time this was written.
        """
        with self._lock:
            self._states.setdefault(str(order_id), {})

    def _on_notification(self, note: Any) -> None:
        try:
            order_id = str(getattr(note, "order_id", "") or "")
            if not order_id:
                return
            with self._lock:
                if order_id not in self._states:
                    return  # someone else's order, or a manual trade
                self._states[order_id] = {
                    "executed": int(getattr(note, "executed_quantity", 0) or 0),
                    "status": str(getattr(note, "status", "") or "").lower(),
                    "price": getattr(note, "average_price", None),
                }
        except Exception:  # noqa: BLE001 -- a listener must never raise into the feed
            _logger.debug("live: order notification not understood", exc_info=True)

    def state(self, order_id: str) -> dict[str, Any]:
        """The last notification for this order, or {} if none has arrived."""
        with self._lock:
            return dict(self._states.get(str(order_id), {}))

    def forget(self, order_id: str) -> None:
        with self._lock:
            self._states.pop(str(order_id), None)


_tracker = _FillTracker()


def _rest_order_state(
    proc: Any, user_id: str, order_id: str, exchange_code: str = cfg.NFO
) -> dict[str, Any]:
    """REST backstop. Only consulted when the WS feed has told us nothing.

    Deliberately the fallback rather than the primary: it costs a broker call, and the feed
    is right almost always. But 'almost always' is exactly the gap that left Strategy Groups
    stuck on Fired when a completion event went missing.

    `exchange_code` defaults to NFO because Bots 3/4 trade NIFTY only. CAS Bingo trades SENSEX
    on BFO too, and an order looked up on the wrong exchange simply is not found -- which
    reads as "nothing filled" and would abandon a real fill.
    """
    try:
        breeze = proc.get_session_breeze(user_id)
        if breeze is None:
            return {}
        resp = breeze.get_order_detail(exchange_code=exchange_code, order_id=str(order_id))
        rows = (resp or {}).get("Success") or []
        if not rows:
            return {}
        row = rows[0]
        return {
            "executed": int(float(row.get("quantity_executed") or row.get("executed_quantity") or 0)),
            "status": str(row.get("status") or "").lower(),
            "price": row.get("average_price"),
        }
    except Exception:  # noqa: BLE001
        _logger.debug("live: REST order lookup failed for %s", order_id, exc_info=True)
        return {}


def await_fill(
    proc: Any,
    user_id: str,
    order_id: str,
    *,
    quantity: int,
    timeout_seconds: float,
    poll_interval: float = 0.5,
    now: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    exchange_code: str = cfg.NFO,
) -> dict[str, Any]:
    """Block until the order fills, dies, or the timeout expires.

    Returns the last known state. A partially filled order is reported as it stands -- the
    caller decides whether a partial is usable, because for an entry it is not and for an
    exit it very much is.
    """
    deadline = now() + max(0.0, float(timeout_seconds))
    state: dict[str, Any] = {}
    consulted_rest = False
    while True:
        state = _tracker.state(order_id) or state
        executed = int(state.get("executed") or 0)
        status = str(state.get("status") or "")
        if executed >= quantity or status in _FILLED or status in _DEAD:
            return state
        if now() >= deadline:
            # One REST check before giving up: the feed may simply have missed the event,
            # and abandoning a filled order as unfilled is the expensive mistake here.
            if not consulted_rest:
                consulted_rest = True
                rest = _rest_order_state(proc, user_id, order_id, exchange_code)
                if rest:
                    state = rest
                    continue
            return state
        sleep(poll_interval)


def cancel(proc: Any, user_id: str, order_id: str, exchange_code: str = cfg.NFO) -> bool:
    """Cancel on the exchange the order was placed on.

    `cancel_order_single` assumes NFO unless the reference carries `|EXCH`, so a bare id sent
    a SENSEX (BFO) cancel to NFO, where it cannot succeed (B-10): every CAS Bingo SENSEX
    timeout became a "could not cancel" stand-down while the order kept resting.
    """
    ref = str(order_id) if str(exchange_code).upper() == cfg.NFO else f"{order_id}|{exchange_code}"
    try:
        result = proc.cancel_order_single(user_id, ref)
        return bool((result or {}).get("success"))
    except Exception:  # noqa: BLE001
        _logger.warning("live: cancel raised for %s", order_id, exc_info=True)
        return False


@dataclass
class LegOrder:
    stock_code: str
    exchange_code: str
    right: str          # "call" | "put"
    strike_price: float
    expiry_display: str
    action: str         # cfg.BUY | cfg.SELL
    quantity: int


def place_and_confirm(
    proc: Any,
    user_id: str,
    leg: LegOrder,
    *,
    price_for_attempt: Callable[[int], float],
    timeout_seconds: float,
    attempts: int = 2,
    now: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    journal: Any = None,
    wall: Callable[[], float] = time.time,
) -> FillResult:
    """Place one leg, sliced under the contract's freeze quantity (B-33).

    The exchange rejects an order above the freeze quantity outright, and the scalpers'
    sizing is not capped by it (cheap expiry-day options can size to thousands of units).
    Each slice is placed and confirmed in turn, the same as a whole order below. The first
    slice that does not fill completely ends the leg: what filled is reported as a partial,
    and a failed cancel or an unknown answer stops everything, as it does for one order.
    """
    from dataclasses import replace as _replace

    from icici_breeze_backend.app.services.bots.placement import qty_per_order

    total = int(leg.quantity)
    per_order = qty_per_order(proc, leg.stock_code, leg.expiry_display, leg.exchange_code, total)
    if per_order <= 0 or per_order >= total:
        return _place_and_confirm_one(
            proc, user_id, leg, price_for_attempt=price_for_attempt,
            timeout_seconds=timeout_seconds, attempts=attempts, now=now, sleep=sleep,
            journal=journal, wall=wall,
        )
    chunks = [per_order] * (total // per_order) + ([total % per_order] if total % per_order else [])
    combined = FillResult(requested_quantity=total)
    value = 0.0
    for chunk in chunks:
        part = _place_and_confirm_one(
            proc, user_id, _replace(leg, quantity=chunk), price_for_attempt=price_for_attempt,
            timeout_seconds=timeout_seconds, attempts=attempts, now=now, sleep=sleep,
            journal=journal, wall=wall,
        )
        combined.attempts += part.attempts
        combined.order_id = part.order_id or combined.order_id
        if part.filled_quantity:
            combined.filled_quantity += int(part.filled_quantity)
            value += float(part.average_price or 0.0) * int(part.filled_quantity)
            combined.average_price = round(value / combined.filled_quantity, 4)
        combined.cancelled = combined.cancelled or part.cancelled
        combined.cancel_failed = combined.cancel_failed or part.cancel_failed
        combined.outcome_unknown = combined.outcome_unknown or part.outcome_unknown
        if not part.ok:
            combined.error = part.error or f"A {chunk}-unit slice did not fill."
            return combined
    return combined


def _place_and_confirm_one(
    proc: Any,
    user_id: str,
    leg: LegOrder,
    *,
    price_for_attempt: Callable[[int], float],
    timeout_seconds: float,
    attempts: int = 2,
    now: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    journal: Any = None,
    wall: Callable[[], float] = time.time,
) -> FillResult:
    """Place, wait, and on a timeout cancel and re-price -- bounded by `attempts`.

    `price_for_attempt(i)` supplies the limit for attempt i, so an entry can re-price against
    the fresh touch and an exit can widen its band progressively. Keeping pricing outside
    this function is what lets one dispatcher serve both.

    A cancel that fails sets `cancel_failed` and stops immediately. Placing a *second* order
    while unsure whether the first is dead is how a bot ends up with twice the position it
    intended.

    `journal` (an `order_intents.Journal`) records each order on the cycle row just before it
    goes out and its id the moment ICICI returns one -- not after the fill wait -- so a crash
    at any point leaves an answerable question (B-02).

    An answer that says nothing -- a transport error, a garbled body, a 200 without an order
    id -- is not a refusal (#24): the order may have been accepted before the answer was
    lost. The order book is read before anything is concluded (B-21); if it cannot settle
    the question, `outcome_unknown` is set and nothing more is placed.
    """
    from icici_breeze_backend.app.services.bots.scalping import order_intents

    _tracker.ensure_registered()
    result = FillResult(requested_quantity=int(leg.quantity))
    ours: set[str] = set()  # ids this call has already placed, never another attempt's match

    for attempt in range(max(1, int(attempts))):
        result.attempts = attempt + 1
        price = limit_on_tick(float(price_for_attempt(attempt)), leg.action)
        sent_at = wall()
        tag = order_intents.new_tag()
        token = journal.sending(leg, price, sent_at, tag) if journal is not None else None
        try:
            response = proc.place_order(
                user_id,
                cfg.OPTIONS,
                leg.stock_code,
                leg.action,
                leg.strike_price,
                cfg.CALL if leg.right == "call" else cfg.PUT,
                str(price),
                leg.expiry_display,
                leg.quantity,
                exchange_code=leg.exchange_code,
                user_remark=tag,
            )
        except Exception as exc:  # noqa: BLE001
            response = {
                "Status": None,
                "Error": f"Broker call failed while placing: {exc}",
                "outcome_unknown": True,
            }

        order_id = ""
        if isinstance(response, dict) and response.get("Status") == 200:
            order_id = str((response.get("Success") or {}).get("order_id") or "")
            if not order_id:
                response = {
                    **response, "Error": "Broker did not return an order id.",
                    "outcome_unknown": True,
                }
        if not order_id:
            error = str((response or {}).get("Error") or "Broker rejected the order.")
            if not (isinstance(response, dict) and response.get("outcome_unknown")):
                result.error = error  # ICICI answered, and the answer was no
                return result
            claimed = set(ours) | (set(journal.order_ids()) if journal is not None else set())
            sleep(order_intents.LOCATE_SETTLE_SECONDS)
            found = order_intents.locate_order(
                proc, user_id, order_intents.intent_fields(leg, price, sent_at, tag), claimed=claimed,
            )
            if found.state == "absent":
                result.error = f"{error} The order book shows it was not placed."
                return result
            if found.state != "found":
                result.outcome_unknown = True
                result.error = (
                    f"{error} The order book could not say whether it was placed "
                    f"({found.why}), so this bot is standing down rather than trading "
                    f"around an order it cannot account for."
                )
                return result
            order_id = str(found.order_id)
            _logger.warning(
                "live: place_order gave no usable answer, but order %s is in the order book",
                order_id,
            )

        ours.add(order_id)
        result.order_id = order_id
        if journal is not None:
            journal.placed(token, order_id)
        _tracker.watch(order_id)
        state = await_fill(
            proc, user_id, order_id, quantity=leg.quantity,
            timeout_seconds=timeout_seconds, now=now, sleep=sleep,
            exchange_code=leg.exchange_code,
        )
        executed = int(state.get("executed") or 0)
        status = str(state.get("status") or "")

        if executed >= leg.quantity:
            result.filled_quantity = executed
            result.average_price = _traded_price(proc, user_id, order_id, leg, state, price)
            _tracker.forget(order_id)
            return result

        if status in _DEAD:
            _tracker.forget(order_id)
            result.filled_quantity = executed
            if executed > 0:
                # It traded partly before it died -- cancelled by someone else, or expired.
                # Those units are a real position, so this stops exactly as a partial we
                # cancelled ourselves does. Re-sending the full size on top of them bought
                # more than planned on an entry and sold more than was held on an exit
                # (B-53). The caller decides what the partial means.
                result.average_price = _traded_price(proc, user_id, order_id, leg, state, price)
                result.error = (
                    f"Order {status} after {executed} of {leg.quantity} filled."
                )
                return result
            # Rejected or cancelled with nothing traded: nothing rests, so a re-price is safe.
            if attempt + 1 >= attempts:
                result.error = f"Order {status or 'did not fill'} after {attempt + 1} attempt(s)."
                return result
            continue

        # Unfilled or partially filled and still live -- it must be cancelled before we
        # either try again or walk away.
        if not cancel(proc, user_id, order_id, leg.exchange_code):
            result.cancel_failed = True
            result.filled_quantity = executed
            if executed > 0:
                result.average_price = _traded_price(proc, user_id, order_id, leg, state, price)
            result.error = (
                f"Could not cancel order {order_id}. It may still fill, so this bot is "
                f"standing down rather than trading around an order it cannot account for."
            )
            return result
        result.cancelled = True
        result.filled_quantity = executed
        _tracker.forget(order_id)

        if executed > 0:
            # A partial that we then cancelled is a real, small position. The caller has to
            # decide, and for an entry the answer is to unwind it.
            result.average_price = _traded_price(proc, user_id, order_id, leg, state, price)
            return result
        if attempt + 1 >= attempts:
            result.error = "Limit did not fill; order cancelled and the cycle abandoned."
            return result

    return result


def _traded_price(
    proc: Any, user_id: str, order_id: str, leg: "LegOrder", state: dict[str, Any], limit: float
) -> float:
    """The price the units actually traded at (B-22).

    The order feed carries no usable average: `averageExecutedRate` held garbage in the live
    capture, so the tracker stores none, and every feed-confirmed fill used to be booked at
    its limit -- ask plus tolerance on an entry, a widening discount on an exit. Realized
    P&L, the daily stop, cooldown streaks and the ladder's first stop all read that. So a
    fill with no price costs one `get_order_detail` call for ICICI's own average.

    An average on the wrong side of the limit (above it for a buy, below it for a sell)
    cannot be a fill of this order and is not trusted; the limit is used, as before.
    """
    try:
        average = float(state.get("price") or 0)
    except (TypeError, ValueError):
        average = 0.0
    if average <= 0:
        rest = _rest_order_state(proc, user_id, order_id, leg.exchange_code)
        try:
            average = float(rest.get("price") or 0)
        except (TypeError, ValueError):
            average = 0.0
    if average <= 0:
        return float(limit)
    is_buy = leg.action == cfg.BUY
    if (is_buy and average > float(limit) + 1e-6) or (not is_buy and average < float(limit) - 1e-6):
        _logger.warning(
            "live: order %s average %.2f is past its %s limit %.2f; booking the limit",
            order_id, average, leg.action, limit,
        )
        return float(limit)
    return average


# At most one REST quote per contract in this many seconds for pricing an exit, so an exit
# retried every pass on a dead feed does not spend the broker's per-minute budget.
EXIT_QUOTE_SPACING_SECONDS = 20.0
_exit_quote_at: dict[tuple[str, str, str, float, str], float] = {}
_exit_quote_lock = threading.Lock()


def exit_touch(
    proc: Any,
    user_id: str,
    *,
    stock_code: str,
    exchange_code: str,
    expiry_display: str,
    strike_price: float,
    right: str,
    is_buy: bool,
    quote: Any = None,
    now: Callable[[], float] = time.monotonic,
) -> Optional[float]:
    """The touch an exit is priced from: the ask to buy back, the bid to sell.

    Taken from the live feed when `quote` is live. Otherwise one REST quote is asked for
    (B-23, B-44). An exit with no live bid used to wait for a feed that had just gone dark,
    which is exactly when the stale-feed exit and a hard square-off fire, and the fly and
    CAS Bingo priced a missing quote at 0.05, a buy-back that could never fill.

    None means no price could be found; the caller sends nothing and retries later.
    """
    if quote is not None and getattr(quote, "live", False):
        touch = getattr(quote, "ask" if is_buy else "bid", None)
        if touch and float(touch) > 0:
            return float(touch)
    key = (str(user_id), str(exchange_code), str(expiry_display), float(strike_price), str(right))
    with _exit_quote_lock:
        last = _exit_quote_at.get(key)
        if last is not None and now() - last < EXIT_QUOTE_SPACING_SECONDS:
            return None
        _exit_quote_at[key] = now()
    bid, ask = rest_option_touch(
        proc, user_id, stock_code=stock_code, exchange_code=exchange_code,
        expiry_display=expiry_display, strike_price=strike_price, right=right,
    )
    touch = ask if is_buy else bid
    return touch if touch and touch > 0 else None


def rest_option_touch(
    proc: Any,
    user_id: str,
    *,
    stock_code: str,
    exchange_code: str,
    expiry_display: str,
    strike_price: float,
    right: str,
) -> tuple[Optional[float], Optional[float]]:
    """(bid, ask) from one ICICI `get_quotes` call, or (None, None)."""
    from icici_breeze_backend.app.services.processor import _expiry_to_breeze_place_order

    try:
        sdk = proc.get_session_breeze(user_id)
        if sdk is None:
            return None, None
        response = sdk.get_quotes(
            stock_code=stock_code,
            exchange_code=exchange_code,
            expiry_date=_expiry_to_breeze_place_order(expiry_display),
            product_type="options",
            right="call" if str(right).lower().startswith("c") else "put",
            strike_price=f"{float(strike_price):g}",
        )
    except Exception:  # noqa: BLE001
        _logger.warning("live: REST quote for an exit failed", exc_info=True)
        return None, None
    rows = (response or {}).get("Success") if isinstance(response, dict) else None
    if not isinstance(rows, list):
        return None, None
    row = next(
        (r for r in rows if isinstance(r, dict) and r.get("exchange_code") == exchange_code),
        rows[0] if rows and isinstance(rows[0], dict) else None,
    )
    if row is None:
        return None, None

    def _num(value: Any) -> Optional[float]:
        try:
            out = float(value)
        except (TypeError, ValueError):
            return None
        return out if out > 0 else None

    return _num(row.get("best_bid_price")), _num(row.get("best_offer_price"))


def no_price_result(quantity: int) -> FillResult:
    """What a close pass records for a leg it could not price: nothing sent, nothing filled,
    so `held_legs` keeps the leg and retries it on its back-off."""
    return FillResult(
        requested_quantity=int(quantity),
        error="No live quote and no ICICI quote to price the exit; nothing was sent.",
    )


_TICK = 0.05


def limit_on_tick(price: float, action: str) -> float:
    """Snap a limit onto the exchange's 0.05 grid, never past the price the ladder allowed.

    The ladders scale the touch by a percentage, so they land off-grid (103.75 x 1.01 =
    104.79) and ICICI refuses the order outright ("Price should be in multiples of: 0.05").
    A buy rounds DOWN and a sell rounds UP: the touch is itself on the grid, so the order
    still crosses it, and a capped buy-back (CAS Bingo liquidation) can never overshoot its
    cap by rounding.
    """
    steps = round(float(price) / _TICK, 6)  # float noise: 101.0 / 0.05 is not exactly 2020
    steps = math.floor(steps) if action == cfg.BUY else math.ceil(steps)
    return max(_TICK, round(steps * _TICK, 2))


def entry_price_ladder(ask: float, tolerance_pct: float) -> Callable[[int], float]:
    """Buy limits: the ask plus a tolerance, re-priced on the second attempt.

    The second attempt is deliberately not more aggressive than the first -- it re-prices
    against a *fresh* touch supplied by the caller, rather than chasing a stale one further.
    """
    return lambda attempt: float(ask) * (1 + max(0.0, tolerance_pct) / 100.0)


def exit_price_ladder(bid: float, band_pct: float, *, widen: float = 1.0) -> Callable[[int], float]:
    """Sell limits, stepping further through the touch on each retry.

    An exit has to complete -- there is a live position with no stop behind it -- so unlike
    an entry this one does get progressively more aggressive, bounded by the retry count.

    `widen=0.0` turns it back into a flat marketable-sell limit, which is what an *entry*
    short wants: Bot 4 sells its ATM legs to open, and an opening order has no reason to
    chase (plan section 3.6's rule, applied to the fly's short legs).
    """
    return lambda attempt: max(
        0.05, float(bid) * (1 - (max(0.0, band_pct) * (1 + widen * attempt)) / 100.0)
    )


def buyback_price_ladder(
    ask: float, band_pct: float, *, widen: float = 1.0
) -> Callable[[int], float]:
    """Buy limits that pay progressively more, for closing a SHORT leg.

    The mirror of `exit_price_ladder` and needed for the same reason: Bot 4 unwinds by buying
    its short legs back, and a flat limit that will not fill leaves a short option open with
    the hedge being sold out from under it. `entry_price_ladder` cannot serve here -- it
    ignores the attempt number by design, because an entry that does not fill is a trade not
    taken, whereas an exit that does not fill is a position nobody is managing.
    """
    return lambda attempt: max(
        0.05, float(ask) * (1 + (max(0.0, band_pct) * (1 + widen * attempt)) / 100.0)
    )


def reset_state_for_tests() -> None:
    global _tracker
    _tracker = _FillTracker()
    with _exit_quote_lock:
        _exit_quote_at.clear()
