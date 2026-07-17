"""Arm-time preconditions for a Strategy Group.

Spec section 4 says an SG may only be armed once every leg is fully executed. That was
written about *entry* orders, but it is load-bearing for a second reason that is easy to
miss: **it is the only thing preventing duplicate exit orders after a Reset.**

A Reset does not cancel the SG's already-placed exit orders — it withdraws future
automation, it does not retract actions already taken (see `strategy_group_lifecycle`).
Those orders can still be resting at the exchange. Without this check, re-arming and
re-firing would stack a *second* exit order on top of each live one: the leg gets
double-exited and the position can flip net-contra.

Traced across every Reset path — a manual cancel of one leg, a broker rejection, spec
section 10 intervention, partial fills (`Partially Executed` is non-terminal, so it blocks
too) — this guard holds.
"""
from __future__ import annotations

import datetime
import logging
from typing import Any

from icici_breeze_backend.app.core.strike import strike_key
from icici_breeze_backend.app.core.timezone import today_ist_date
from icici_breeze_backend.app.services.reference_data.scrip_master_sql import (
    normalize_expiry_display,
)
from icici_breeze_backend.app.services.strategy_group_lifecycle import LIVE_ORDER_STATUSES

_logger = logging.getLogger(__name__)


def today_order_window() -> tuple[str, str]:
    """The date range for "orders that could still be working right now".

    `get_orders` requires an explicit range (ICICI caps it at 10 days). Mirrors the Orders
    page's own default — today through the next weekday — so both surfaces agree on what
    counts as a live order rather than drifting apart.
    """
    start = today_ist_date()
    nxt = start
    while True:
        nxt += datetime.timedelta(days=1)
        if nxt.weekday() < 5:
            break
    return start.strftime("%Y-%m-%d"), nxt.strftime("%Y-%m-%d")


class ArmPreconditionError(Exception):
    """Raised when an SG cannot be armed yet. `message` is user-facing and must name the
    real cause — a generic 'all legs must be executed' would leave someone staring at a
    blocked button with no idea that a previous rule's exit orders are the reason."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _expiry_matches(a: Any, b: Any) -> bool:
    try:
        return normalize_expiry_display(str(a or "")) == normalize_expiry_display(str(b or ""))
    except (ValueError, TypeError):
        return str(a or "").strip().lower() == str(b or "").strip().lower()


def _label(row: dict[str, Any]) -> str:
    """"NIFTY 26000 CE". `strike_key`, not str(): Strike is a bare float and str() would
    render "26000.0" at the user."""
    right = "CE" if str(row.get("right") or "").strip().lower().startswith("c") else "PE"
    return f"{row.get('stock_code') or ''} {strike_key(row.get('strike_price'))} {right}".strip()


def live_orders_for_group(
    breeze, user_id: str, stock_code: str, expiry_display: str
) -> list[dict[str, Any]]:
    """Every non-terminal order for this scrip+expiry, straight from the REST order book.

    REST-authoritative on purpose, never derived from WS state: a dropped notification
    must not be able to make an orphaned order look terminal and thereby unblock a re-arm.
    """
    start, end = today_order_window()
    resp = breeze.get_orders(user_id, start=start, end=end)
    if not isinstance(resp, dict) or resp.get("Status") != 200:
        # Fail closed. If we cannot prove there are no live orders, we must not let an arm
        # through -- the downside is a blocked button; the alternative is a duplicate exit.
        raise ArmPreconditionError(
            "Couldn't check your existing orders with the broker just now, so this rule "
            "wasn't armed. Try again in a moment."
        )
    rows = resp.get("Success")
    if not isinstance(rows, list):
        return []
    want_stock = stock_code.strip().upper()
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("stock_code") or "").strip().upper() != want_stock:
            continue
        if not _expiry_matches(row.get("expiry_date"), expiry_display):
            continue
        if str(row.get("status") or "").strip().lower() in LIVE_ORDER_STATUSES:
            out.append(row)
    return out


def assert_can_arm(breeze, user_id: str, stock_code: str, expiry_display: str) -> None:
    """Raise ArmPreconditionError unless this SG can be armed right now.

    Scoped to ANY non-terminal order for the scrip+expiry, not just ones tied to
    currently-open legs: a stray unfilled order still changes what the group will be, and
    a previous rule's orphan still risks the duplicate-fire above.
    """
    live = live_orders_for_group(breeze, user_id, stock_code, expiry_display)
    if not live:
        return
    labels = sorted({_label(r) for r in live})
    shown = ", ".join(labels[:3]) + ("…" if len(labels) > 3 else "")
    raise ArmPreconditionError(
        f"{len(live)} order(s) for {stock_code} {expiry_display} are still working at the "
        f"exchange ({shown}). Profit Booking / Stop Loss can only be set once every leg is "
        f"fully executed — wait for them to fill or expire, or cancel them in the Order "
        f"Book, then try again."
    )
