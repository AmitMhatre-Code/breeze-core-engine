"""The index move a scalp must catch to pay for itself -- the shadow report's bar for a hit.

A flip that the index follows by less than the trade's own round-trip charges lost money, so the
readiness verdict scores flips against a breakeven move, not an arbitrary bps figure. The charges
are the ones every bot and the backtest use (Settings -> Trading Costs, `bots/charges.py`) -- one
cost model, never a second rupee figure to keep in step with it -- priced on one lot of the
nearest expiry's at-the-money option, bought and sold at the same premium:

    cost = ChargesModel.round_trip(premium, premium, lot size)
    breakeven points = cost / (lot size x delta)        breakeven bps = points / level x 10^4

The premium is the previous session's close from the F&O bhavcopy, not a live quote: the screen
reads this every minute, and about three-quarters of a *one-lot* round trip is flat brokerage and
its GST, so a day-old premium moves the answer by a few rupees. With no bhavcopy price the cost is
the flat part alone.

Why the bar depends on how many lots
------------------------------------
Brokerage is Rs 20 per order however big the order is, so it amortises and the bar falls steeply
with size. Priced on NIFTY at a Rs 90 premium: one lot needs 0.80 bps, five lots 0.31 and ten
lots 0.24 -- a third of the one-lot figure. Judging a signal against the one-lot bar therefore
fails signals that a real position size would clear, which is why `lots` is an argument and not
an assumption. It defaults to 1 so an unsized caller gets the most conservative bar.

Why the spread is in the bar
----------------------------
The bhavcopy has no bid or ask, so this used to leave the spread out entirely and say so. That
made the bar too kind in the opposite direction: unlike brokerage, the spread scales with size
and never amortises, so at the sizes where brokerage stops mattering the spread is most of what
is left. It is taken from `scalping/spreads.spread_stats()` -- the same observed median this
deployment feeds its bot backtests, never a second number -- and `spread_source` reports whether
that was calibrated from real quotes or is still the uncalibrated default.

`SCALP_DELTA` assumes an at-the-money option. A scalper buying further out earns less per index
point and needs a bigger move, so this is the lowest bar a trade could clear, not a promise.
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import closing
from datetime import date, datetime
from typing import Any, Callable

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.timezone import IST
from icici_breeze_backend.app.services.bots import charges as trading_charges
from icici_breeze_backend.app.services.reference_data import symbol_registry

SCALP_DELTA = 0.5

# The signal covers exactly these two indices; the Security Master supplies the contract names.
_SEGMENT_FOR_LABEL: dict[str, str] = {"nifty": cfg.NFO, "sensex": cfg.BFO}

_lock = threading.Lock()
_daily_cache: dict[tuple[str, str, str], Any] = {}


def _scrip_db_path() -> str:
    return cfg.DATA_PATH + cfg.SCRIP_DB


def _connect() -> sqlite3.Connection:
    # Read-only: a plain connect would create an empty scrip DB where none exists yet.
    return sqlite3.connect(f"file:{_scrip_db_path()}?mode=ro", uri=True)


def _codes(label: str) -> tuple[str, tuple[str, ...]] | None:
    segment = _SEGMENT_FOR_LABEL.get(label)
    if segment is None:
        return None
    return segment, symbol_registry.aliases_for(label.upper(), segment)


def _nearest_expiry_lot(label: str, today: date) -> int | None:
    """Lot size of the index's nearest unexpired option expiry. Exchanges revise lot sizes on a
    schedule and far expiries list the new size first, so the nearest one is what trades today."""
    found = _codes(label)
    if found is None:
        return None
    segment, codes = found
    marks = ", ".join("?" for _ in codes)
    try:
        with closing(_connect()) as conn:
            rows = conn.execute(
                "SELECT ExpiryDate, MAX(LotSize) FROM scrip_master "
                f"WHERE SegmentCode = ? AND (ShortName IN ({marks}) OR ExchangeCode IN ({marks})) "
                "AND OptionType IN ('CE', 'PE') AND LotSize > 0 GROUP BY ExpiryDate",
                (segment, *codes, *codes),
            ).fetchall()
    except sqlite3.Error:
        return None
    best: tuple[date, int] | None = None
    for expiry, lot in rows:
        try:
            day = datetime.strptime(str(expiry), "%d-%b-%Y").date()
        except ValueError:
            continue
        if day >= today and (best is None or day < best[0]):
            best = (day, int(lot))
    return best[1] if best else None


def _atm_premium(label: str, today: date) -> float | None:
    """The nearest unexpired expiry's at-the-money premium in the latest bhavcopy: the mean close
    of the call and put at the strike nearest that day's spot (the one the scalper would buy)."""
    found = _codes(label)
    if found is None:
        return None
    segment, codes = found
    marks = ", ".join("?" for _ in codes)
    where = f"LOWER(segment) = ? AND stock_code IN ({marks})"
    params = (segment.lower(), *codes)
    try:
        with closing(_connect()) as conn:
            expiry = conn.execute(
                f"SELECT MIN(expiry_date) FROM fo_bhavcopy WHERE {where} AND expiry_date >= ?",
                (*params, today.isoformat()),
            ).fetchone()[0]
            if not expiry:
                return None
            rows = conn.execute(
                f"SELECT strike_price, ltp, spot_price FROM fo_bhavcopy WHERE {where} AND expiry_date = ?",
                (*params, expiry),
            ).fetchall()
    except sqlite3.Error:
        return None
    spot = next((float(s) for _k, _p, s in rows if s and float(s) > 0), None)
    if spot is None:
        return None
    by_strike: dict[float, list[float]] = {}
    for strike, price, _spot in rows:
        if strike is not None and price and float(price) > 0:
            by_strike.setdefault(float(strike), []).append(float(price))
    if not by_strike:
        return None
    atm = min(by_strike, key=lambda k: (abs(k - spot), k))  # ties go to the lower strike
    prices = by_strike[atm]
    return round(sum(prices) / len(prices), 2)


