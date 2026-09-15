"""Arm a bot position's PB/SL the moment its entry orders are done, driven by the order feed.

Why this exists
---------------
Bot 2 used to arm its stop straight after `place_short_legs` returned. The arm guard
(`strategy_group_arm_guard`) refuses while any order for the expiry is still working -- it
has to, it is what stops a re-arm stacking duplicate exits -- and a limit order a few
seconds old, freeze-sliced into nine pieces per leg, is almost never all filled yet. So on a
real-sized expiry-day trade the arm failed nearly every time, nothing tried again, and the
position sat open with no automatic exit (NIFTY expiry, 15-Sep-2026: 14,885-qty strangle).

How it decides when to arm
--------------------------
* **The WS order feed is the signal.** ICICI reports every order on the account-wide feed:
  an acknowledgement ("Ordered") as soon as the exchange accepts it, each fill, and the
  terminal state. When every one of the bot's own orders is terminal, the arm is attempted.
  That attempt runs the arm guard, which reads the REST order book on purpose (see its
  docstring) -- it is the arm's own broker call, not polling.
* **The listener records every F&O order event, not only the ones already being waited on.**
  The orders go out before a pending exit can be registered for them, and a quick fill's
  events would otherwise land in that gap and be lost.
* **REST is otherwise touched only when the feed looks broken**, at most once per
  `REST_BACKSTOP_SECONDS` per position:
    - an order the feed has not acknowledged `ACK_GRACE_SECONDS` after it went out -- the
      ack always comes, so its absence means a deaf order socket, not a resting order;
    - the WebSocket is down, or nothing at all has arrived on it for that long in market
      hours;
    - the process restarted while waiting, so events during the gap reached nobody.
  A limit order resting on a healthy feed costs nothing: silence after an ack is the order
  resting, and the feed will say when that changes.
* **The feed can run a beat ahead of the REST order book**, and an unrelated live order on
  the same expiry also blocks the guard. So a refused attempt after the feed said "done" is
  retried briefly, then waits for the next order event on that scrip+expiry, with the
  backstop cadence behind it.

It never cancels anything. An unfilled leg keeps working until it fills, the user cancels
it, or the exchange expires it -- and each of those is an order event that re-triggers the
arm. Cancelling the unfilled rest is therefore how a user gets protection *now*, and the
approval reply says so.

Single worker thread. The listener runs on the SDK's WS callback thread, where a broker call
or a SQLite write would stall tick ingestion for everyone, so it only records and wakes.
"""
from __future__ import annotations

import datetime
import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Optional

from icici_breeze_backend.app.core.timezone import now_ist
from icici_breeze_backend.app.domain.bots import ReasonCode
from icici_breeze_backend.app.repositories import bots as repo

_logger = logging.getLogger(__name__)

# ICICI acknowledges an accepted order on the feed within a second or two. Two minutes of
# silence is not latency.
ACK_GRACE_SECONDS = 120.0
# The fewest seconds between REST checks for one position while the feed looks broken.
REST_BACKSTOP_SECONDS = 120.0
# After the feed says every order is done but the guard still refuses: the REST book lagging
# the feed settles in seconds, so try twice quickly before falling back to the backstop.
_QUICK_RETRY_SECONDS = (10.0, 30.0)
# How often the worker re-checks without being woken -- the resolution of the timers above,
# not a polling interval (a pass that has nothing due makes no broker call).
_TICK_SECONDS = 5.0
# Nothing can fill after the close, so a stop still waiting then will never arm.
_MARKET_CLOSE = datetime.time(15, 30)
_ORDER_MEMORY = 5000

_LIVE_STATUSES = {"requested", "queued", "ordered", "partially executed", "freezed"}


@dataclass
class _OrderState:
    status: str
    executed: int
    total: int
    stock_code: str
    expiry_display: str


@dataclass
class _Pending:
    id: str
    user_id: str
    bot_type: str
    run_id: Optional[str]
    stock_code: str
    exchange_code: str
    expiry_display: str
    order_ids: list[str]
    terms: dict[str, Any]
    registered_at: float
    alerted: bool = False
    # Loaded from the database after a restart: the feed's events for these orders went
    # nowhere, so the first check has to be REST.
    resumed: bool = False
    last_rest_at: Optional[float] = None
    next_attempt_at: float = 0.0
    quick_retries: int = 0
    created_date: datetime.date = field(default_factory=lambda: now_ist().date())


_lock = threading.Lock()
_orders: "OrderedDict[str, _OrderState]" = OrderedDict()
_pending: dict[str, _Pending] = {}
_wake = threading.Event()
_stop = threading.Event()
_thread: Optional[threading.Thread] = None
_listening = False


