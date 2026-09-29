"""Which orders a live bot entry sent, and what became of them (B-02, B-21).

docs/bug-audit-2026-09-28.md, design decision #54. A live entry writes its cycle row BEFORE
any order goes out (`detail.pending`), so a crash leaves a question rather than a position
nothing knows about. Two things made that question unanswerable:

* **The order ids were written late** -- only after `place_and_confirm` had waited for the
  fill and cancelled whatever rested. A crash in that window left a row with no ids.
* **A lost `place_order` answer was read as a refusal** (B-21). A transport error or a
  garbled body carries no refusal guarantee (#24): ICICI may have accepted the order.

And nothing kept the exit path off an unanswered row: a pending Long Scalper or Iron Fly row
was an open cycle, so a hard square-off sold its *planned* legs -- a naked short when the
buy never filled.

The rule this module keeps is:

    **A row is traded only after its outcome is known.** Until then it is a question the
    bot keeps asking the broker, and it places no order for it.

How the question gets answered:

* `Journal` writes each order onto the row as `detail.intents` just before it is sent, and
  its id the moment ICICI returns one. Unwind orders sent during an aborted entry are
  journaled too, so a crash mid-unwind nets out correctly.
* `locate_order` finds an order that has no id by reading the order book: same contract,
  side and quantity, placed within a window around the send. One match is the order; none
  means it never went in; two or more, an unreadable book or an unreadable order time is
  "unknown" -- never guessed.
* `resolve_pending` reads every journaled order (cancelling any still resting -- nothing is
  managing an entry from a pass that no longer exists), nets what each leg holds, and
  settles the row:
    - nothing held -> abandoned, as an unfilled entry;
    - the whole structure (or the Long Scalper's single leg, at any size) -> adopted
      through the bot's own entry recorder, with real entry prices and `entry_value`;
    - part of a multi-leg structure -> B-01's `unwinding` state holding only what filled,
      so the existing broker-checked, shorts-first, bounded close takes it from there;
    - still unknown -> left pending, re-asked on a back-off, one Telegram alert; after
      `MAX_RESOLVE_ATTEMPTS` more failures the bot is switched off, and the row keeps being
      re-asked every 5 minutes until the broker answers.

It runs at startup and on every bot pass. `placing()` marks a row whose orders are going out
in this process right now, so the resolver never answers a question that is still being
asked (CAS Bingo's manual route places from a request thread while its loop runs).

`user_remark` tagging is deliberately absent until a live check shows ICICI echoes it in the
order list; when it does, a tag match slots into `_matches` ahead of the time window.
"""
from __future__ import annotations

import datetime
import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Optional

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.timezone import IST
from icici_breeze_backend.app.domain.bots import ReasonCode
from icici_breeze_backend.app.services.bots.scalping import held_legs

_logger = logging.getLogger(__name__)

# Seconds before each re-check of a row that could not be settled. The last value repeats.
RESOLVE_BACKOFF_SECONDS = held_legs.RETRY_BACKOFF_SECONDS
# Failed re-checks after the first before the bot is switched off (about 9 minutes).
MAX_RESOLVE_ATTEMPTS = len(RESOLVE_BACKOFF_SECONDS)
# Wait before reading the order book for an order whose answer was lost, so an order ICICI
# accepted a moment ago has reached the book.
LOCATE_SETTLE_SECONDS = 2.0
# An order in the book matches a send placed this many seconds either side of it. Before
# covers clock skew between this host and ICICI; after covers a slow request.
MATCH_BEFORE_SECONDS = 60.0
MATCH_AFTER_SECONDS = 180.0
# Rows written by the build before journaling carry only the time the row was opened, and
# every leg's orders went out after it, one at a time.
LEGACY_MATCH_AFTER_SECONDS = 900.0

_KIND = "scalping_orphan"


# --------------------------------------------------------------------------------------
# Rows whose orders are going out right now
# --------------------------------------------------------------------------------------

