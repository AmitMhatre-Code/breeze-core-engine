"""Parses ICICI order-notification WS payloads into a normalized event.

Delivery path (verified against breeze_connect==1.0.65 source): subscribing with
`subscribe_feeds(get_order_notification=True)` opens a *separate* socket.io client
(`sio_order_refresh_handler` -> `config.LIVE_FEEDS_URL`) from the price-tick client
(`LIVE_STREAM_URL`) -- but `SocketEventBreeze.on_message` dispatches BOTH to
`breeze.on_ticks`, which `breeze_websocket_manager` already points at
`ws_tick_pipeline.ingest_tick`. So order events arrive on the same callback as
price ticks and must be discriminated there; `is_order_notification()` is that
discriminator (price ticks never carry `orderReference`).

Payload facts below were verified against three real captures taken from the live
Breeze API Playground (place -> modify -> cancel of one NIFTY 26000 CE order); the
raw dumps are in docs/Temp/order_{placed,modified,cancelled}. Two of them matter a
lot and are not guessable from the SDK source:

1. **Every price field is x100** -- the rupee amount to 2dp with the decimal point
   removed. Limit Rs 3.00 arrives as "300", Rs 2.80 as "280", strike 26000 as
   "2600000". No exceptions.
2. **A modify emits no distinct event.** The modified tick is just another
   `orderStatus: "Ordered"` with a changed `limitRate` -- same `messageType`, same
   `orderReference`. Placed vs modified are indistinguishable except by comparing
   against prior state; order events by `messageSequence` (monotonic).

Fields deliberately NOT surfaced, because the captures prove they lie:
  * `averageExecutedRate` -- read "1550900.000000" on the *placed* tick while
    `executedQuantity` was "0". Meaningless; never compute realized P&L from it.
  * `stopLossOrderReference` -- SDK bug: assigned `data[37]`, the *same index* as
    `acknowledgeNumber` (breeze_connect.py:697-698). Both captures show identical
    values. It is not a real reference.
  * `squareOffMarket` / `quickExitFlag` -- flipped N->Y->N across a plain price
    modify. Despite the name, `squareOffMarket` CANNOT be used to identify
    square-off orders; use `orderReference` set membership instead.
  * `totalAmountBlocked` -- read "*".
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from icici_breeze_backend.app.core.strike import Strike, parse_strike
from icici_breeze_backend.app.services.reference_data.scrip_master_sql import normalize_expiry_display

_logger = logging.getLogger(__name__)

# ICICI scales price-like fields by 100 (2dp with the point removed) -- see module docstring.
_PRICE_SCALE = 100.0

# The SDK maps single-letter codes to these strings via config.TUX_TO_USER_MAP before
# they ever reach us, so we match on the human-readable form.
#
# Full orderStatus domain: All, Requested, Queued, Ordered, Partially Executed, Executed,
# Rejected, Expired, Partially Executed And Expired, Partially Executed And Cancelled,
# Freezed, Cancelled. ("All" is a filter value, never a live status.)
STATUS_EXECUTED = "executed"
_TERMINAL_SUCCESS = {"executed"}
_TERMINAL_FAILURE = {
    "cancelled",
    "rejected",
    "expired",
    "partially executed and cancelled",
    "partially executed and expired",
}
# Non-terminal: requested, queued, ordered, partially executed, freezed.
# `Freezed` semantics are unconfirmed against real ICICI -- treated as non-terminal
# (i.e. keep waiting) because that is the fail-safe reading: it never resolves an SG.
_NON_TERMINAL = {"requested", "queued", "ordered", "partially executed", "freezed"}

# messageType 4/5 are the cash/equity payload shape (has `openQuantity`); 6/7 are F&O
# (no `openQuantity` -- pending must be derived). This is an options-only feature.
_FNO_MESSAGE_TYPES = {"6", "7"}


def is_order_notification(raw: Any) -> bool:
    """Discriminates an order-notification dict from a price tick on the shared
    `on_ticks` callback. Price ticks never carry `orderReference`."""
    return isinstance(raw, dict) and bool(raw.get("orderReference"))


def _scaled_price(raw: Any) -> float | None:
    """ICICI sends prices as the 2dp rupee amount with the decimal point removed."""
    if raw in (None, ""):
        return None
    try:
        return float(str(raw).strip()) / _PRICE_SCALE
    except (TypeError, ValueError):
        return None


def _int_or_zero(raw: Any) -> int:
    try:
        return int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return 0


@dataclass(frozen=True)
class OrderNotification:
    """One normalized order-state event.

    `order_id` is `orderReference`, which is the SAME identifier REST `place_order`
    returns as `Success.order_id` and the order book lists as `order_id` (confirmed
    against the live account; format `YYYYMMDD` + pipeId + 8-digit sequence, matching
    the real fixture in tests/test_modify_order_single.py). That identity is what lets
    the SG lifecycle tell "our own exit filled" from "the user traded manually" --
    without it there is no principled way to separate the two.
    """

    order_id: str
    user_id: str
    status: str
    stock_code: str
    exchange_code: str
    expiry_display: str
    strike: Strike | None
    right: str  # "call" | "put"
    action: str  # "Buy" | "Sell" | "NA"
    limit_price: float | None
    total_quantity: int
    executed_quantity: int
    cancelled_quantity: int
    expired_quantity: int
    sequence: str

    @property
    def pending_quantity(self) -> int:
        """F&O payloads have no `openQuantity` (that's the cash shape) -- derive it."""
        return max(
            0,
            self.total_quantity
            - self.executed_quantity
            - self.cancelled_quantity
            - self.expired_quantity,
        )

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL_SUCCESS or self.status in _TERMINAL_FAILURE

    @property
    def is_terminal_success(self) -> bool:
        """Fully executed. Only this counts toward an SG reaching Completed."""
        return self.status in _TERMINAL_SUCCESS

    @property
    def is_terminal_failure(self) -> bool:
        """Cancelled / rejected / expired, including the two partial-fill terminals.
        Any of these Resets an SG (spec section 8)."""
        return self.status in _TERMINAL_FAILURE

    @property
    def moved_position(self) -> bool:
        """True when this event actually changed the holder's position, i.e. some
        quantity filled. Used to decide whether an order we did NOT place counts as
        the manual intervention that invalidates a rule -- a merely *resting* manual
        order changes no composition until it fills."""
        return self.executed_quantity > 0


def parse_order_notification(raw: Any) -> OrderNotification | None:
    """Normalize one raw order-notification dict. Returns None for anything that
    isn't a parseable F&O order event (price ticks, cash-shape payloads, junk).
    Never raises -- this runs on the SDK's WS callback thread, where an exception
    would take out tick ingestion for every other consumer too.
    """
    if not is_order_notification(raw):
        return None
    try:
        message_type = str(raw.get("messageType") or "").strip()
        if message_type not in _FNO_MESSAGE_TYPES:
            # Cash/equity (4/5) or unknown -- options-only feature, ignore quietly.
            return None

        order_id = str(raw.get("orderReference") or "").strip()
        user_id = str(raw.get("userId") or "").strip()
        if not order_id or not user_id:
            return None

        status = str(raw.get("orderStatus") or "").strip().lower()
        if status and status not in _TERMINAL_SUCCESS and status not in _TERMINAL_FAILURE:
            if status not in _NON_TERMINAL:
                _logger.warning(
                    "Unknown ICICI orderStatus=%r on order_id=%s -- treating as non-terminal",
                    raw.get("orderStatus"),
                    order_id,
                )

        right_raw = str(raw.get("optionType") or "").strip().lower()
        right = "call" if right_raw.startswith("c") else "put"

        try:
            expiry_display = normalize_expiry_display(str(raw.get("expiryDate") or ""))
        except (ValueError, TypeError):
            expiry_display = str(raw.get("expiryDate") or "").strip()

        strike_rupees = _scaled_price(raw.get("strikePrice"))
        strike = parse_strike(strike_rupees) if strike_rupees is not None else None

        return OrderNotification(
            order_id=order_id,
            user_id=user_id,
            status=status,
            stock_code=str(raw.get("stockCode") or "").strip().upper(),
            exchange_code=str(raw.get("orderExchangeCode") or "").strip().upper(),
            expiry_display=expiry_display,
            strike=strike,
            right=right,
            action=str(raw.get("orderFlow") or "").strip(),
            limit_price=_scaled_price(raw.get("limitRate")),
            total_quantity=_int_or_zero(raw.get("orderTotalQuantity")),
            executed_quantity=_int_or_zero(raw.get("executedQuantity")),
            cancelled_quantity=_int_or_zero(raw.get("cancelledQuantity")),
            expired_quantity=_int_or_zero(raw.get("expiredQuantity")),
            sequence=str(raw.get("messageSequence") or "").strip(),
        )
    except Exception:  # noqa: BLE001 -- must never break the shared tick callback
        _logger.exception("Failed to parse order notification payload")
        return None