def _label(stock_code: str) -> str:
    from icici_breeze_backend.app.services.bots.expiry_index_writer import INDEX_LABEL

    return INDEX_LABEL.get(stock_code, stock_code)


def _is_terminal(status: str) -> bool:
    return bool(status) and status not in _LIVE_STATUSES


def _same_expiry(a: str, b: str) -> bool:
    from icici_breeze_backend.app.services.reference_data.scrip_master_sql import (
        normalize_expiry_display,
    )

    try:
        return normalize_expiry_display(str(a or "")) == normalize_expiry_display(str(b or ""))
    except (ValueError, TypeError):
        return str(a or "").strip().lower() == str(b or "").strip().lower()


# --------------------------------------------------------------------------------------
# The feed
# --------------------------------------------------------------------------------------


def record_order_state(
    order_id: str,
    *,
    status: str,
    executed: int,
    total: int,
    stock_code: str = "",
    expiry_display: str = "",
) -> None:
    """Remember an order's latest state and wake any position waiting on it.

    Wakes on the bot's own orders, and also on any order for the same scrip+expiry reaching
    a terminal state -- an unrelated working order blocks the guard too, and its end is
    exactly when a refused arm can succeed.
    """
    state = _OrderState(
        status=str(status or "").strip().lower(),
        executed=int(executed or 0),
        total=int(total or 0),
        stock_code=str(stock_code or "").strip().upper(),
        expiry_display=str(expiry_display or ""),
    )
    oid = str(order_id)
    woke = False
    with _lock:
        _orders[oid] = state
        _orders.move_to_end(oid)
        while len(_orders) > _ORDER_MEMORY:
            _orders.popitem(last=False)
        for p in _pending.values():
            mine = oid in p.order_ids
            neighbour = (
                _is_terminal(state.status)
                and state.stock_code == p.stock_code
                and _same_expiry(state.expiry_display, p.expiry_display)
            )
            if mine or neighbour:
                p.next_attempt_at = 0.0
                p.quick_retries = 0
                woke = True
    if woke:
        _wake.set()


def _on_notification(note: Any) -> None:
    """Order-feed listener. Never raises: it runs on the shared WS callback thread."""
    try:
        record_order_state(
            str(getattr(note, "order_id", "") or ""),
            status=str(getattr(note, "status", "") or ""),
            executed=int(getattr(note, "executed_quantity", 0) or 0),
            total=int(getattr(note, "total_quantity", 0) or 0),
            stock_code=str(getattr(note, "stock_code", "") or ""),
            expiry_display=str(getattr(note, "expiry_display", "") or ""),
        )
    except Exception:  # noqa: BLE001
        _logger.debug("exit arming: order notification not understood", exc_info=True)


def ensure_listening() -> None:
    global _listening
    if _listening:
        return
    from icici_breeze_backend.app.services import ws_tick_pipeline

    ws_tick_pipeline.register_order_notification_listener(_on_notification)
    _listening = True


def prepare(proc: Any, user_id: str) -> None:
    """Call BEFORE the first order goes out: listen, and make sure the order socket is up.

    An acknowledgement that arrives before anyone is listening is lost, and a lost ack is
    what the malfunction check reads as a deaf feed -- a wasted REST call at best.
    """
    ensure_listening()
    try:
        from icici_breeze_backend.app.services.breeze_websocket_manager import (
            ensure_order_feed,
        )

        ensure_order_feed(proc, user_id)
    except Exception:  # noqa: BLE001 -- the REST backstop covers a feed that will not start
        _logger.warning("exit arming: could not bring up the order feed", exc_info=True)


def fill_state(order_ids: list[str]) -> dict[str, Any]:
    """What the feed has said about these orders so far."""
    with _lock:
        states = [_orders.get(str(oid)) for oid in order_ids]
    heard = [s for s in states if s is not None]
    return {
        "heard": bool(order_ids) and len(heard) == len(order_ids),
        "terminal": bool(order_ids)
        and len(heard) == len(order_ids)
        and all(_is_terminal(s.status) for s in heard),
        "executed": sum(s.executed for s in heard),
        "total": sum(s.total for s in heard),
    }


def orders_settled(order_ids: list[str]) -> bool:
    """True when the feed has reported every one of these orders as done."""
    return bool(fill_state(order_ids)["terminal"])


# --------------------------------------------------------------------------------------
# Registering a position to arm later
# --------------------------------------------------------------------------------------


