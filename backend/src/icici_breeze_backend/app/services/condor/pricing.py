"""Delta for the condor engine: a line-for-line port of the Portfolio leg-delta model.

The Portfolio card shows the engine's numbers next to the leg-delta column, which is computed
in the browser by `frontend/src/lib/strategy-builder/greeks.ts`. If the two models disagreed,
a roll could fire on a delta the user cannot see on the same screen. So this module mirrors it
exactly, and `test_condor_pricing.py` checks both against one shared fixture:

* time to 15:30 IST on expiry day, 365-day year, floored at one minute (`expiry.ts`);
* the forward from put-call parity at the five trusted strikes nearest spot, carried as an
  implied yield q, rejected beyond a 3% basis (`impliedForward`);
* per-side smiles from trusted quotes (two-sided book, relative spread <= 10%), linear in
  log-moneyness, flat beyond the ends (`chainIv.ts`);
* a strike's own LTP IV when its side has fewer than two anchors, else the ATM IV;
* the bisection IV solver (`blackScholes.ts`), not scipy's brentq: the answers differ in the
  last digits, and those digits decide ties when a strike is chosen by delta.

`options_strategy_engine.greeks.bs_delta` is not used: it omits the e^(-qT) carry term.
"""
from __future__ import annotations

import datetime
import math
from dataclasses import dataclass
from typing import Iterable, Literal, Mapping, Optional

Right = Literal["Call", "Put"]

DEFAULT_R = 0.07
DEFAULT_Q = 0.0
PARITY_STRIKES = 5
MAX_FORWARD_BASIS = 0.03
MAX_TRUSTED_REL_SPREAD = 0.10
_IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
_YEAR_SECONDS = 365 * 24 * 3600
MIN_YEARS_TO_CLOSE = 60 / _YEAR_SECONDS


@dataclass(frozen=True)
class Quote:
    """One contract's book. Missing values are None, never zero (#60)."""

    bid: Optional[float] = None
    ask: Optional[float] = None
    ltp: Optional[float] = None
    bid_qty: Optional[float] = None
    ask_qty: Optional[float] = None


@dataclass(frozen=True)
class ChainRow:
    strike: float
    call: Optional[Quote] = None
    put: Optional[Quote] = None

    def side(self, right: Right) -> Optional[Quote]:
        return self.call if right == "Call" else self.put


# --------------------------------------------------------------------------------------
# Black-Scholes, as blackScholes.ts
# --------------------------------------------------------------------------------------


def _erf(x: float) -> float:
    """Abramowitz-Stegun 7.1.26, as blackScholes.ts -- not math.erf. The approximation is off
    by up to 1.5e-7, and parity with the Portfolio column matters more than those digits."""
    sign = -1.0 if x < 0 else 1.0
    ax = abs(x)
    t = 1.0 / (1.0 + 0.3275911 * ax)
    y = 1.0 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * math.exp(-ax * ax)
    return sign * y


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + _erf(x / math.sqrt(2.0)))


def _d12(s: float, k: float, t: float, r: float, q: float, sigma: float) -> tuple[float, float]:
    sqrt_t = math.sqrt(t)
    d1 = (math.log(s / k) + (r - q + 0.5 * sigma * sigma) * t) / (sigma * sqrt_t)
    return d1, d1 - sigma * sqrt_t


def bs_price(right: Right, s: float, k: float, t: float, sigma: float, r: float = DEFAULT_R, q: float = DEFAULT_Q) -> float:
    if t <= 0 or sigma <= 0:
        return max(0.0, s - k) if right == "Call" else max(0.0, k - s)
    d1, d2 = _d12(s, k, t, r, q, sigma)
    if right == "Call":
        return s * math.exp(-q * t) * _norm_cdf(d1) - k * math.exp(-r * t) * _norm_cdf(d2)
    return k * math.exp(-r * t) * _norm_cdf(-d2) - s * math.exp(-q * t) * _norm_cdf(-d1)