def _daily(kind: str, label: str, today: date | None, compute: Callable[[str, date], Any]) -> Any:
    """Cached per IST day; a miss is not cached, since reference data may still be loading."""
    day = today or datetime.now(IST).date()
    key = (kind, label, day.isoformat())
    with _lock:
        if key in _daily_cache:
            return _daily_cache[key]
    value = compute(label, day)
    if value is not None:
        with _lock:
            _daily_cache[key] = value
    return value


def lot_size(label: str, today: date | None = None) -> int | None:
    return _daily("lot", label, today, _nearest_expiry_lot)


def atm_premium(label: str, today: date | None = None) -> float | None:
    return _daily("premium", label, today, _atm_premium)


def _spread_rupees(premium: float | None, quantity: int) -> tuple[float, str, float]:
    """(rupees given up to the spread over a round trip, where that came from, the % used).

    One full spread per round trip: half on the way in, half on the way out. With no premium
    there is nothing to take a percentage of, so the spread is 0 rather than a guess."""
    try:
        from icici_breeze_backend.app.services.bots.scalping import spreads

        stats = spreads.spread_stats()
    except Exception:  # noqa: BLE001 -- an unreadable sample table must not break the bar
        return 0.0, "unavailable", 0.0
    if not premium or premium <= 0:
        return 0.0, stats.source, stats.median_spread_pct
    return stats.spread_for(float(premium)) * quantity, stats.source, stats.median_spread_pct


def breakeven(label: str, level: float | None, *, lots: int = 1) -> dict[str, Any]:
    """The round trip, its inputs, and the breakeven move in index points and bps, for a position
    of `lots` lots. `points`/`bps` are None when the lot size or the index level is not known, and
    the caller falls back.

    `charges_bps` and `spread_bps` split the bar into the half that amortises with size and the
    half that does not -- see the module docstring. `bps` is their sum, and is what a call has to
    beat."""
    lots = max(1, int(lots))
    lot = lot_size(label)
    premium = atm_premium(label)
    cost = charges = spread = points = bps = charges_bps = spread_bps = None
    spread_source = "unavailable"
    spread_pct = 0.0
    if lot:
        quantity = lot * lots
        price = premium or 0.0  # no price: the flat brokerage and its GST alone
        charges = trading_charges.load_charges().round_trip(
            price, price, quantity, exchange_code=_SEGMENT_FOR_LABEL.get(label, cfg.NFO)
        )
        spread, spread_source, spread_pct = _spread_rupees(premium, quantity)
        cost = charges + spread
        if level and level > 0:
            per_point = quantity * SCALP_DELTA
            points = cost / per_point
            bps = points / level * 1e4
            charges_bps = charges / per_point / level * 1e4
            spread_bps = spread / per_point / level * 1e4
    return {
        "cost_rupees": round(cost, 2) if cost is not None else None,
        "charges_rupees": round(charges, 2) if charges is not None else None,
        "spread_rupees": round(spread, 2) if spread is not None else None,
        "spread_source": spread_source,
        "spread_pct_of_premium": round(spread_pct, 4),
        "premium": premium,
        "lot_size": lot,
        "lots": lots,
        "quantity": (lot * lots) if lot else None,
        "delta": SCALP_DELTA,
        "index_level": level,
        "points": round(points, 2) if points is not None else None,
        "bps": round(bps, 3) if bps is not None else None,
        "charges_bps": round(charges_bps, 3) if charges_bps is not None else None,
        "spread_bps": round(spread_bps, 3) if spread_bps is not None else None,
    }


def reset_state_for_tests() -> None:
    with _lock:
        _daily_cache.clear()