def pending_note(stock_code: str) -> str:
    """The run-log suffix for a stop still waiting. `exit_arming` rewrites it in place."""
    return f" — {_label(stock_code)} stop arms once every order fills"


def _armed_note(stock_code: str) -> str:
    return f" — {_label(stock_code)} stop armed at {now_ist().strftime('%H:%M')}"


def wait_then_arm(
    *,
    user_id: str,
    bot_type: str,
    run_id: Optional[str],
    stock_code: str,
    exchange_code: str,
    expiry_display: str,
    order_ids: list[str],
    terms: dict[str, Any],
    alerted: bool = False,
) -> Optional[str]:
    """Register a position to arm once its orders are done. Returns the pending id.

    Persisted first, so a restart resumes it. A persistence failure still leaves it waiting
    in memory: losing durability is bad, not arming at all is worse.
    """
    ensure_listening()
    pending_id: Optional[str] = None
    try:
        pending_id = repo.create_pending_exit(
            user_id=user_id,
            bot_type=bot_type,
            run_id=run_id,
            stock_code=stock_code,
            exchange_code=exchange_code,
            expiry_display=expiry_display,
            order_ids=list(order_ids),
            terms=terms,
        )
        if alerted:
            repo.update_pending_exit(pending_id, alerted=1)
    except Exception:  # noqa: BLE001
        _logger.exception("exit arming: could not persist pending exit for %s", stock_code)
    if pending_id is None:
        import uuid

        pending_id = f"mem-{uuid.uuid4()}"
    with _lock:
        _pending[pending_id] = _Pending(
            id=pending_id,
            user_id=user_id,
            bot_type=bot_type,
            run_id=run_id,
            stock_code=stock_code,
            exchange_code=exchange_code,
            expiry_display=expiry_display,
            order_ids=[str(o) for o in order_ids],
            terms=dict(terms),
            registered_at=time.monotonic(),
            alerted=alerted,
        )
    _wake.set()
    return pending_id


def current_status(pending_id: Optional[str]) -> Optional[str]:
    """"waiting" | "armed" | "failed" | ... for a registered position, or None."""
    if not pending_id:
        return None
    with _lock:
        if pending_id in _pending:
            return "waiting"
    try:
        row = repo.get_pending_exit(pending_id)
    except Exception:  # noqa: BLE001
        return None
    return str(row["status"]) if row else None


# --------------------------------------------------------------------------------------
# The worker
# --------------------------------------------------------------------------------------


def _feed_malfunction(p: _Pending, now: float) -> Optional[str]:
    """Why the feed cannot be trusted for this position right now, or None if it can."""
    if p.resumed:
        return "the app restarted while this stop was waiting"
    with _lock:
        unheard = [oid for oid in p.order_ids if oid not in _orders]
    if unheard and now - p.registered_at >= ACK_GRACE_SECONDS:
        return f"{len(unheard)} order(s) never acknowledged on the order feed"
    if now - p.registered_at < ACK_GRACE_SECONDS:
        # Inside the grace window nothing below can be judged fairly yet.
        return None
    try:
        from icici_breeze_backend.app.services.breeze_websocket_manager import (
            current_ws_user_id,
        )

        if current_ws_user_id() is None:
            return "the WebSocket is not connected"
        from icici_breeze_backend.app.services.market_calendar import is_market_open
        from icici_breeze_backend.app.services.ws_tick_pipeline import last_tick_age_seconds

        age = last_tick_age_seconds()
        if age is not None and age > ACK_GRACE_SECONDS and is_market_open():
            return f"nothing has arrived on the WebSocket for {age:.0f}s"
    except Exception:  # noqa: BLE001 -- unable to tell is itself a reason to check
        _logger.debug("exit arming: feed health check failed", exc_info=True)
        return "the feed's health could not be read"
    return None


def _close(p: _Pending, status: str, *, rule_id: Optional[str] = None,
           error: Optional[str] = None) -> None:
    with _lock:
        _pending.pop(p.id, None)
    if p.id.startswith("mem-"):
        return
    try:
        repo.update_pending_exit(p.id, status=status, rule_id=rule_id, last_error=error)
    except Exception:  # noqa: BLE001
        _logger.exception("exit arming: could not record %s for %s", status, p.id)