def bs_delta(right: Right, s: float, k: float, t: float, sigma: float, r: float = DEFAULT_R, q: float = DEFAULT_Q) -> float:
    if right == "Call":
        if t <= 0 or sigma <= 0:
            return 1.0 if s > k else 0.0
        d1, _ = _d12(s, k, t, r, q, sigma)
        return math.exp(-q * t) * _norm_cdf(d1)
    if t <= 0 or sigma <= 0:
        return -1.0 if s < k else 0.0
    d1, _ = _d12(s, k, t, r, q, sigma)
    return math.exp(-q * t) * (_norm_cdf(d1) - 1.0)


def implied_volatility(
    right: Right, price: float, s: float, k: float, t: float, r: float = DEFAULT_R, q: float = DEFAULT_Q
) -> Optional[float]:
    if price <= 0 or s <= 0 or k <= 0 or t <= 0:
        return None
    intrinsic = max(0.0, s - k) if right == "Call" else max(0.0, k - s)
    if price < intrinsic - 1e-6:
        return None
    lo, hi = 0.01, 3.0

    def f(sig: float) -> float:
        return bs_price(right, s, k, t, sig, r, q) - price

    if f(lo) > 0:
        return lo
    if f(hi) < 0:
        return None
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        v = f(mid)
        if abs(v) < 1e-6:
            return mid
        if v > 0:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


# --------------------------------------------------------------------------------------
# Time, as expiry.ts
# --------------------------------------------------------------------------------------


def expiry_close(expiry: datetime.date) -> datetime.datetime:
    """F&O contracts expire at the 15:30 IST close."""
    return datetime.datetime.combine(expiry, datetime.time(15, 30), tzinfo=_IST)


def years_to_expiry_close(expiry: datetime.date, now: datetime.datetime) -> float:
    if now.tzinfo is None:
        now = now.replace(tzinfo=_IST)
    return max(MIN_YEARS_TO_CLOSE, (expiry_close(expiry) - now).total_seconds() / _YEAR_SECONDS)


# --------------------------------------------------------------------------------------
# Trusted quotes and smiles, as chainIv.ts
# --------------------------------------------------------------------------------------


def _positive(v: Optional[float]) -> bool:
    return v is not None and isinstance(v, (int, float)) and math.isfinite(v) and v > 0


def trusted_mid(quote: Optional[Quote]) -> Optional[float]:
    """Mid of a two-sided book with a relative spread of at most 10%, else None."""
    if quote is None or not (_positive(quote.bid_qty) and _positive(quote.ask_qty)):
        return None
    if not (_positive(quote.bid) and _positive(quote.ask)):
        return None
    mid = (quote.bid + quote.ask) / 2.0
    return mid if mid > 0 and (quote.ask - quote.bid) / mid <= MAX_TRUSTED_REL_SPREAD else None


def _smile(rows: Iterable[ChainRow], right: Right, spot: float, t: float, q: float) -> list[tuple[float, float]]:
    points = []
    for row in rows:
        mid = trusted_mid(row.side(right))
        if mid is None:
            continue
        iv = implied_volatility(right, mid, spot, row.strike, t, DEFAULT_R, q)
        if iv is None or iv <= 0:
            continue
        points.append((math.log(row.strike / spot), iv))
    return sorted(points)


def sigma_for_strike(smile: list[tuple[float, float]], strike: float, spot: float, fallback: float) -> float:
    if len(smile) < 2 or not (spot > 0) or not (strike > 0):
        return fallback
    x = math.log(strike / spot)
    if x <= smile[0][0]:
        return smile[0][1]
    if x >= smile[-1][0]:
        return smile[-1][1]
    for (lx, liv), (hx, hiv) in zip(smile, smile[1:]):
        if lx <= x <= hx:
            if hx == lx:
                return liv
            return liv + (x - lx) / (hx - lx) * (hiv - liv)
    return fallback


