"""Breeze-style responses for local mock mode (ICICI_BROKER_MODE=mock)."""

from __future__ import annotations

import datetime
import logging

_logger = logging.getLogger(__name__)

MOCK_CUSTOMER_DETAILS_SESSION_TOKEN = "mock_customerdetails_session_token"

MARGIN_DIRECT_RESPONSE = {
    "Status": 200,
    "Success": {
        "limit_list": [{"amount": 500000}, {"amount": 250000}],
        "cash_limit": 1_000_000.0,
    },
    "Error": None,
}


def _parse_display_date(value: str) -> datetime.date | None:
    try:
        return datetime.datetime.strptime(value, "%d-%b-%Y").date()
    except (TypeError, ValueError):
        return None


def _nearest_tradeable_expiry(stock_code: str, exchange_code: str) -> str:
    """Nearest real, still-open expiry for `stock_code`, read live from the scrip-master
    index -- so mock positions/orders always show a tradeable (future) contract instead of
    a date that has since rolled off the local reference data.

    Falls back to a plausible near-future date if the local scrip index has no rows yet
    (e.g. a fresh checkout before reference data has been seeded).
    """
    today = datetime.date.today()
    try:
        from icici_breeze_backend.app.services.reference_data.scrip_index import get_underlyings

        for entry in get_underlyings(exchange_code) or []:
            if str(entry.get("stock_code") or "").strip().upper() != stock_code.upper():
                continue
            future = sorted(
                parsed
                for d in (entry.get("expiry_dates") or [])
                if (parsed := _parse_display_date(d)) and parsed > today
            )
            if future:
                return future[0].strftime("%d-%b-%Y")
    except Exception:
        _logger.debug("mock expiry lookup failed for %s/%s", stock_code, exchange_code, exc_info=True)
    return (today + datetime.timedelta(days=7)).strftime("%d-%b-%Y")


def mock_portfolio_position_rows() -> list[dict]:
    """NFO index option shorts + one long; shapes match processor.get_positions enrichment."""
    nifty_expiry = _nearest_tradeable_expiry("NIFTY", "NFO")
    cnxban_expiry = _nearest_tradeable_expiry("CNXBAN", "NFO")
    return [
        {
            "stock_code": "NIFTY",
            "exchange_code": "NFO",
            "product_type": "Options",
            "stock_index_indicator": "Index",
            "action": "Sell",
            "quantity": "50",
            "average_price": "125.50",
            "ltp": "118.25",
            "right": "Call",
            "strike_price": "24800",
            "expiry_date": nifty_expiry,
        },
        {
            "stock_code": "NIFTY",
            "exchange_code": "NFO",
            "product_type": "Options",
            "stock_index_indicator": "Index",
            "action": "Buy",
            "quantity": "25",
            "average_price": "95.00",
            "ltp": "102.40",
            "right": "Put",
            "strike_price": "23500",
            "expiry_date": nifty_expiry,
        },
        {
            "stock_code": "CNXBAN",
            "exchange_code": "NFO",
            "product_type": "Options",
            "stock_index_indicator": "Index",
            "action": "Sell",
            "quantity": "15",
            "average_price": "310.00",
            "ltp": "305.00",
            "right": "Put",
            "strike_price": "52000",
            "expiry_date": cnxban_expiry,
        },
    ]


CUSTOMER_DETAILS_DIRECT_RESPONSE = {
    "Status": 200,
    "Success": {
        "session_token": MOCK_CUSTOMER_DETAILS_SESSION_TOKEN,
        "id": "MOCKUSER",
        "exg_trade_date": "04-Apr-2026",
    },
    "Error": None,
}


def mock_order_list_rows() -> list[dict]:
    nifty_expiry = _nearest_tradeable_expiry("NIFTY", "NFO")
    bsesen_expiry = _nearest_tradeable_expiry("BSESEN", "BFO")
    return [
        {
            "order_id": "MOCK-ORD-OPEN-1",
            "stock_code": "NIFTY",
            "exchange_code": "NFO",
            "expiry_date": nifty_expiry,
            "strike_price": 24900.0,
            "right": "Call",
            "product_type": "Options",
            "status": "Ordered",
            "action": "Sell",
            "quantity": 75,
            "pending_quantity": 75,
        },
        {
            "order_id": "MOCK-ORD-DONE-1",
            "stock_code": "NIFTY",
            "exchange_code": "NFO",
            "expiry_date": nifty_expiry,
            "strike_price": 24800.0,
            "right": "Call",
            "product_type": "Options",
            "status": "Executed",
            "action": "Sell",
            "quantity": 50,
            "pending_quantity": 0,
        },
        {
            "order_id": "MOCK-ORD-BFO-1",
            "stock_code": "BSESEN",
            "exchange_code": "BFO",
            "expiry_date": bsesen_expiry,
            "strike_price": 82000.0,
            "right": "Put",
            "product_type": "Options",
            "status": "Partially Executed",
            "action": "Sell",
            "quantity": 20,
            "pending_quantity": 8,
        },
    ]

MOCK_TRADE_LIST_SAMPLE = [
    {
        "trade_date": "28-Mar-2026",
        "quantity": "50",
        "average_cost": "120.00",
        "action": "Sell",
        "brokerage_amount": "45.00",
        "total_taxes": "18.50",
        "stock_code": "NIFTY",
        "exchange_code": "NFO",
    },
    {
        "trade_date": "15-Mar-2026",
        "quantity": "25",
        "average_cost": "88.00",
        "action": "Buy",
        "brokerage_amount": "22.00",
        "total_taxes": "9.00",
        "stock_code": "NIFTY",
        "exchange_code": "NFO",
    },
    {
        "trade_date": "10-Mar-2026",
        "quantity": "10",
        "average_cost": "210.00",
        "action": "Sell",
        "brokerage_amount": "30.00",
        "total_taxes": "12.00",
        "stock_code": "BSESEN",
        "exchange_code": "BFO",
    },
]


def mock_response_for_icici_url(url: str) -> dict:
    u = (url or "").lower()
    if "customerdetails" in u:
        return dict(CUSTOMER_DETAILS_DIRECT_RESPONSE)
    if "margin" in u and "breezeapi" in u:
        return dict(MARGIN_DIRECT_RESPONSE)
    if "portfoliopositions" in u:
        return {"Status": 200, "Success": mock_portfolio_position_rows(), "Error": None}
    if "breezeapi/api/v1" in u:
        tail = u.split("breezeapi/api/v1/")[-1].split("?")[0].strip("/")
        if any(k in tail for k in ("order", "trade", "position", "history", "scrip")):
            return {"Status": 200, "Success": [], "Error": None}
        return {"Status": 200, "Success": {}, "Error": None}
    return {"Status": 200, "Success": {}, "Error": None}
