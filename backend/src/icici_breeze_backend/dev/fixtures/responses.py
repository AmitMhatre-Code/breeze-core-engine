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
        # Stock-option shorts -- exercise the Holdings Option Writer's "don't oversell"
        # netting. CIPLA holds 850 (2 lots of 425) and already has 1 lot of CE written, so
        # only 1 further CE lot is coverable. GAIL has an open PE lot against its holding.
        {
            "stock_code": "CIPLA",
            "exchange_code": "NFO",
            "product_type": "Options",
            "stock_index_indicator": "Stock",
            "action": "Sell",
            "quantity": "425",
            "average_price": "28.40",
            "ltp": "24.15",
            "right": "Call",
            "strike_price": "1500",
            "expiry_date": _nearest_tradeable_expiry("CIPLA", "NFO"),
        },
        {
            "stock_code": "GAIL",
            "exchange_code": "NFO",
            "product_type": "Options",
            "stock_index_indicator": "Stock",
            "action": "Sell",
            "quantity": "3550",
            "average_price": "4.05",
            "ltp": "3.20",
            "right": "Put",
            "strike_price": "165",
            "expiry_date": _nearest_tradeable_expiry("GAIL", "NFO"),
        },
    ]


# --------------------------------------------------------------------------------------
# Equity holdings -- input for the Holdings Option Writer bot (docs/bots-mvp-plan.md).
#
# Field shape VERIFIED against a real `get_portfolio_holdings(exchange_code="NSE")`
# response from production (2026-08-31). Notes that cost real confusion:
#
#   * There is NO pledge field. Portfolio holdings reports the FULL quantity, pledged
#     included, and nothing marks which rows are pledged. Pledged quantity is only
#     derivable as `portfolio_qty - demat_qty`, because `get_demat_holdings` reports 0 for
#     pledged shares. Bot 1 therefore needs BOTH endpoints: portfolio for the coverage cap,
#     demat to know what is deliverable today without unpledging.
#   * `change_percentage` is the **day's** price move, NOT the holding's P&L. (Real row:
#     AMARAJ avg 751.47, cmp 892.65, change_percentage -3.11.) Never treat it as return.
#   * `product_type` / `expiry_date` / `strike_price` / `right` /
#     `category_index_per_stock` / `action` are **null** on equity rows, not "".
#   * `current_market_price` is populated and usable as a spot fallback -- helpful given
#     the namespace split below.
#   * `booked_profit_loss` is realized P&L on partly-sold holdings; non-zero in practice.
#
# NAMESPACE: `stock_code` is the ICICI **ShortName** (HDFBAN, INFTEC, STABAN), the same
# namespace as `scrip_master`. `fo_bhavcopy` is keyed on **NSE symbols** instead and only
# 27 of 216 underlyings spell the same in both. Most rows below are drawn from the
# overlapping set so the mock resolves spot end-to-end today; RELIND is included precisely
# because it does NOT, so the missing bridge stays visible in dev.
#
# DATA: synthetic by decision (2026-08-31). Do NOT paste a real portfolio in here -- this
# file ships inside the image deployed to every customer's AWS account.
#
# Fields: (stock_code, lot_size, quantity, pledged_qty, average_price, ltp, day_chg_pct, case)
# lot_size and pledged_qty are carried to document each case's intent; production code must
# read lot size from the scrip master and infer pledging from the demat delta.
_MOCK_EQUITY_HOLDINGS: tuple[tuple, ...] = (
    ("ITC",     1725,  5175,     0,   231.40,   266.00,  0.42, "3 clean lots"),
    ("NTPC",    1500,  5700,     0,   172.87,   330.10, -0.80, "3 lots + 1200 remainder"),
    ("HINPET",  2025,  1170,     0,   171.12,   365.75,  0.48, "BELOW one lot -- excluded"),
    ("TCS",      225,   700,     0,  2696.82,  2342.10,  2.45, "3 lots, booked P&L non-zero"),
    ("ONGC",    2250,  5000,     0,   241.83,   232.30, -0.19, "2 lots + 500 remainder"),
    ("SAIL",    4700, 14100,  9400,   118.25,   200.00,  1.10, "2 of 3 lots PLEDGED"),
    ("MARUTI",    50,   150,     0, 11020.00, 13376.30, -1.24, "3 lots, high delivery cost"),
    ("CIPLA",    425,  1560,   425,  1477.04,  1409.20, -1.00, "3 lots, has an open short CE"),
    ("GAIL",    3550,  7100,     0,   152.30,   171.10,  0.65, "2 lots, has an open short PE"),
    ("RELIND",   500,  1000,     0,  1205.00,  1380.20,  0.31, "in scrip_master, NOT in fo_bhavcopy"),
    ("IRCTC",      0,   500,     0,   680.00,   742.15, -2.05, "no F&O contracts -- excluded"),
    ("LIBEES",     0, 11000,     0,  1001.60,   999.99, -0.00, "liquid ETF collateral -- excluded"),
)

# Realized P&L only shows up on holdings that have been partly sold.
_MOCK_BOOKED_PL = {"TCS": "710237.65", "CIPLA": "142392.31"}


def mock_portfolio_holding_rows(
    exchange_code: str = "", stock_code: str = ""
) -> list[dict]:
    """Equity holdings as `get_portfolio_holdings` returns them.

    This -- not `get_demat_holdings` -- is the correct source for holdings QUANTITY: demat
    reports 0 for pledged shares, which would silently undercount coverage on any pledged
    scrip. `quantity` here is the FULL holding, pledged included, and carries no marker for
    which part is pledged (see module notes above).
    """
    ex = (exchange_code or "").strip().upper()
    if ex and ex != "NSE":
        return []
    want = (stock_code or "").strip().upper()
    rows = []
    for code, _lot, qty, _pledged, avg, ltp, day_chg, _case in _MOCK_EQUITY_HOLDINGS:
        if want and code != want:
            continue
        rows.append(
            {
                "stock_code": code,
                "exchange_code": "NSE",
                "quantity": str(qty),
                "average_price": f"{avg:g}",
                "booked_profit_loss": _MOCK_BOOKED_PL.get(code, "0"),
                "current_market_price": f"{ltp:g}",
                "change_percentage": str(day_chg),
                "answer_flag": "N",
                "product_type": None,
                "expiry_date": None,
                "strike_price": None,
                "right": None,
                "category_index_per_stock": None,
                "action": None,
                "realized_profit": None,
                "unrealized_profit": None,
                "open_position_value": None,
                "portfolio_charges": None,
            }
        )
    return rows


def mock_demat_holding_rows() -> list[dict]:
    """Demat holdings -- deliberately reproduces ICICI's pledged-quantity quirk.

    Pledged shares come back as 0, so a wholly pledged scrip vanishes from any coverage
    calculation built on this endpoint. Kept faithful rather than "fixed" so a regression
    to `get_demat_holdings` for quantity fails visibly in mock instead of quietly
    undersizing in production.

    It is still needed, though: since `get_portfolio_holdings` carries no pledge marker,
    `portfolio_qty - demat_qty` is the ONLY way to learn what is pledged.
    """
    rows = []
    for code, _lot, qty, pledged, _avg, _ltp, _chg, _case in _MOCK_EQUITY_HOLDINGS:
        free = max(0, qty - pledged)
        rows.append(
            {
                "stock_code": code,
                "stock_ISIN": f"INE000{abs(hash(code)) % 10**5:05d}01",
                "quantity": str(free),
                "demat_avail_quantity": str(free),
            }
        )
    return rows


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
