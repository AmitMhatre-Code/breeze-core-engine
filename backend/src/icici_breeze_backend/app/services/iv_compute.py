"""Black-Scholes implied volatility and expected range from ATM IV."""
import math
import logging
from typing import Optional, Tuple

_logger = logging.getLogger(__name__)

# Risk-free rate and dividend yield (annualized). Adjust if needed.
DEFAULT_R = 0.07
DEFAULT_Q = 0.0


def _norm_cdf(x: float) -> float:
    """Standard normal CDF using error function."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price_call(
    spot: float,
    strike: float,
    t: float,
    sigma: float,
    r: float = DEFAULT_R,
    q: float = DEFAULT_Q,
) -> float:
    """Black-Scholes call price. t = time to expiry in years."""
    if t <= 0 or sigma <= 0:
        return max(0.0, spot - strike)
    sqrt_t = math.sqrt(t)
    d1 = (math.log(spot / strike) + (r - q + 0.5 * sigma * sigma) * t) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    return spot * math.exp(-q * t) * _norm_cdf(d1) - strike * math.exp(-r * t) * _norm_cdf(d2)


def bs_price_put(
    spot: float,
    strike: float,
    t: float,
    sigma: float,
    r: float = DEFAULT_R,
    q: float = DEFAULT_Q,
) -> float:
    """Black-Scholes put price. t = time to expiry in years."""
    if t <= 0 or sigma <= 0:
        return max(0.0, strike - spot)
    sqrt_t = math.sqrt(t)
    d1 = (math.log(spot / strike) + (r - q + 0.5 * sigma * sigma) * t) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    return strike * math.exp(-r * t) * _norm_cdf(-d2) - spot * math.exp(-q * t) * _norm_cdf(-d1)


def implied_volatility(
    price: float,
    spot: float,
    strike: float,
    t: float,
    option_type: str,
    r: float = DEFAULT_R,
    q: float = DEFAULT_Q,
) -> Optional[float]:
    """
    Compute implied volatility from option price.
    option_type: 'call' or 'put'.
    Returns sigma (annualized) or None if not found.
    """
    if t <= 0 or price <= 0 or spot <= 0 or strike <= 0:
        return None
    try:
        from scipy.optimize import brentq
    except ImportError:
        _logger.warning("scipy not available; IV computation skipped")
        return None

    def objective(sig: float) -> float:
        if option_type and option_type.lower() == "put":
            return bs_price_put(spot, strike, t, sig, r, q) - price
        return bs_price_call(spot, strike, t, sig, r, q) - price

    try:
        # IV typically in [0.01, 3.0] for annualized
        iv = brentq(objective, 1e-4, 3.0, maxiter=100)
        return round(iv, 6)
    except (ValueError, ZeroDivisionError):
        return None


def expected_range_from_atm_iv(
    spot: float,
    atm_iv: float,
    t_years: float,
) -> Tuple[float, float, float]:
    """
    1-SD lognormal expected range at expiry.
    Returns (lower, upper, move_pct).
    move_pct = sigma * sqrt(T) in percentage (e.g. 0.02 for 2%).
    """
    if t_years <= 0 or atm_iv <= 0:
        return spot, spot, 0.0
    sqrt_t = math.sqrt(t_years)
    lower = spot * math.exp(-atm_iv * sqrt_t)
    upper = spot * math.exp(atm_iv * sqrt_t)
    move_pct = atm_iv * sqrt_t
    return round(lower, 2), round(upper, 2), round(move_pct, 6)
