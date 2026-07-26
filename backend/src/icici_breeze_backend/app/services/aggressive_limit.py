"""Aggressive-limit price derivation for the "limit_tolerance" order mode.

The tolerance mode places an ordinary limit order at a price offset from LTP so it fills
like a market order but stays bounded:

    BUY  -> LTP * (1 + tolerance/100)   (pay up to fill)
    SELL -> LTP * (1 - tolerance/100)   (undercut to fill)

The result is rounded to the exchange tick and the tolerance is clamped server-side so a
bad/hostile client value can never push the price arbitrarily far from LTP. This is the
app-side workaround the config comment refers to — a real order_type=limit submission that
reprices aggressively — and it needs nothing from ICICI.
"""
from __future__ import annotations

import icici_breeze_backend.app.core.config as cfg


class AggressiveLimitError(ValueError):
    """Raised when an aggressive limit price cannot be derived (e.g. missing/invalid LTP)."""


def clamp_tolerance_pct(tolerance_pct: float | int | str | None) -> float:
    """Coerce a requested tolerance% into the supported [0, MAX] range.

    Falls back to the configured default when the value is missing or unparseable; a
    negative value clamps to 0, an oversized value clamps to MAX.
    """
    max_pct = float(cfg.AGGRESSIVE_LIMIT_MAX_TOLERANCE_PCT)
    default_pct = float(cfg.AGGRESSIVE_LIMIT_DEFAULT_TOLERANCE_PCT)
    if tolerance_pct is None or tolerance_pct == "":
        value = default_pct
    else:
        try:
            value = float(tolerance_pct)
        except (TypeError, ValueError):
            value = default_pct
    if value != value:  # NaN
        value = default_pct
    return max(0.0, min(max_pct, value))


def round_to_tick(price: float, tick: float | None = None) -> float:
    """Round to the nearest exchange tick (default from config)."""
    t = float(tick if tick is not None else cfg.AGGRESSIVE_LIMIT_TICK_SIZE)
    if t <= 0:
        return round(float(price), 2)
    steps = round(float(price) / t)
    return round(steps * t, 2)


def compute_aggressive_limit_price(
    action: str,
    ltp: float | int | str | None,
    tolerance_pct: float | int | str | None,
    *,
    tick: float | None = None,
) -> float:
    """Derive the tick-rounded aggressive limit price for one leg.

    `action` is "Buy" or "Sell" (case-insensitive). Raises AggressiveLimitError when LTP is
    missing or not a positive number — callers must surface that rather than place a bad order.
    The returned price is always >= one tick (a BUY/SELL is never sent at 0).
    """
    try:
        ltp_f = float(ltp)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise AggressiveLimitError("Live price (LTP) is unavailable for this contract.")
    if ltp_f <= 0 or ltp_f != ltp_f:  # non-positive or NaN
        raise AggressiveLimitError("Live price (LTP) is unavailable for this contract.")

    tol = clamp_tolerance_pct(tolerance_pct) / 100.0
    side = str(action or "").strip().lower()
    if side == cfg.BUY.lower():
        raw = ltp_f * (1.0 + tol)
    elif side == cfg.SELL.lower():
        raw = ltp_f * (1.0 - tol)
    else:
        raise AggressiveLimitError(f"Unsupported order action: {action!r}")

    priced = round_to_tick(raw, tick)
    t = float(tick if tick is not None else cfg.AGGRESSIVE_LIMIT_TICK_SIZE)
    floor = t if t > 0 else 0.05
    # A hard SELL tolerance could round to <= 0 for a very cheap option; never send a zero-price order.
    if priced < floor:
        priced = round(floor, 2)
    return priced