_in_flight: set[str] = set()
_in_flight_lock = threading.Lock()


@contextmanager
def placing(cycle_id: str) -> Iterator[None]:
    with _in_flight_lock:
        _in_flight.add(str(cycle_id))
    try:
        yield
    finally:
        with _in_flight_lock:
            _in_flight.discard(str(cycle_id))


def in_flight(cycle_id: str) -> bool:
    with _in_flight_lock:
        return str(cycle_id) in _in_flight


# --------------------------------------------------------------------------------------
# The journal: every order on the row, before it is sent
# --------------------------------------------------------------------------------------


def intent_fields(leg: Any, price: float, sent_at: float) -> dict[str, Any]:
    """One order, as the journal stores it and the order book is searched for it."""
    return {
        "stock_code": str(leg.stock_code),
        "exchange_code": str(leg.exchange_code),
        "right": str(leg.right),
        "strike_price": float(leg.strike_price),
        "expiry_display": str(leg.expiry_display),
        "action": str(leg.action),
        "quantity": int(leg.quantity),
        "price": float(price),
        "sent_at": float(sent_at),
        "order_id": None,
    }


class Journal:
    """Writes a live cycle's orders onto its row as they go out.

    Mutates `cycle.detail` in place and persists it, so a caller that later builds its detail
    from `cycle.detail` keeps the journal. A write that fails raises: an order that could not
    be journaled must not be sent, and one sent but not journaled is still found by
    `locate_order`.
    """

    def __init__(self, cycle: Any) -> None:
        self._cycle = cycle
        detail = dict(cycle.detail or {})
        detail.setdefault("intents", [])
        cycle.detail = detail

    def sending(self, leg: Any, price: float, sent_at: float) -> int:
        intents = self._cycle.detail["intents"]
        intents.append(intent_fields(leg, price, sent_at))
        self._persist()
        return len(intents) - 1

    def placed(self, index: int, order_id: str) -> None:
        self._cycle.detail["intents"][index]["order_id"] = str(order_id)
        self._persist()

    def order_ids(self) -> list[str]:
        return [
            str(i["order_id"]) for i in self._cycle.detail.get("intents") or [] if i.get("order_id")
        ]

    def _persist(self) -> None:
        from icici_breeze_backend.app.repositories import bots as repo

        repo.update_cycle_detail(self._cycle.id, self._cycle.detail)


# --------------------------------------------------------------------------------------
# Finding an order that has no id
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Located:
    state: str  # "found" | "absent" | "unknown"
    order_id: Optional[str] = None
    why: str = ""


def _expiry_date(text: Any) -> Optional[datetime.date]:
    raw = str(text or "").strip()
    for fmt, part in (("%d-%b-%Y", raw), ("%Y-%m-%d", raw[:10])):
        try:
            return datetime.datetime.strptime(part, fmt).date()
        except ValueError:
            continue
    return None


def _order_time(text: Any) -> Optional[float]:
    """ICICI's `order_datetime` as an epoch, or None when it cannot be read.

    `DD-MON-YYYY HH:MM:SS` is the shape the GTT order book uses; the plain order list's
    shape is unverified from here, so the ISO shapes are accepted too. An unreadable time is
    never guessed: the caller treats the match as unknown.
    """
    raw = str(text or "").strip()
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            naive = datetime.datetime.strptime(raw[:19] if "T" in fmt else raw, fmt)
        except ValueError:
            continue
        return naive.replace(tzinfo=IST).timestamp()
    return None


