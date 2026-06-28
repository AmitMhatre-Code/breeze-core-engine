"""Canonical strike parsing and formatting for NSE/BSE options."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

Strike = float

_STRIKE_DP = 4


def _to_decimal(raw: Any) -> Decimal | None:
    if raw is None:
        return None
    s = str(raw).replace(",", "").strip()
    if not s:
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def parse_strike(raw: Any) -> Strike | None:
    """Parse strike; reject non-positive; round to 4 dp for stable equality."""
    d = _to_decimal(raw)
    if d is None or d <= 0:
        return None
    rounded = round(float(d), _STRIKE_DP)
    if rounded <= 0:
        return None
    return rounded


def strike_key(raw: Any) -> str:
    """Canonical string for Redis/dict/SQLite keys (e.g. '24000', '150.35')."""
    strike = parse_strike(raw)
    if strike is None:
        return ""
    d = Decimal(str(strike)).normalize()
    text = format(d, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def strike_for_broker(raw: Any) -> str:
    """Wire format for ICICI Breeze SDK — preserves fractional strikes."""
    key = strike_key(raw)
    if not key:
        raise ValueError(f"Invalid strike: {raw!r}")
    return key


def strikes_sorted(values: Any) -> list[Strike]:
    """Dedupe and sort strike values."""
    seen: set[Strike] = set()
    for raw in values or []:
        strike = parse_strike(raw)
        if strike is not None:
            seen.add(strike)
    return sorted(seen)


def strikes_equal(a: Any, b: Any) -> bool:
    sa = parse_strike(a)
    sb = parse_strike(b)
    return sa is not None and sb is not None and sa == sb
