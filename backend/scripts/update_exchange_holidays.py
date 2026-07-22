#!/usr/bin/env python3
"""Dev helper: print guidance for refreshing backend/data/exchange_holidays.json.

NSE/BSE holiday pages are not reliably machine-parseable; update the JSON manually
from official circulars, then run tests/test_market_hours.py.
"""
from __future__ import annotations

print(
    "Update backend/data/exchange_holidays.json from:\n"
    "  https://www.nseindia.com/resources/exchange-communication-holidays\n"
    "  https://www.bseindia.com/static/markets/marketinfo/listholi\n"
    "Use ISO dates (YYYY-MM-DD) as keys in the holidays object."
)