def _matches(row: dict[str, Any], intent: dict[str, Any]) -> bool:
    """Same contract, side and quantity."""
    if str(row.get("stock_code") or "").strip().upper() != str(intent.get("stock_code") or "").upper():
        return False
    right = str(row.get("right") or "").strip().lower()[:1]
    if right != str(intent.get("right") or "").lower()[:1]:
        return False
    if str(row.get("action") or "").strip().lower() != str(intent.get("action") or "").lower():
        return False
    try:
        if abs(float(row.get("strike_price") or 0) - float(intent.get("strike_price") or 0)) > 0.001:
            return False
        if int(float(row.get("quantity") or 0)) != int(intent.get("quantity") or 0):
            return False
    except (TypeError, ValueError):
        return False
    want, have = _expiry_date(intent.get("expiry_display")), _expiry_date(row.get("expiry_date"))
    if want is not None and have is not None:
        return want == have
    return str(row.get("expiry_date") or "").strip().lower() == str(
        intent.get("expiry_display") or ""
    ).strip().lower()


def locate_order(
    proc: Any,
    user_id: str,
    intent: dict[str, Any],
    *,
    claimed: set[str] = frozenset(),  # type: ignore[assignment]
    after_seconds: float = MATCH_AFTER_SECONDS,
) -> Located:
    """The order this intent sent, looked up in the day's order book.

    `claimed` holds ids already known to belong to something else -- another attempt, another
    cycle -- so they can never be mistaken for this send. One `get_order_list` call.
    """
    sent_at = float(intent.get("sent_at") or 0)
    exchange = str(intent.get("exchange_code") or cfg.NFO)
    day = datetime.datetime.fromtimestamp(sent_at, IST).date().isoformat()
    try:
        response = proc.get_orders(user_id, day, day, exchange_codes=[exchange])
    except Exception:  # noqa: BLE001 -- unreadable is an answer of its own, not a crash
        _logger.warning("order_intents: order book read failed", exc_info=True)
        return Located("unknown", why="the order book could not be read")
    if not isinstance(response, dict) or response.get("Status") != 200:
        return Located("unknown", why="the order book could not be read")

    in_window: list[str] = []
    untimed = 0
    for row in response.get("Success") or []:
        if not isinstance(row, dict):
            continue
        order_id = str(row.get("order_id") or "")
        if not order_id or order_id in claimed or not _matches(row, intent):
            continue
        placed_at = _order_time(row.get("order_datetime"))
        if placed_at is None:
            untimed += 1
        elif sent_at - MATCH_BEFORE_SECONDS <= placed_at <= sent_at + after_seconds:
            in_window.append(order_id)
    if untimed:
        return Located(
            "unknown", why=f"{untimed} matching order(s) carry a time that could not be read"
        )
    if not in_window:
        return Located("absent")
    if len(in_window) > 1:
        return Located(
            "unknown", why=f"{len(in_window)} identical orders were placed around that time"
        )
    return Located("found", order_id=in_window[0])


# --------------------------------------------------------------------------------------
# Settling a pending row
# --------------------------------------------------------------------------------------


@dataclass
class RecoveredLeg:
    """What one planned leg holds after every journaled order has been read."""

    leg: dict[str, Any]
    quantity: int
    price: float  # average of the fills that opened it
    order_ids: list[str] = field(default_factory=list)

    @property
    def is_short(self) -> bool:
        return self.leg.get("action") == cfg.SELL


AdoptFn = Callable[[Any, list[RecoveredLeg]], None]


def resolve_pending(
    proc: Any,
    user_id: str,
    bot_type: str,
    *,
    adopt: AdoptFn,
    charges: Any,
    now: Optional[float] = None,
    force: bool = False,
) -> int:
    """Settle this bot's pending rows that are due. Returns how many were settled.

    `force` ignores the back-off: a restart is new information, and the first question after
    one should not wait. Never raises.
    """
    from icici_breeze_backend.app.repositories import bots as repo

    now = time.time() if now is None else now
    settled = 0
    try:
        pending = repo.pending_cycles(user_id, bot_type)
    except Exception:  # noqa: BLE001
        _logger.exception("order_intents: could not list pending rows for %s", bot_type)
        return 0
    for cycle in pending:
        if in_flight(cycle.id):
            continue
        if not force and now < float((cycle.detail or {}).get("resolve_next_at") or 0):
            continue
        try:
            if _resolve_one(proc, user_id, bot_type, cycle, adopt=adopt, charges=charges, now=now):
                settled += 1
        except Exception:  # noqa: BLE001 -- one bad row must not stop the bot's pass
            _logger.exception("order_intents: could not settle cycle %s", cycle.id)
    return settled


