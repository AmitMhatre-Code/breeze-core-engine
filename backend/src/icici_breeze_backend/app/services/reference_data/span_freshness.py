"""Is the loaded SPAN file current? Used to warn before a backtest sizes off it.

A file is outdated when its source date is older than the most recent trading day on or
before today, per the exchange calendar in Settings. Saturday is therefore fine on Friday's
file (or on Monday's beginning-of-day file, which is newer still), and Monday morning is not
fine on Friday's.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from typing import Any

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.timezone import IST, today_ist_date

_EXCHANGES = ((cfg.NFO, "NSE"), (cfg.BFO, "BSE"))


def latest_trading_day(today: dt.date | None = None) -> dt.date:
    """The most recent trading day on or before `today`."""
    from icici_breeze_backend.app.services.market_calendar import is_trading_day

    day = today or today_ist_date()
    for _ in range(15):
        try:
            if is_trading_day(dt.datetime(day.year, day.month, day.day, 12, 0, tzinfo=IST)):
                return day
        except Exception:  # noqa: BLE001 - calendar unreadable: weekdays only
            if day.weekday() < 5:
                return day
        day -= dt.timedelta(days=1)
    return day


def _loaded_source_date(exchange_code: str) -> str | None:
    try:
        with sqlite3.connect(cfg.DATA_PATH + cfg.SCRIP_DB) as conn:
            row = conn.execute(
                "SELECT MAX(source_date) FROM exchange_margin_baseline WHERE exchange_code = ?",
                (exchange_code,),
            ).fetchone()
    except sqlite3.Error:
        return None
    return str(row[0]) if row and row[0] else None


def span_file_freshness(today: dt.date | None = None) -> dict[str, Any]:
    """Per exchange: loaded source date, the date it should be at least, and whether it is behind."""
    expected = latest_trading_day(today)
    expected_ymd = expected.strftime("%Y%m%d")
    out: dict[str, Any] = {"expected_source_date": expected_ymd, "exchanges": {}, "outdated": False}
    for code, label in _EXCHANGES:
        loaded = _loaded_source_date(code)
        outdated = loaded is None or loaded < expected_ymd
        out["exchanges"][code] = {
            "label": label,
            "source_date": loaded,
            "outdated": outdated,
        }
        out["outdated"] = out["outdated"] or outdated
    return out


def outdated_message(exchange_code: str, today: dt.date | None = None) -> str | None:
    """A one-line warning for `exchange_code`'s SPAN file, or None when it is current."""
    fresh = span_file_freshness(today)
    ex = fresh["exchanges"].get(exchange_code)
    if not ex or not ex["outdated"]:
        return None
    if not ex["source_date"]:
        return f"No {ex['label']} SPAN file is loaded; margins fall back to ICICI's calculator."
    loaded = dt.datetime.strptime(ex["source_date"], "%Y%m%d").strftime("%d-%b-%Y")
    want = dt.datetime.strptime(fresh["expected_source_date"], "%Y%m%d").strftime("%d-%b-%Y")
    return (
        f"The {ex['label']} SPAN file is outdated: it is for {loaded}, but the latest trading day "
        f"is {want}. Margins sized from it may be off."
    )
