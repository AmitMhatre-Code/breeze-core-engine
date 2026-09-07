"""Futures contract resolution for the scalping signal (services.bots.scalping.futures_feed).

The subscription itself needs a live SDK and is exercised in mock mode; what is unit-tested
here is the part that decides *which* contract, because that is pure and is where a wrong
answer silently poisons the volume filter.
"""
from __future__ import annotations

import datetime

from icici_breeze_backend.app.services.bots.scalping.futures_feed import (
    NiftyFuturesFeed,
    monthly_expiries,
    near_month_contract,
)

# A realistic NIFTY option expiry list: weeklies plus the month-end contract, which is the
# one the monthly future expires alongside.
_EXPIRIES = [
    "08-Sep-2026", "15-Sep-2026", "22-Sep-2026", "29-Sep-2026",  # Sep, monthly = 29th
    "06-Oct-2026", "13-Oct-2026", "20-Oct-2026", "27-Oct-2026",  # Oct, monthly = 27th
    "23-Nov-2026",
]


def test_monthly_expiries_takes_the_last_of_each_month():
    """Futures are monthly and expire with the month's final weekly option expiry.

    Derived rather than hardcoded as "last Thursday" because SEBI has moved expiry weekdays
    before -- the same reason `bots.scheduler` reads the scrip master instead of a weekday.
    """
    assert monthly_expiries(_EXPIRIES) == [
        datetime.date(2026, 9, 29),
        datetime.date(2026, 10, 27),
        datetime.date(2026, 11, 23),
    ]


def test_monthly_expiries_ignores_unparseable_entries():
    assert monthly_expiries(["not-a-date", "", None, "29-Sep-2026"]) == [
        datetime.date(2026, 9, 29)
    ]


def test_near_month_is_the_next_monthly_expiry():
    c = near_month_contract(_EXPIRIES, today=datetime.date(2026, 9, 10))
    assert c is not None
    assert c.expiry_display == "29-Sep-2026"
    assert c.stock_code == "NIFTY"


def test_roll_happens_the_day_after_expiry_not_on_it():
    """'Roll on expiry day' means the near month stays current through its own session."""
    on_expiry = near_month_contract(_EXPIRIES, today=datetime.date(2026, 9, 29))
    assert on_expiry is not None and on_expiry.expiry_display == "29-Sep-2026"

    day_after = near_month_contract(_EXPIRIES, today=datetime.date(2026, 9, 30))
    assert day_after is not None and day_after.expiry_display == "27-Oct-2026"


def test_no_contract_when_every_expiry_is_in_the_past():
    assert near_month_contract(_EXPIRIES, today=datetime.date(2027, 1, 1)) is None
    assert near_month_contract([], today=datetime.date(2026, 9, 10)) is None


def test_symbol_formatting_accepts_bare_and_prefixed_tokens():
    """The real SDK returns '4.1!71472'; a bare token must still be usable."""
    assert NiftyFuturesFeed._format_symbol("4.1!71472") == "4.1!71472"
    assert NiftyFuturesFeed._format_symbol("71472") == "4.1!71472"


def test_token_resolution_survives_the_sdk_returning_an_exception():
    """`get_stock_token_value` swallows failures and RETURNS the exception object.

    Verified in breeze_connect's source (`except Exception as e: return e`). A resolver that
    assumed a tuple would raise on the unpack; this must simply move to the next candidate
    expiry format.
    """
    contract = near_month_contract(_EXPIRIES, today=datetime.date(2026, 9, 10))

    class Sdk:
        def __init__(self):
            self.interval = ""
            self.calls = []

        def get_stock_token_value(self, **kwargs):
            self.calls.append(kwargs["expiry_date"])
            if len(self.calls) < 3:
                return ValueError("Stock code not valid")  # returned, not raised
            return "4.1!987654", False

    sdk = Sdk()
    feed = NiftyFuturesFeed()
    assert feed._resolve_token(sdk, contract) == "4.1!987654"
    assert len(sdk.calls) == 3  # tried candidates until one resolved


def test_token_resolution_primes_uninitialised_sdk_interval():
    """The SDK reads `self.interval` while resolving but never sets it in __init__.

    Leaving it unset raises inside the SDK; `index_spot_feed` primes it for the same reason.
    """
    contract = near_month_contract(_EXPIRIES, today=datetime.date(2026, 9, 10))

    class Sdk:
        def get_stock_token_value(self, **kwargs):
            return "4.1!1", False

    sdk = Sdk()
    assert not hasattr(sdk, "interval")
    NiftyFuturesFeed()._resolve_token(sdk, contract)
    assert sdk.interval == ""


def test_token_resolution_returns_none_when_no_format_resolves():
    contract = near_month_contract(_EXPIRIES, today=datetime.date(2026, 9, 10))

    class Sdk:
        interval = ""

        def get_stock_token_value(self, **kwargs):
            raise RuntimeError("no such contract")

    assert NiftyFuturesFeed()._resolve_token(Sdk(), contract) is None
