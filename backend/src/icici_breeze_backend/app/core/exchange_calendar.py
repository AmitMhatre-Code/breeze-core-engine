"""NSE/BSE trading holiday calendar loaded from bundled JSON."""
from __future__ import annotations

import json
from datetime import date
from functools import lru_cache
from typing import Mapping

from icici_breeze_backend.core import config as cfg


@lru_cache(maxsize=1)
def _load_holidays() -> Mapping[str, str]:
    path = cfg.DATA_PATH + "exchange_holidays.json"
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    raw = data.get("holidays") or {}
    return {str(k): str(v) for k, v in raw.items()}


def get_holiday_dates() -> frozenset[date]:
    """All exchange holidays as date objects."""
    out: set[date] = set()
    for iso in _load_holidays():
        out.add(date.fromisoformat(iso))
    return frozenset(out)


def get_holiday_name(d: date) -> str | None:
    """Holiday name for an ISO calendar date, or None if not a holiday."""
    return _load_holidays().get(d.isoformat())


def is_exchange_holiday(d: date) -> bool:
    return d.isoformat() in _load_holidays()


def clear_holiday_cache() -> None:
    """Invalidate bundled holiday cache (tests and after file updates)."""
    _load_holidays.cache_clear()
