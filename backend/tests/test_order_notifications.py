"""Parser tests against the real ICICI order-notification captures.

The x100 scaling and the lying fields are the two things that would silently corrupt
the SG lifecycle, so they get explicit coverage rather than being implied.
"""
from __future__ import annotations

import pytest

from icici_breeze_backend.app.services.order_notifications import (
    OrderNotification,
    is_order_notification,
    parse_order_notification,
)
from tests.fixtures.order_notifications import (
    ORDER_CANCELLED,
    ORDER_MODIFIED,
    ORDER_PLACED,
    executed,
    with_status,
)


def test_parses_real_placed_capture():
    n = parse_order_notification(ORDER_PLACED)
    assert isinstance(n, OrderNotification)
    assert n.order_id == "202607173800017846"
    assert n.user_id == "VIKRAMMH"
    assert n.status == "ordered"
    assert n.stock_code == "NIFTY"
    assert n.exchange_code == "NFO"
    assert n.right == "call"
    assert n.action == "Sell"
    assert n.total_quantity == 130
    assert n.sequence == "2026071701788212"


def test_prices_are_scaled_by_100():
    """The user set a Rs 3.00 limit and ICICI sent "300"; strike 26000 arrived as
    "2600000". Getting this wrong would misprice/misidentify every contract."""
    n = parse_order_notification(ORDER_PLACED)
    assert n.limit_price == pytest.approx(3.00)
    assert float(n.strike) == pytest.approx(26000.0)

    modified = parse_order_notification(ORDER_MODIFIED)
    assert modified.limit_price == pytest.approx(2.80)


def test_modify_is_indistinguishable_except_by_limit_and_sequence():
    """A modify emits no distinct event type -- same messageType, same orderReference,
    still 'Ordered'. Only limitRate and messageSequence move."""
    placed = parse_order_notification(ORDER_PLACED)
    modified = parse_order_notification(ORDER_MODIFIED)
    assert modified.order_id == placed.order_id
    assert modified.status == placed.status == "ordered"
    assert modified.limit_price != placed.limit_price
    assert int(modified.sequence) > int(placed.sequence)


def test_order_reference_is_stable_across_the_whole_lifecycle():
    ids = {
        parse_order_notification(p).order_id
        for p in (ORDER_PLACED, ORDER_MODIFIED, ORDER_CANCELLED)
    }
    assert ids == {"202607173800017846"}


def test_cancelled_capture_is_terminal_failure():
    n = parse_order_notification(ORDER_CANCELLED)
    assert n.status == "cancelled"
    assert n.is_terminal
    assert n.is_terminal_failure
    assert not n.is_terminal_success
    assert n.cancelled_quantity == 130
    assert not n.moved_position  # nothing filled -> not a manual-intervention signal


def test_executed_is_the_only_terminal_success():
    n = parse_order_notification(executed())
    assert n.is_terminal_success
    assert n.moved_position
    assert n.pending_quantity == 0


@pytest.mark.parametrize(
    "status",
    ["Cancelled", "Rejected", "Expired",
     "Partially Executed And Cancelled", "Partially Executed And Expired"],
)
def test_all_six_terminal_failures_reset(status):
    """Spec section 8 lists cancelled/rejected/expired/failed. The two partial-fill
    terminals belong here on their own terms -- they contain a cancel/expiry."""
    n = parse_order_notification(with_status(status))
    assert n.is_terminal_failure, status
    assert not n.is_terminal_success, status


@pytest.mark.parametrize(
    "status", ["Requested", "Queued", "Ordered", "Partially Executed", "Freezed"]
)
def test_non_terminal_statuses_keep_waiting(status):
    """Fired is a patient state: a resting exit order must NOT resolve the SG."""
    n = parse_order_notification(with_status(status))
    assert not n.is_terminal, status
    assert not n.is_terminal_failure, status


def test_pending_quantity_is_derived_since_fno_has_no_open_quantity():
    n = parse_order_notification(
        with_status("Partially Executed", executedQuantity="50", cancelledQuantity="0")
    )
    assert n.pending_quantity == 80  # 130 total - 50 executed
    assert n.moved_position


def test_partial_execute_and_cancel_leaves_no_pending_but_did_move_position():
    n = parse_order_notification(
        with_status(
            "Partially Executed And Cancelled", executedQuantity="50", cancelledQuantity="80"
        )
    )
    assert n.pending_quantity == 0
    assert n.is_terminal_failure
    assert n.moved_position  # 50 lots really did close -> residual exposure changed


def test_cash_shape_message_types_are_ignored():
    """4/5 are the cash/equity payload shape. This is an options-only feature."""
    for mt in ("4", "5"):
        assert parse_order_notification({**ORDER_PLACED, "messageType": mt}) is None


def test_price_ticks_are_not_order_notifications():
    """Order events share the on_ticks callback with price ticks; the discriminator
    is orderReference, which price ticks never carry."""
    tick = {"symbol": "4.1!35001", "last": "123.4", "ltp": "123.4"}
    assert not is_order_notification(tick)
    assert parse_order_notification(tick) is None


def test_parser_never_raises_on_garbage():
    """Runs on the SDK's WS callback thread -- an exception here would take out tick
    ingestion for every other consumer."""
    for junk in (None, "", [], 42, {}, {"orderReference": "x"},
                 {"orderReference": "x", "messageType": "6"}):
        assert parse_order_notification(junk) is None


def test_malformed_numbers_do_not_raise():
    n = parse_order_notification(
        {**ORDER_PLACED, "limitRate": "abc", "orderTotalQuantity": "", "strikePrice": "??"}
    )
    assert n is not None
    assert n.limit_price is None
    assert n.strike is None
    assert n.total_quantity == 0


def test_lying_fields_are_not_surfaced():
    """averageExecutedRate read 1550900.0 on the placed tick with executedQuantity 0;
    stopLossOrderReference is an SDK aliasing bug (same index as acknowledgeNumber);
    squareOffMarket flipped N->Y on a plain price modify, so it must never be used to
    identify square-off orders."""
    n = parse_order_notification(ORDER_PLACED)
    for banned in (
        "average_executed_rate", "stop_loss_order_reference",
        "square_off_market", "quick_exit_flag", "total_amount_blocked",
    ):
        assert not hasattr(n, banned), banned