def _revise_run(p: _Pending, *, note: str, settle_code: Optional[str],
                settle_status: Optional[str]) -> None:
    """Replace this index's "stop arms once..." note in its run with how it ended.

    The run's status and code change only if they still say "pending" and nothing else on
    the run is still waiting -- a run that failed for another reason stays failed.
    """
    if not p.run_id:
        return
    try:
        run = repo.get_run(p.run_id)
        if run is None:
            return
        text = str(run.get("reason_text") or "")
        waiting = pending_note(p.stock_code)
        text = text.replace(waiting, note) if waiting in text else f"{text}{note}"
        status = str(run.get("status") or "")
        code = str(run.get("reason_code") or "")
        with _lock:
            others = any(
                q.run_id == p.run_id and q.id != p.id for q in _pending.values()
            )
        if code == ReasonCode.EXIT_ARM_PENDING and not others and settle_code:
            code = settle_code
            status = settle_status or status
        repo.revise_finished_run(p.run_id, status=status, reason_code=code, reason_text=text)
    except Exception:  # noqa: BLE001
        _logger.exception("exit arming: could not revise run %s", p.run_id)


def _notify(user_id: str, text: str) -> None:
    try:
        from icici_breeze_backend.app.services.telegram_alerts import notify_bot_exit_update

        notify_bot_exit_update(user_id, text)
    except Exception:  # noqa: BLE001
        _logger.exception("exit arming: telegram update failed")


def _armed_message(p: _Pending) -> str:
    terms = p.terms
    lines = [f"🛡 *Stop armed* — {_label(p.stock_code)} {p.expiry_display}", ""]
    loss = float(terms.get("loss_limit") or 0)
    multiple = terms.get("loss_multiple")
    lines.append(
        f"Loss limit ₹{loss:,.0f}"
        + (f" ({float(multiple):g}× the premium collected)" if multiple else "")
    )
    target = terms.get("target_option_price")
    if target:
        lines.append(f"Books profit once every leg trades at or below ₹{float(target):g}")
    else:
        lines.append("No profit target — held to expiry unless the stop fires")
    lines += ["", "Automatic exit is live."]
    return "\n".join(lines)


def _attempt(p: _Pending, proc: Any, now: float, *, via: str) -> None:
    from icici_breeze_backend.app.services.bots import expiry_index_writer as bot2
    from icici_breeze_backend.app.services.strategy_group_arm_guard import (
        ArmPreconditionError,
    )

    try:
        rule_id = bot2.arm_exit_rule(
            proc,
            p.user_id,
            stock_code=p.stock_code,
            exchange_code=p.exchange_code,
            expiry_display=p.expiry_display,
            terms=p.terms,
        )
    except ArmPreconditionError as e:
        # Still something working (ours per the REST book, or an unrelated order), or the
        # book could not be read. Neither is a reason to alert -- the approval reply already
        # told the user the stop is waiting.
        if via == "feed" and p.quick_retries < len(_QUICK_RETRY_SECONDS):
            p.next_attempt_at = now + _QUICK_RETRY_SECONDS[p.quick_retries]
            p.quick_retries += 1
        else:
            p.next_attempt_at = now + REST_BACKSTOP_SECONDS
        _logger.info("exit arming: %s not armed yet (%s): %s", p.stock_code, via, e.message)
        if not p.id.startswith("mem-"):
            try:
                repo.update_pending_exit(p.id, last_error=e.message)
            except Exception:  # noqa: BLE001
                pass
        return
    except Exception as e:  # noqa: BLE001
        _logger.exception("exit arming: arm failed for %s", p.stock_code)
        p.next_attempt_at = now + REST_BACKSTOP_SECONDS
        if not p.alerted:
            p.alerted = True
            if not p.id.startswith("mem-"):
                try:
                    repo.update_pending_exit(p.id, alerted=1, last_error=str(e))
                except Exception:  # noqa: BLE001
                    pass
            _notify(
                p.user_id,
                f"🚨 *Stop NOT armed* — {_label(p.stock_code)} {p.expiry_display}: {e}\n\n"
                "The position is open with no automatic exit. Retrying every 2 minutes; "
                "set PB/SL yourself in Portfolio if you would rather not wait.",
            )
        return

    _close(p, "armed", rule_id=rule_id)
    _revise_run(
        p,
        note=_armed_note(p.stock_code),
        settle_code=ReasonCode.ORDERS_PLACED,
        settle_status="completed",
    )
    _logger.info("exit arming: %s stop armed (rule %s, via %s)", p.stock_code, rule_id, via)
    _notify(p.user_id, _armed_message(p))