# --------------------------------------------------------------------------------------
# The model, as greeks.ts
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class GreeksModel:
    spot: float
    t: float
    r: float
    q: float
    forward_source: Literal["parity", "default"]
    call_smile: tuple[tuple[float, float], ...]
    put_smile: tuple[tuple[float, float], ...]
    atm_sigma: Optional[float]
    rows: Mapping[float, ChainRow]

    def sigma(self, right: Right, strike: float) -> Optional[float]:
        """Smile, else the strike's own LTP IV, else ATM. None when none of these exist."""
        curve = list(self.call_smile if right == "Call" else self.put_smile)
        if len(curve) >= 2:
            return sigma_for_strike(curve, strike, self.spot, math.nan)
        row = self.rows.get(strike)
        quote = row.side(right) if row else None
        ltp = quote.ltp if quote else None
        if _positive(ltp):
            own = implied_volatility(right, float(ltp), self.spot, strike, self.t, self.r, self.q)
            if own is not None and own > 0:
                return own
        return self.atm_sigma

    def delta(self, right: Right, strike: float) -> Optional[float]:
        """Per-unit delta (calls 0..1, puts -1..0), or None when it cannot be priced."""
        if not (strike > 0):
            return None
        sigma = self.sigma(right, strike)
        if sigma is None or not math.isfinite(sigma):
            return None
        d = bs_delta(right, self.spot, strike, self.t, sigma, self.r, self.q)
        return d if math.isfinite(d) else None


def _implied_forward(rows: list[ChainRow], spot: float, t: float, r: float) -> Optional[float]:
    reads = []
    for row in sorted(rows, key=lambda x: abs(x.strike - spot)):
        c, p = trusted_mid(row.call), trusted_mid(row.put)
        if c is None or p is None:
            continue
        reads.append(row.strike + math.exp(r * t) * (c - p))
        if len(reads) == PARITY_STRIKES:
            break
    if not reads:
        return None
    reads.sort()
    mid = len(reads) // 2
    forward = reads[mid] if len(reads) % 2 else (reads[mid - 1] + reads[mid]) / 2.0
    if not (forward > 0) or abs(math.log(forward / spot)) > MAX_FORWARD_BASIS:
        return None
    return forward


def _atm_sigma(rows: Mapping[float, ChainRow], atm: Optional[float], spot: float, t: float, r: float, q: float) -> Optional[float]:
    if atm is None:
        return None
    row = rows.get(atm)
    ivs = []
    for right in ("Call", "Put"):
        quote = row.side(right) if row else None
        price = trusted_mid(quote)
        if price is None:
            price = quote.ltp if quote else None
        if not _positive(price):
            continue
        iv = implied_volatility(right, float(price), spot, atm, t, r, q)
        if iv is not None and iv > 0:
            ivs.append(iv)
    return sum(ivs) / len(ivs) if ivs else None


def build_greeks_model(
    rows: Iterable[ChainRow],
    spot: Optional[float],
    expiry: datetime.date,
    now: datetime.datetime,
    atm_strike: Optional[float] = None,
) -> Optional[GreeksModel]:
    """One chain (one underlying, one expiry) -> the model, or None without a usable spot.

    `atm_strike` defaults to the listed strike nearest spot, which is what the chain's own
    `atm_strike` is.
    """
    if spot is None or not (spot > 0):
        return None
    rows = list(rows)
    t = years_to_expiry_close(expiry, now)
    r = DEFAULT_R
    forward = _implied_forward(rows, spot, t, r)
    q = r - math.log(forward / spot) / t if forward is not None else DEFAULT_Q
    by_strike = {row.strike: row for row in rows}
    if atm_strike is None and by_strike:
        atm_strike = min(by_strike, key=lambda k: abs(k - spot))
    return GreeksModel(
        spot=spot,
        t=t,
        r=r,
        q=q,
        forward_source="parity" if forward is not None else "default",
        call_smile=tuple(_smile(rows, "Call", spot, t, q)),
        put_smile=tuple(_smile(rows, "Put", spot, t, q)),
        atm_sigma=_atm_sigma(by_strike, atm_strike, spot, t, r, q),
        rows=by_strike,
    )
