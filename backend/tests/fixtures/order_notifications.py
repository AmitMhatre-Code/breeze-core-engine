"""Real ICICI order-notification payloads, captured from the live Breeze API Playground.

Source: docs/Temp/order_{placed,modified,cancelled} -- one NIFTY 26000 CE order placed
at limit Rs 3.00, modified to Rs 2.80, then cancelled. Copied verbatim (the `keys:` line
in those dumps is the playground's own header and is not part of the payload).

These are deliberately unedited: the whole point is to test against what ICICI actually
sends, including the fields that are garbage (see order_notifications.py's docstring).
Live-mode broker calls only work from the production static IP, so these captures are
the only ground truth available locally.
"""
from __future__ import annotations

from typing import Any

# Placed: limit Rs 3.00 -> limitRate "300". Strike 26000 -> strikePrice "2600000".
# Note averageExecutedRate "1550900.000000" despite executedQuantity "0" -- garbage.
ORDER_PLACED: dict[str, Any] = {
    "sourceNumber": "1",
    "group": "*",
    "userId": "VIKRAMMH",
    "key": "*",
    "messageLength": "267",
    "requestType": "5",
    "messageSequence": "2026071701788212",
    "messageDate": "17-07-2026",
    "messageTime": "12:45:30",
    "messageCategory": "Intraday Calls",
    "messagePriority": "N",
    "messageType": "6",
    "orderMatchAccount": "8500284443",
    "orderExchangeCode": "NFO",
    "stockCode": "NIFTY",
    "productType": "Options",
    "optionType": "Call",
    "exerciseType": "E",
    "strikePrice": "2600000",
    "expiryDate": "21-Jul-2026",
    "orderValidDate": "17-Jul-2026",
    "orderFlow": "Sell",
    "limitMarketFlag": "Limit",
    "orderType": "Day",
    "limitRate": "300",
    "orderStatus": "Ordered",
    "orderReference": "202607173800017846",
    "orderTotalQuantity": "130",
    "executedQuantity": "0",
    "cancelledQuantity": "0",
    "expiredQuantity": "0",
    "stopLossTrigger": "0",
    "specialFlag": "N",
    "pipeId": "38",
    "channel": "WEB",
    "modificationOrCancelFlag": "Y",
    "tradeDate": "17-Jul-2026",
    "acknowledgeNumber": "1200000100947912    ",
    "stopLossOrderReference": "1200000100947912    ",
    "totalAmountBlocked": "*",
    "averageExecutedRate": "1550900.000000",
    "cancelFlag": "0.000000",
    "squareOffMarket": "N",
    "quickExitFlag": "N",
    "stopValidTillDateFlag": "Y",
    "priceImprovementFlag": "N",
    "conversionImprovementFlag": "N",
    "trailUpdateCondition": "N",
    "systemPartnerCode": "N",
}

# Modified: Rs 3.00 -> Rs 2.80. Still orderStatus "Ordered", same orderReference, same
# messageType. Note squareOffMarket/quickExitFlag flipped N->Y on a plain price change.
ORDER_MODIFIED: dict[str, Any] = {
    **ORDER_PLACED,
    "messageSequence": "2026071701796231",
    "messageTime": "12:47:01",
    "messageLength": "261",
    "limitRate": "280",
    "averageExecutedRate": "0.000000",
    "squareOffMarket": "Y",
    "quickExitFlag": "Y",
    "stopValidTillDateFlag": "N",
}

# Cancelled: orderStatus "Cancelled", cancelledQuantity now the full 130.
ORDER_CANCELLED: dict[str, Any] = {
    **ORDER_PLACED,
    "messageSequence": "2026071701802257",
    "messageTime": "12:48:17",
    "messageLength": "263",
    "limitRate": "280",
    "orderStatus": "Cancelled",
    "cancelledQuantity": "130",
    "averageExecutedRate": "0.000000",
    "stopValidTillDateFlag": "N",
}


def executed(**overrides: Any) -> dict[str, Any]:
    """A fully-executed variant. Not captured live (the test order was cancelled
    rather than filled), so it is synthesised from ORDER_PLACED by applying only the
    fields ICICI is documented to change: orderStatus + executedQuantity."""
    return {
        **ORDER_PLACED,
        "messageSequence": "2026071701810000",
        "orderStatus": "Executed",
        "executedQuantity": ORDER_PLACED["orderTotalQuantity"],
        **overrides,
    }


def with_status(status: str, **overrides: Any) -> dict[str, Any]:
    return {**ORDER_PLACED, "orderStatus": status, **overrides}
