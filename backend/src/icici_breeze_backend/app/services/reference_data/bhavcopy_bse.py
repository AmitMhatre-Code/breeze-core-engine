"""BSE FO bhavcopy download and parse."""
from __future__ import annotations

import csv
import datetime as dt
import io
import logging
from typing import Callable

import requests

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.services.market_calendar import is_trading_day
from icici_breeze_backend.app.core.timezone import IST, today_ist_date
from icici_breeze_backend.app.services.reference_data.bhavcopy_common import (
    BSE_HTTP_HEADERS,
    normalize_bse_fo_bhavcopy_row,
    request_timeout,
)

_logger = logging.getLogger(__name__)


def _download_text(url: str) -> str:
    resp = requests.get(url, headers=BSE_HTTP_HEADERS, timeout=request_timeout())
    resp.raise_for_status()
    return resp.content.decode("utf-8", errors="replace")


def fetch_bse_fo_bhavcopy_for_date(day: dt.date) -> tuple[list[dict[str, str]], str] | None:
    ymd = day.strftime("%Y%m%d")
    url = cfg.BSE_FO_BHAVCOPY_URL_TEMPLATE.format(yyyymmdd=ymd)
    try:
        text = _download_text(url)
    except Exception as exc:
        _logger.debug("BSE FO bhavcopy download failed for %s: %s", day.isoformat(), exc)
        return None
    rows: list[dict[str, str]] = []
    reader = csv.DictReader(io.StringIO(text))
    for raw in reader:
        nr = normalize_bse_fo_bhavcopy_row(raw)
        if nr:
            rows.append(nr)
    if not rows:
        return None
    return rows, url


def fetch_latest_bse_fo_bhavcopy(
    *,
    lookback_days: int | None = None,
    progress_cb: Callable[[int, int, str], None] | None = None,
) -> tuple[list[dict[str, str]], dt.date, str] | None:
    lookback = max(1, int(lookback_days or cfg.REFERENCE_DATA_LOOKBACK_DAYS))
    today = today_ist_date()
    for i in range(lookback):
        day = today - dt.timedelta(days=i)
        if not is_trading_day(dt.datetime(day.year, day.month, day.day, 12, 0, tzinfo=IST)):
            continue
        if progress_cb:
            progress_cb(i + 1, lookback, f"Downloading BSE FO BhavCopy for {day.isoformat()}")
        fetched = fetch_bse_fo_bhavcopy_for_date(day)
        if fetched:
            rows, url = fetched
            return rows, day, url
    return None
