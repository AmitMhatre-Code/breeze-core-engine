"""Contract facts the backtests replay against (docs/bots-scalping-plan.md section 8.6).

Everything here is a *date-ranged fact about the exchange*, not a setting. The live bots never
need any of it -- they read lot sizes and expiries from the scrip master -- but the scrip
master only describes contracts that are still listed, and a replay walks contracts that
expired months ago. So the replays need the history written down, and a fact that changed
over time (the expiry weekday, the lot size) has to be written down *with its dates*: one
value across a change misprices every day on one side of it.

`HISTORY_START` was a floor until 2026-09-17 and is now only the default start of a range with
no dates given. Replays are unrestricted in period and always use today's lot size and today's
margin (#36). The date-ranged facts below still matter: a wrong expiry weekday before its
verified window resolves to a contract ICICI has no bars for, which shows up as a reported data
gap on that day rather than as a mispriced trade.
"""
from __future__ import annotations

import datetime
import math
from typing import Optional, Sequence

# UNVERIFIED: the first day of the current lot-size era. NIFTY's lot is 65 today (the scrip
# master, and the 10-11 Sep paper fills: 845 units = 13 lots). It was 75 before, and as far as
# we know the change came with the January 2026 contracts. Confirm against the NSE circular and
# move this date if it is wrong -- every replay and every fetch clips to it.
HISTORY_START = datetime.date(2026, 1, 1)

# Today's lot sizes, valid from HISTORY_START. A replay never asks about an earlier day.
LOT_SIZE: dict[str, int] = {"NIFTY": 65, "BSESEN": 20}

STRIKE_STEP: dict[str, float] = {"NIFTY": 50.0, "BSESEN": 100.0}

OPTION_EXCHANGE: dict[str, str] = {"NIFTY": "NFO", "BSESEN": "BFO"}

# The cash index each option settles against, as (exchange_code, stock_code) for ICICI's
# historical API. The bots read spot off the index, not the futures: a monthly future carries
# a basis of a strike or two, so an ATM chosen off it is the wrong contract.
# INDVIX is not an index anything trades against, but its 1-minute bars are fetched and cached
# the same way, for Bot 4's `vix_not_rising` entry filter (#38).
SPOT_SOURCE: dict[str, tuple[str, str]] = {
    "NIFTY": ("NSE", "NIFTY"),
    "BSESEN": ("BSE", "BSESEN"),
    "INDVIX": ("NSE", "INDVIX"),
}

WeekdayMap = Sequence[tuple[Optional[datetime.date], int]]

# Monday=0 ... Sunday=6. SEBI moved both indices' expiry day in 2025, so each map is
# date-ranged. Verified against the scrip master on 2026-09-13: every listed NIFTY expiry is a
# Tuesday (one holiday-shifted Monday) and every SENSEX expiry a Thursday. The earlier entries
# predate HISTORY_START and are kept only so a date before it is never silently mispriced.
EXPIRY_WEEKDAY_MAP: dict[str, WeekdayMap] = {
    "NIFTY": (
        (datetime.date(2025, 8, 31), 3),  # Thursday, up to and including this date
        (None, 1),                         # Tuesday, thereafter
    ),
    "BSESEN": ((None, 3),),                # Thursday throughout the history the replays accept
}


class OutsideHistory(ValueError):
    """A day before HISTORY_START, where today's lot size is not known to apply."""


def lot_size_for(stock_code: str, day: datetime.date) -> int:
    """Today's lot size, for every replayed day (#36).

    A backtest answers "what would this bot, as configured today, have done?", so it sizes with
    today's lot and today's margin whatever the date -- the user's rule, decided 2026-09-17. A
    day before HISTORY_START (NIFTY was 75 a lot then) is therefore replayed at 65, deliberately.
    `day` is kept in the signature so a future dated table slots in without touching callers."""
    return LOT_SIZE[stock_code]


def expiry_weekday_for(d: datetime.date, weekday_map: WeekdayMap) -> int:
    for until, weekday in weekday_map:
        if until is None or d <= until:
            return weekday
    return weekday_map[-1][1]


def is_trading_day(d: datetime.date, holidays: Optional[set[datetime.date]] = None) -> bool:
    return d.weekday() < 5 and d not in (holidays or set())


def next_expiry(
    d: datetime.date,
    weekday_map: WeekdayMap,
    holidays: Optional[set[datetime.date]] = None,
) -> datetime.date:
    """The next weekly expiry on or after `d`, shifted back off an exchange holiday.

    Shifted *back*, not forward: when an expiry day is a holiday the exchange brings the
    expiry forward to the previous trading day, it does not defer it.
    """
    holidays = holidays or set()
    target = expiry_weekday_for(d, weekday_map)
    ahead = (target - d.weekday()) % 7
    expiry = d + datetime.timedelta(days=ahead)
    while not is_trading_day(expiry, holidays):
        expiry -= datetime.timedelta(days=1)
        if expiry < d:
            # Shifting back has moved the expiry into the past; the next one is a week out.
            return next_expiry(d + datetime.timedelta(days=1), weekday_map, holidays)
    return expiry


def monthly_expiry(
    year: int, month: int, weekday: int, holidays: Optional[set[datetime.date]] = None
) -> datetime.date:
    """The last `weekday` of the month, shifted back off a holiday -- the futures expiry."""
    first_next = datetime.date(year + (month == 12), month % 12 + 1, 1)
    last = first_next - datetime.timedelta(days=1)
    expiry = last - datetime.timedelta(days=(last.weekday() - weekday) % 7)
    while not is_trading_day(expiry, holidays):
        expiry -= datetime.timedelta(days=1)
    return expiry


def near_month_futures_expiry(
    d: datetime.date, stock_code: str, holidays: Optional[set[datetime.date]] = None
) -> datetime.date:
    """The futures contract a replay of `d` should read: this month's until it expires.

    A candle history spans contracts, so a fetch has to roll. The expiry day itself still
    reads the expiring contract, which is what was trading that day.
    """
    weekday = expiry_weekday_for(d, EXPIRY_WEEKDAY_MAP[stock_code])
    this_month = monthly_expiry(d.year, d.month, weekday, holidays)
    if d <= this_month:
        return this_month
    nxt = datetime.date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return monthly_expiry(nxt.year, nxt.month, expiry_weekday_for(nxt, EXPIRY_WEEKDAY_MAP[stock_code]), holidays)


def expiry_api(d: datetime.date) -> str:
    """ICICI's expiry format, as `processor._expiry_display_to_api` builds it."""
    return f"{d.isoformat()}T06:00:00.000Z"


def atm_strike(spot: float, stock_code: str) -> float:
    """The grid strike nearest spot, ties to the lower -- the live `atm_strike` tie rule."""
    step = STRIKE_STEP[stock_code]
    lower = math.floor(spot / step) * step
    upper = lower + step
    return upper if (upper - spot) < (spot - lower) else lower


def strike_beyond(target: float, stock_code: str, *, up: bool) -> float:
    """The grid strike at or beyond `target`, away from the money -- the Bot 2 strike rule."""
    step = STRIKE_STEP[stock_code]
    return (math.ceil(target / step) if up else math.floor(target / step)) * step


def trading_days(
    start: datetime.date, end: datetime.date, holidays: Optional[set[datetime.date]] = None
) -> list[datetime.date]:
    out = []
    d = start
    while d <= end:
        if is_trading_day(d, holidays):
            out.append(d)
        d += datetime.timedelta(days=1)
    return out