def _terminal(state: dict[str, Any], quantity: int) -> bool:
    from icici_breeze_backend.app.services.bots.scalping import live

    status = str(state.get("status") or "").lower()
    return (
        int(state.get("executed") or 0) >= quantity
        or status in live._FILLED
        or status in live._DEAD
    )


def _settled_state(
    proc: Any, user_id: str, order_id: str, exchange: str, quantity: int
) -> dict[str, Any] | str:
    """The order's final state, or why it cannot be had yet.

    An order still resting is cancelled first: it is an entry from a pass that no longer
    exists, and nothing would manage what it filled into.
    """
    from icici_breeze_backend.app.services.bots.scalping import live

    state = live._rest_order_state(proc, user_id, order_id, exchange)
    if not state:
        return f"the broker did not answer for order {order_id}"
    if _terminal(state, quantity):
        return state
    if not live.cancel(proc, user_id, order_id, exchange):
        return f"order {order_id} is still open and could not be cancelled"
    state = live._rest_order_state(proc, user_id, order_id, exchange)
    if not state or not _terminal(state, quantity):
        return f"order {order_id} was cancelled but its final state is not confirmed yet"
    return state


def _opened_epoch(opened_at: Any) -> float:
    try:
        naive = datetime.datetime.strptime(str(opened_at)[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return time.time()
    return naive.replace(tzinfo=IST).timestamp()


def _intents_of(cycle: Any) -> tuple[list[dict[str, Any]], bool]:
    """The row's journal, or -- for a row written before journaling -- one search per planned
    leg from the time the row was opened. Returns (intents, legacy)."""
    detail = cycle.detail or {}
    if "intents" in detail:
        return [dict(i) for i in detail.get("intents") or []], False
    opened = _opened_epoch(getattr(cycle, "opened_at", None))
    intents = []
    for leg in cycle.legs or []:
        if int(leg.get("quantity") or 0) <= 0:
            continue
        intents.append({
            "stock_code": str(leg.get("stock_code") or ""),
            "exchange_code": str(leg.get("exchange_code") or cfg.NFO),
            "right": str(leg.get("right") or "call"),
            "strike_price": float(leg.get("strike_price") or 0),
            "expiry_display": str(leg.get("expiry_display") or ""),
            "action": str(leg.get("action") or cfg.BUY),
            "quantity": int(leg.get("quantity") or 0),
            "price": 0.0,
            "sent_at": opened,
            "order_id": None,
        })
    return intents, True


def _ids_in(value: Any) -> set[str]:
    out: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "order_id" and item:
                out.add(str(item))
            elif key == "order_ids" and isinstance(item, list):
                out.update(str(x) for x in item if x)
            else:
                out |= _ids_in(item)
    elif isinstance(value, list):
        for item in value:
            out |= _ids_in(item)
    return out


def _claimed_elsewhere(user_id: str, cycle_id: str) -> set[str]:
    """Order ids any other recent bot cycle of this user already accounts for."""
    from icici_breeze_backend.app.repositories import bots as repo

    out: set[str] = set()
    for other in repo.list_cycles(user_id, limit=200):
        if other.id != cycle_id:
            out |= _ids_in(other.detail or {})
    return out


def _leg_key(item: dict[str, Any]) -> tuple[str, float]:
    return (str(item.get("right") or "call"), float(item.get("strike_price") or 0))


def _resolve_one(
    proc: Any, user_id: str, bot_type: str, cycle: Any, *, adopt: AdoptFn, charges: Any,
    now: float,
) -> bool:
    from icici_breeze_backend.app.repositories import bots as repo

    detail = dict(cycle.detail or {})
    intents, legacy = _intents_of(cycle)
    problems: list[str] = []
    if legacy and detail.get("order_ids"):
        problems.append("order ids were recorded without the legs they belong to")
        intents = []

    claimed = _claimed_elsewhere(user_id, cycle.id) | {
        str(i["order_id"]) for i in intents if i.get("order_id")
    }
    after = LEGACY_MATCH_AFTER_SECONDS if legacy else MATCH_AFTER_SECONDS
    # Per (right, strike): units bought minus units sold, and what the opening fills cost.
    net: dict[tuple[str, float], int] = {}
    bought: dict[tuple[str, float], list[float]] = {}  # [units, rupees]
    sold: dict[tuple[str, float], list[float]] = {}
    ids: dict[tuple[str, float], list[str]] = {}
    for intent in intents:
        order_id = str(intent.get("order_id") or "")
        quantity = int(intent.get("quantity") or 0)
        exchange = str(intent.get("exchange_code") or cfg.NFO)
        if not order_id:
            found = locate_order(proc, user_id, intent, claimed=claimed, after_seconds=after)
            if found.state == "unknown":
                problems.append(f"{_describe(intent)}: {found.why}")
                continue
            if found.state == "absent":
                continue
            order_id = str(found.order_id)
            claimed.add(order_id)
            intent["order_id"] = order_id
            intent["located"] = True
        state = _settled_state(proc, user_id, order_id, exchange, quantity)
        if isinstance(state, str):
            problems.append(f"{_describe(intent)}: {state}")
            continue
        executed = int(state.get("executed") or 0)
        if executed <= 0:
            continue
        try:
            price = float(state.get("price") or intent.get("price") or 0.0)
        except (TypeError, ValueError):
            price = float(intent.get("price") or 0.0)
        key = _leg_key(intent)
        buy = str(intent.get("action") or "").lower() == str(cfg.BUY).lower()
        net[key] = net.get(key, 0) + (executed if buy else -executed)
        book = bought if buy else sold
        units_cost = book.setdefault(key, [0.0, 0.0])
        units_cost[0] += executed
        units_cost[1] += price * executed
        ids.setdefault(key, []).append(order_id)

    if not legacy:
        detail["intents"] = intents

    planned = [l for l in cycle.legs or [] if int(l.get("quantity") or 0) > 0]
    by_key = {_leg_key(l): l for l in planned}
    fills: list[RecoveredLeg] = []
    for key, units in net.items():
        if units == 0:
            continue
        leg = by_key.get(key)
        if leg is None:
            problems.append(f"{key[0]} {int(key[1])} filled but is not a leg of this entry")
            continue
        long_leg = leg.get("action") != cfg.SELL
        held = units if long_leg else -units
        if held < 0:
            problems.append(f"{key[0]} {int(key[1])} is held the opposite way to the plan")
            continue
        opening = (bought if long_leg else sold).get(key, [0.0, 0.0])
        price = opening[1] / opening[0] if opening[0] else 0.0
        fills.append(RecoveredLeg(leg=dict(leg), quantity=held, price=round(price, 2),
                                  order_ids=list(ids.get(key) or [])))

    if problems:
        _still_unresolved(user_id, bot_type, cycle, detail, problems, now=now)
        return False

    detail["reconciled"] = True
    alerted = bool(detail.get("resolve_alerted"))
    fills.sort(key=lambda f: planned.index(by_key[_leg_key(f.leg)]))
    if not fills:
        repo.abandon_cycle(
            cycle.id,
            reason_code=ReasonCode.ENTRY_UNFILLED,
            reason_text="Interrupted before the order filled; nothing was traded.",
        )
        _logger.warning("order_intents: cycle %s -- nothing filled; abandoned", cycle.id)
        if alerted:
            _alert_nothing_traded(user_id, bot_type, disarmed=bool(detail.get("resolve_disarmed")))
        return True

    complete = len(planned) <= 1 or (
        len(fills) == len(planned)
        and all(f.quantity == int(f.leg.get("quantity") or 0) for f in fills)
    )
    cycle.detail = detail
    if complete:
        adopt(cycle, fills)
        _logger.warning("order_intents: cycle %s -- adopted %s", cycle.id, _summary(fills))
        _alert_adopted(user_id, bot_type, fills)
    else:
        _start_unwind(cycle, detail, fills, charges)
        _logger.warning(
            "order_intents: cycle %s -- partial entry %s; unwinding", cycle.id, _summary(fills)
        )
        _alert_unwinding(user_id, bot_type, fills)
    return True


def _start_unwind(
    cycle: Any, detail: dict[str, Any], fills: list[RecoveredLeg], charges: Any
) -> None:
    """Hand a partial structure to B-01's close machinery, holding only what filled."""
    from icici_breeze_backend.app.repositories import bots as repo

    exchange = str(fills[0].leg.get("exchange_code") or cfg.NFO)
    detail = dict(detail)
    detail.pop("pending", None)
    detail["order_ids"] = sorted(_ids_in(detail.get("intents") or []))
    detail["entry_fills"] = [
        {
            "right": f.leg.get("right"), "strike": float(f.leg.get("strike_price") or 0),
            "action": f.leg.get("action"), "price": f.price, "quantity": f.quantity,
            "order_ids": f.order_ids,
        }
        for f in fills
    ]
    detail["entry_charges"] = round(
        sum(
            charges.leg_charges(f.price, f.quantity, is_buy=not f.is_short, exchange_code=exchange)
            for f in fills
        ),
        2,
    )
    detail.update({
        "unwinding": True, "unwind_attempts": 0, "unwind_next_at": 0,
        "unwind_from_recovery": True,
    })
    held_legs.remember_exit(
        detail,
        ReasonCode.ENTRY_PARTIAL_UNWOUND,
        "Interrupted mid-entry with only part of the structure filled; the legs that filled "
        "were closed.",
    )
    legs = [held_legs.resized(f.leg, f.quantity, entry=f.price) for f in fills]
    repo.update_cycle_holdings(cycle.id, legs, detail)


def stand_down(
    user_id: str,
    bot_type: str,
    cycle: Any,
    why: str,
    *,
    cancel_failed: bool = False,
    now: Optional[float] = None,
) -> None:
    """Freeze an entry whose last order cannot be accounted for.

    Called in place of any unwind: unwinding the other legs around an order that may have
    filled is how a wing gets sold under a short. The row stays pending with its journal;
    `resolve_pending` takes it from here on the back-off.
    """
    detail = dict(cycle.detail or {})
    detail["pending"] = True
    if cancel_failed:
        detail["cancel_failed"] = True
    _still_unresolved(
        user_id, bot_type, cycle, detail, [why], now=time.time() if now is None else now,
    )


def _still_unresolved(
    user_id: str, bot_type: str, cycle: Any, detail: dict[str, Any], problems: list[str],
    *, now: float,
) -> None:
    from icici_breeze_backend.app.repositories import bots as repo

    attempts = int(detail.get("resolve_attempts") or 0) + 1
    why = "; ".join(problems)
    detail["resolve_attempts"] = attempts
    detail["resolve_problem"] = why
    detail["resolve_next_at"] = now + RESOLVE_BACKOFF_SECONDS[
        min(attempts, len(RESOLVE_BACKOFF_SECONDS)) - 1
    ]
    _logger.error(
        "order_intents: cycle %s (%s) still unsettled after %d check(s) -- %s",
        cycle.id, bot_type, attempts, why,
    )
    if not detail.get("resolve_alerted"):
        _alert_unresolved(user_id, bot_type, why)
        detail["resolve_alerted"] = True
    if attempts > MAX_RESOLVE_ATTEMPTS and not detail.get("resolve_disarmed"):
        _disarm(user_id, bot_type, why, attempts)
        detail["resolve_disarmed"] = True
    repo.update_cycle_detail(cycle.id, detail)
    cycle.detail = detail


def _disarm(user_id: str, bot_type: str, why: str, attempts: int) -> None:
    from icici_breeze_backend.app.services.bots.scalping import guards

    if not guards.switch_off(user_id, bot_type, f"an entry order cannot be accounted for: {why}"):
        return
    _send(
        user_id,
        f"\U0001f6d1 *{_label(bot_type)} switched off*\n\n"
        f"After {attempts} checks the bot still cannot account for an entry order: {why}.\n\n"
        "It keeps checking every 5 minutes and will settle the entry as soon as the Order "
        "Book answers, but it will not trade again until you switch it back on.",
    )


# --------------------------------------------------------------------------------------
# Telegram
# --------------------------------------------------------------------------------------


def _label(bot_type: str) -> str:
    from icici_breeze_backend.app.services.telegram_alerts import _BOT_LABEL

    return _BOT_LABEL.get(bot_type, bot_type)


def _describe(intent: dict[str, Any]) -> str:
    side = "buy" if str(intent.get("action") or "").lower() == str(cfg.BUY).lower() else "sell"
    return (
        f"{side} {intent.get('right')} {int(float(intent.get('strike_price') or 0))} "
        f"x{int(intent.get('quantity') or 0)}"
    )


def _summary(fills: list[RecoveredLeg]) -> str:
    return "; ".join(
        f"{'short' if f.is_short else 'long'} {f.leg.get('right')} "
        f"{int(float(f.leg.get('strike_price') or 0))} x{f.quantity} @ {f.price:.2f}"
        for f in fills
    )


def _send(user_id: str, text: str) -> None:
    from icici_breeze_backend.app.services.telegram_alerts import _notify

    try:
        _notify(user_id, text, kind=_KIND)
    except Exception:  # noqa: BLE001 -- an alert failure must not undo what was settled
        _logger.exception("order_intents: could not send an alert")


def _alert_unresolved(user_id: str, bot_type: str, why: str) -> None:
    _send(
        user_id,
        f"⚠️ *{_label(bot_type)} needs checking*\n\n"
        f"The bot cannot tell yet what became of an entry order: {why}.\n\n"
        "It will not open anything new until that is settled, and it will not place any "
        "order for that entry. It re-checks the Order Book by itself after 30 seconds, then "
        "1, 2 and 5 minutes; if it still cannot tell, it switches itself off.\n\n"
        "*Check the Order Book for an unexpected order or position.*",
    )


def _alert_adopted(user_id: str, bot_type: str, fills: list[RecoveredLeg]) -> None:
    _send(
        user_id,
        f"✅ *{_label(bot_type)}: interrupted entry recovered*\n\n"
        f"An entry was interrupted, and the Order Book shows it filled: {_summary(fills)}.\n\n"
        "The bot is now managing it with its normal exits.",
    )


def _alert_unwinding(user_id: str, bot_type: str, fills: list[RecoveredLeg]) -> None:
    _send(
        user_id,
        f"⚠️ *{_label(bot_type)}: interrupted entry is being closed*\n\n"
        f"An entry was interrupted with only part of it filled: {_summary(fills)}.\n\n"
        "That is not a structure the bot trades, so it is closing those legs now, shorts "
        "first, checking your positions before each try. You will get a message when they "
        "are closed.",
    )


def _alert_nothing_traded(user_id: str, bot_type: str, *, disarmed: bool) -> None:
    tail = (
        "The bot stays switched off until you switch it back on."
        if disarmed
        else "The bot can trade again."
    )
    _send(
        user_id,
        f"✅ *{_label(bot_type)}: nothing was traded*\n\n"
        f"The entry order it could not account for never filled, so no position was "
        f"opened. {tail}",
    )


def reset_state_for_tests() -> None:
    with _in_flight_lock:
        _in_flight.clear()