def _evaluate_one(p: _Pending, proc: Any, now: float) -> None:
    # The caller that placed the orders is still writing the run's first verdict. Acting
    # now would race it: our revision could land first and be overwritten by "pending".
    if p.run_id and repo.run_status(p.run_id) == "running":
        return

    today = now_ist()
    if today.date() > p.created_date or today.time() >= _MARKET_CLOSE:
        _close(p, "abandoned", error="market closed before every order finished")
        _revise_run(
            p,
            note=f" — {_label(p.stock_code)} stop NEVER armed (market closed first)",
            settle_code=ReasonCode.EXIT_ARM_FAILED,
            settle_status="failed",
        )
        _notify(
            p.user_id,
            f"⚠️ *Stop never armed* — {_label(p.stock_code)} {p.expiry_display}: the "
            "market closed with an order still working, so the stop could not be armed. "
            "Check the position in Portfolio.",
        )
        return

    state = fill_state(p.order_ids)
    if state["terminal"]:
        if state["executed"] <= 0:
            _close(p, "nothing_filled")
            _revise_run(
                p,
                note=f" — no {_label(p.stock_code)} order filled, so no stop was needed",
                settle_code=ReasonCode.ORDER_REJECTED,
                settle_status="failed",
            )
            _notify(
                p.user_id,
                f"ℹ️ *No stop needed* — none of the bot's {_label(p.stock_code)} orders "
                "filled, so there is no position to protect.",
            )
            return
        if now >= p.next_attempt_at:
            _attempt(p, proc, now, via="feed")
        return

    reason = _feed_malfunction(p, now)
    if reason is None:
        return
    if p.last_rest_at is not None and now - p.last_rest_at < REST_BACKSTOP_SECONDS:
        return
    p.last_rest_at = now
    p.resumed = False
    _logger.warning("exit arming: checking %s over REST because %s", p.stock_code, reason)
    _attempt(p, proc, now, via="rest")


def evaluate(proc: Any = None, *, now: Optional[float] = None) -> None:
    """One pass over every waiting position. The worker's body; tests call it directly."""
    now = time.monotonic() if now is None else now
    with _lock:
        items = list(_pending.values())
    if not items:
        return
    if proc is None:
        from icici_breeze_backend.app.services.processor import processor

        proc = processor()
    for p in items:
        try:
            _evaluate_one(p, proc, now)
        except Exception:  # noqa: BLE001 -- one position must not stall the others
            _logger.exception("exit arming: evaluation failed for %s", p.id)


def _resume_from_database() -> None:
    today = now_ist().date().isoformat()
    try:
        rows = repo.waiting_pending_exits()
    except Exception:  # noqa: BLE001
        _logger.exception("exit arming: could not load waiting stops")
        return
    for row in rows:
        created = str(row.get("created_at") or "")[:10]
        if created and created < today:
            # Yesterday's orders are gone; there is nothing left to arm against.
            try:
                repo.update_pending_exit(
                    str(row["id"]), status="abandoned",
                    last_error="the day ended before every order finished",
                )
            except Exception:  # noqa: BLE001
                pass
            continue
        with _lock:
            _pending[str(row["id"])] = _Pending(
                id=str(row["id"]),
                user_id=str(row["user_id"]),
                bot_type=str(row["bot_type"]),
                run_id=row.get("run_id"),
                stock_code=str(row["stock_code"]),
                exchange_code=str(row["exchange_code"]),
                expiry_display=str(row["expiry_display"]),
                order_ids=[str(o) for o in row["order_ids"]],
                terms=dict(row["terms"]),
                registered_at=time.monotonic(),
                alerted=bool(row.get("alerted")),
                resumed=True,
                created_date=(
                    datetime.date.fromisoformat(created) if created else now_ist().date()
                ),
            )
    if rows:
        _logger.info("exit arming: resumed %d waiting stop(s)", len(_pending))


def _loop() -> None:
    while not _stop.is_set():
        _wake.wait(_TICK_SECONDS)
        _wake.clear()
        if _stop.is_set():
            break
        try:
            evaluate()
        except Exception:  # noqa: BLE001
            _logger.exception("exit arming pass failed")


def start_exit_arming() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    ensure_listening()
    _resume_from_database()
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="bot-exit-arming", daemon=True)
    _thread.start()
    _logger.info("Bot exit-arming worker started.")


def stop_exit_arming() -> None:
    global _thread
    _stop.set()
    _wake.set()
    _thread = None


def reset_state_for_tests() -> None:
    with _lock:
        _orders.clear()
        _pending.clear()


__all__ = [
    "ACK_GRACE_SECONDS",
    "REST_BACKSTOP_SECONDS",
    "current_status",
    "ensure_listening",
    "evaluate",
    "fill_state",
    "orders_settled",
    "pending_note",
    "prepare",
    "record_order_state",
    "start_exit_arming",
    "stop_exit_arming",
    "wait_then_arm",
]
