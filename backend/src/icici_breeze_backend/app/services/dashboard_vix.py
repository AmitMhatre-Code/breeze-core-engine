"""Dashboard VIX widget: INDVIX quote/history, NIFTY option chain ATM IV, expected range from ATM IV."""
import concurrent.futures
import datetime
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Tuple, TypeVar
from zoneinfo import ZoneInfo

# NSE index options: expiry = last trading at market close on expiry date (3:30 PM IST).
IST = ZoneInfo("Asia/Kolkata")
NSE_INDEX_OPTION_LTT_HOUR = 15
NSE_INDEX_OPTION_LTT_MINUTE = 30

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.market_hours import get_reference_time_for_iv_ist, is_india_market_open
from icici_breeze_backend.app.services.iv_compute import DEFAULT_R, DEFAULT_Q, expected_range_from_atm_iv, implied_volatility

_logger = logging.getLogger(__name__)

# Instrumentation: log timing for ATM IV steps at INFO level (grep for this prefix).
VIX_TIMING_PREFIX = "[vix-timing]"
# ATM IV calculation step-by-step trace with real ICICI data (grep for this prefix).
ATM_IV_TRACE_PREFIX = "[atm-iv-trace]"


def _log_timing(step: str, elapsed_ms: float, detail: str = "") -> None:
    msg = f"{VIX_TIMING_PREFIX} {step}: {elapsed_ms:.0f} ms"
    if detail:
        msg += f" ({detail})"
    _logger.info(msg)


T = TypeVar("T")
_OPTIONS_RETRIES = 3
_OPTIONS_RETRY_DELAY = 0.5


def _retry_breeze(fn: Callable[[], T], label: str) -> T:
    """Run fn up to _OPTIONS_RETRIES times with _OPTIONS_RETRY_DELAY between attempts. Return last result or raise."""
    last_err = None
    for attempt in range(_OPTIONS_RETRIES):
        try:
            return fn()
        except Exception as e:
            last_err = e
            if attempt < _OPTIONS_RETRIES - 1:
                _logger.debug("%s attempt %s failed: %s; retrying in %.1fs", label, attempt + 1, e, _OPTIONS_RETRY_DELAY)
                time.sleep(_OPTIONS_RETRY_DELAY)
    raise last_err

INDVIX_SYMBOL = "INDVIX"
NIFTY_SYMBOL = "NIFTY"
NSE = "NSE"
NFO = "NFO"
PRODUCT_CASH = "cash"
PRODUCT_OPTIONS = "options"
VIX_HISTORY_DAYS = 30


def _expiry_display_to_api(expiry: str) -> str:
    """DD-Mon-YYYY -> YYYY-MM-DDT06:00:00.000Z."""
    return datetime.datetime.strptime(expiry, "%d-%b-%Y").strftime("%Y-%m-%d") + "T06:00:00.000Z"


def _expiry_to_t_years(expiry_str: str) -> float:
    """
    Expiry string (DD-Mon-YYYY or API format) -> time to expiry in years.
    Uses Last Trading Time (LTT) = expiry date at 3:30 PM IST (NSE index option close).
    Reference time: now if market is open, else previous market close (so T matches stale LTP when closed).
    """
    s = expiry_str.removesuffix("T06:00:00.000Z").strip()
    try:
        if len(s.split("-")[0]) == 4:
            exp_date = datetime.datetime.strptime(s[:10], "%Y-%m-%d")
        else:
            exp_date = datetime.datetime.strptime(s, "%d-%b-%Y")
    except ValueError:
        return 0.0
    # LTT = expiry date at market close (3:30 PM IST)
    ltt_ist = exp_date.replace(
        hour=NSE_INDEX_OPTION_LTT_HOUR,
        minute=NSE_INDEX_OPTION_LTT_MINUTE,
        second=0,
        microsecond=0,
        tzinfo=IST,
    )
    ref_ist = get_reference_time_for_iv_ist()
    delta = (ltt_ist - ref_ist).total_seconds()
    if delta <= 0:
        return 0.0
    return delta / (365.25 * 24 * 3600)


def _get_nifty_expiries(processor) -> List[str]:
    """Return sorted list of NIFTY expiry dates (display format DD-Mon-YYYY), next 3."""
    codes = processor.fetch_stock_codes()
    for item in codes:
        if item.get("stock_code") == NIFTY_SYMBOL:
            expiries = item.get("expiry_dates") or []
            # Sort by date; filter past
            today = datetime.datetime.now()
            valid = []
            for e in expiries:
                try:
                    dt = datetime.datetime.strptime(e, "%d-%b-%Y")
                    if dt.date() >= today.date():
                        valid.append((dt, e))
                except ValueError:
                    continue
            valid.sort(key=lambda x: x[0])
            return [e for _, e in valid[:3]]
    return []


def _quote_nse_cash(breeze, stock_code: str) -> Optional[Dict]:
    """Get NSE cash quote. Returns first success payload or None. Retries on transient failure."""
    if breeze is None:
        return None

    def _get() -> Optional[Dict]:
        r = breeze.get_quotes(
            stock_code=stock_code,
            exchange_code=NSE,
            product_type=PRODUCT_CASH,
            expiry_date="",
            right="",
            strike_price="",
        )
        if isinstance(r, dict) and (r.get("Status") or r.get("status")) == 200:
            succ = r.get("Success") or r.get("success")
            if isinstance(succ, list) and succ:
                return succ[0]
            if isinstance(succ, dict):
                return succ
        return None

    try:
        out = _retry_breeze(lambda: _get(), f"get_quotes NSE cash {stock_code}")
        return out
    except Exception as e:
        _logger.warning("get_quotes NSE cash %s failed after retries: %s", stock_code, e)
    return None


def _historical_vix(breeze, from_date: str, to_date: str) -> List[Dict[str, Any]]:
    """Get INDVIX daily history. Returns list of {date, value}."""
    if breeze is None:
        return []
    try:
        r = breeze.get_historical_data_v2(
            interval="1day",
            from_date=from_date,
            to_date=to_date,
            stock_code=INDVIX_SYMBOL,
            exchange_code=NSE,
            product_type=PRODUCT_CASH,
        )
        if not isinstance(r, dict) or (r.get("Status") or r.get("status")) != 200:
            return []
        succ = r.get("Success") or r.get("success")
        if not isinstance(succ, list):
            return []
        out = []
        for row in succ:
            dt = row.get("datetime") or row.get("date")
            close = row.get("close")
            if dt is not None and close is not None:
                try:
                    if isinstance(close, str):
                        close = float(close)
                    out.append({"date": str(dt)[:10], "value": round(float(close), 2)})
                except (TypeError, ValueError):
                    pass
        return out
    except Exception as e:
        _logger.warning("get_historical_data_v2 INDVIX failed: %s", e)
        return []


def _option_chain(breeze, expiry_api: str, right: str) -> List[Dict]:
    """Get NIFTY option chain for one expiry and right. Returns list of strike info with ltp. Retries on transient failure."""
    if breeze is None:
        return []

    def _get() -> List[Dict]:
        r = breeze.get_option_chain_quotes(
            stock_code=NIFTY_SYMBOL,
            exchange_code=NFO,
            product_type=PRODUCT_OPTIONS,
            right=right,
            expiry_date=expiry_api,
        )
        if not isinstance(r, dict) or (r.get("Status") or r.get("status")) != 200:
            return []
        succ = r.get("Success") or r.get("success")
        return list(succ) if isinstance(succ, list) else []

    try:
        return _retry_breeze(lambda: _get(), f"get_option_chain_quotes NIFTY {expiry_api} {right}")
    except Exception as e:
        _logger.warning("get_option_chain_quotes NIFTY %s %s failed after retries: %s", expiry_api, right, e)
        return []


def _atm_strike(spot: float, strikes: List[float]) -> Optional[float]:
    """Strike closest to spot (ATM)."""
    if not strikes:
        return None
    return min(strikes, key=lambda k: abs(k - spot))


def _strikes_sorted_from_chain(calls: List[Dict], puts: List[Dict]) -> List[float]:
    """Unique sorted strike prices from option chain rows that have ltp > 0."""
    strikes = set()
    for row in calls:
        try:
            k = float(row.get("strike_price", 0))
            ltp = float(row.get("ltp") or row.get("last") or 0)
            if k > 0 and ltp > 0:
                strikes.add(k)
        except (TypeError, ValueError):
            pass
    for row in puts:
        try:
            k = float(row.get("strike_price", 0))
            ltp = float(row.get("ltp") or row.get("last") or 0)
            if k > 0 and ltp > 0:
                strikes.add(k)
        except (TypeError, ValueError):
            pass
    return sorted(strikes)


def _compute_atm_iv_only(
    calls: List[Dict],
    puts: List[Dict],
    spot: float,
    t_years: float,
) -> Tuple[Dict[float, float], List[float]]:
    """Compute IV for ATM strike only. Returns (strike_to_iv, [atm_k]) with at most one strike."""
    strikes_sorted = _strikes_sorted_from_chain(calls, puts)
    if not strikes_sorted:
        return {}, []
    atm_k = _atm_strike(spot, strikes_sorted)
    call_ltp, put_ltp = _build_ltp_maps(calls, puts)
    strike_to_iv: Dict[float, float] = {}
    if atm_k is not None:
        iv_call = implied_volatility(call_ltp.get(atm_k), spot, atm_k, t_years, "call") if atm_k in call_ltp else None
        iv_put = implied_volatility(put_ltp.get(atm_k), spot, atm_k, t_years, "put") if atm_k in put_ltp else None
        if iv_call is not None and iv_put is not None:
            strike_to_iv[atm_k] = (iv_call + iv_put) / 2
        elif iv_call is not None:
            strike_to_iv[atm_k] = iv_call
        elif iv_put is not None:
            strike_to_iv[atm_k] = iv_put
    return strike_to_iv, [atm_k] if atm_k is not None else []


def _compute_iv_for_strike_window(
    calls: List[Dict],
    puts: List[Dict],
    spot: float,
    t_years: float,
    window_size: int = 4,
) -> Tuple[Dict[float, float], List[float]]:
    """
    Compute IV only for ATM and the next `window_size` strikes below and above spot.
    Returns (strike_to_iv, strikes_in_order) for the window (4 below, ATM, 4 above).
    """
    t0 = time.perf_counter()
    strikes_sorted = _strikes_sorted_from_chain(calls, puts)
    _log_timing("iv_window.strikes_from_chain", (time.perf_counter() - t0) * 1000, f"{len(strikes_sorted)} strikes")
    if not strikes_sorted:
        return {}, []

    atm_k = _atm_strike(spot, strikes_sorted)
    try:
        idx = strikes_sorted.index(atm_k)
    except ValueError:
        idx = 0
    lo = max(0, idx - window_size)
    hi = min(len(strikes_sorted), idx + window_size + 1)
    strikes_window = strikes_sorted[lo:hi]

    t1 = time.perf_counter()
    call_ltp = {}
    for row in calls:
        try:
            k = float(row.get("strike_price", 0))
            ltp = float(row.get("ltp") or row.get("last") or 0)
            if k > 0 and ltp > 0:
                call_ltp[k] = ltp
        except (TypeError, ValueError):
            pass
    put_ltp = {}
    for row in puts:
        try:
            k = float(row.get("strike_price", 0))
            ltp = float(row.get("ltp") or row.get("last") or 0)
            if k > 0 and ltp > 0:
                put_ltp[k] = ltp
        except (TypeError, ValueError):
            pass
    _log_timing("iv_window.build_ltp_maps", (time.perf_counter() - t1) * 1000, f"call_ltp={len(call_ltp)} put_ltp={len(put_ltp)}")

    strike_to_iv: Dict[float, float] = {}
    t2 = time.perf_counter()
    for k in strikes_window:
        iv_call = implied_volatility(call_ltp[k], spot, k, t_years, "call") if k in call_ltp else None
        iv_put = implied_volatility(put_ltp[k], spot, k, t_years, "put") if k in put_ltp else None
        if iv_call is not None and iv_put is not None:
            strike_to_iv[k] = (iv_call + iv_put) / 2
        elif iv_call is not None:
            strike_to_iv[k] = iv_call
        elif iv_put is not None:
            strike_to_iv[k] = iv_put
    _log_timing("iv_window.implied_volatility_loop", (time.perf_counter() - t2) * 1000, f"{len(strikes_window)} strikes")
    return strike_to_iv, strikes_window


def _build_ltp_maps(calls: List[Dict], puts: List[Dict]) -> Tuple[Dict[float, float], Dict[float, float]]:
    """Build strike -> LTP from option chain rows (same logic as _compute_iv_for_strike_window)."""
    call_ltp: Dict[float, float] = {}
    for row in calls:
        try:
            k = float(row.get("strike_price", 0))
            ltp = float(row.get("ltp") or row.get("last") or 0)
            if k > 0 and ltp > 0:
                call_ltp[k] = ltp
        except (TypeError, ValueError):
            pass
    put_ltp: Dict[float, float] = {}
    for row in puts:
        try:
            k = float(row.get("strike_price", 0))
            ltp = float(row.get("ltp") or row.get("last") or 0)
            if k > 0 and ltp > 0:
                put_ltp[k] = ltp
        except (TypeError, ValueError):
            pass
    return call_ltp, put_ltp


def _option_chain_oi_metrics(
    calls: List[Dict], puts: List[Dict]
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """
    From option chain rows, compute put_call_ratio and strikes with highest call/put OI.
    Returns (put_call_ratio, strike_highest_call_oi, strike_highest_put_oi).
    """
    def _oi(row: Dict) -> float:
        try:
            oi = row.get("open_interest")
            if oi is None:
                return 0.0
            return float(oi)
        except (TypeError, ValueError):
            return 0.0

    total_call_oi = sum(_oi(r) for r in calls)
    total_put_oi = sum(_oi(r) for r in puts)
    put_call_ratio = (total_put_oi / total_call_oi) if total_call_oi > 0 else None

    strike_highest_call_oi: Optional[float] = None
    if calls:
        best = max(calls, key=lambda r: _oi(r))
        try:
            k = float(best.get("strike_price", 0))
            if k > 0:
                strike_highest_call_oi = k
        except (TypeError, ValueError):
            pass

    strike_highest_put_oi: Optional[float] = None
    if puts:
        best = max(puts, key=lambda r: _oi(r))
        try:
            k = float(best.get("strike_price", 0))
            if k > 0:
                strike_highest_put_oi = k
        except (TypeError, ValueError):
            pass

    return put_call_ratio, strike_highest_call_oi, strike_highest_put_oi


def _log_atm_iv_trace(
    spot: float,
    nifty_quote: Optional[Dict],
    exp: str,
    t_years: float,
    calls: List[Dict],
    puts: List[Dict],
    strikes_window: List[float],
    strike_to_iv: Dict[float, float],
    atm_k: Optional[float],
) -> None:
    """
    Log each step of ATM IV calculation with real data from ICICI (NIFTY quote + option chain).
    Grep logs for ATM_IV_TRACE_PREFIX to see the full trace.
    """
    _logger.info("%s === ATM IV calculation trace (real ICICI data) ===", ATM_IV_TRACE_PREFIX)

    # Step 1: NIFTY spot source
    _logger.info(
        "%s Step 1 - NIFTY spot: used spot = %.2f (from get_quotes NSE cash NIFTY)",
        ATM_IV_TRACE_PREFIX, spot,
    )
    if nifty_quote:
        ltp_raw = nifty_quote.get("ltp")
        last_raw = nifty_quote.get("last")
        spot_price_raw = nifty_quote.get("spot_price")
        _logger.info(
            "%s Step 1 - Raw quote keys used: ltp=%s, last=%s, spot_price=%s (value chosen: first non-empty)",
            ATM_IV_TRACE_PREFIX, ltp_raw, last_raw, spot_price_raw,
        )

    # Step 2: Expiry and time to expiry (LTT = expiry date 3:30 PM IST)
    ref_ist = get_reference_time_for_iv_ist()
    ref_label = "now (market open)" if is_india_market_open(ref_ist) else "previous market close (market closed)"
    _logger.info(
        "%s Step 2 - Reference time for T: %s (%s)",
        ATM_IV_TRACE_PREFIX, ref_ist.strftime("%Y-%m-%d %H:%M:%S %Z"), ref_label,
    )
    _logger.info(
        "%s Step 2 - First expiry: %s -> t_years = %.6f (time from reference to LTT, 3:30 PM IST on expiry date)",
        ATM_IV_TRACE_PREFIX, exp, t_years,
    )

    # Step 3: Sample raw option chain row from ICICI (so we see exact field names/values)
    if calls:
        sample = calls[0]
        _logger.info(
            "%s Step 3 - Sample call row (ICICI get_option_chain_quotes): keys=%s",
            ATM_IV_TRACE_PREFIX, list(sample.keys()),
        )
        _logger.info(
            "%s Step 3 - Sample call: strike_price=%s, ltp=%s, last=%s (LTP used = ltp or last)",
            ATM_IV_TRACE_PREFIX,
            sample.get("strike_price"), sample.get("ltp"), sample.get("last"),
        )
    if puts:
        sample = puts[0]
        _logger.info(
            "%s Step 3 - Sample put row: strike_price=%s, ltp=%s, last=%s",
            ATM_IV_TRACE_PREFIX,
            sample.get("strike_price"), sample.get("ltp"), sample.get("last"),
        )

    # Step 4: ATM strike selection
    _logger.info(
        "%s Step 4 - Strikes in window (4 below ATM, ATM, 4 above): %s",
        ATM_IV_TRACE_PREFIX, strikes_window,
    )
    _logger.info(
        "%s Step 4 - ATM strike = strike closest to spot: atm_k = %.2f (spot = %.2f)",
        ATM_IV_TRACE_PREFIX, atm_k if atm_k is not None else 0, spot,
    )

    if atm_k is None:
        _logger.info("%s (No ATM strike; trace ends)", ATM_IV_TRACE_PREFIX)
        return

    call_ltp, put_ltp = _build_ltp_maps(calls, puts)
    call_ltp_atm = call_ltp.get(atm_k)
    put_ltp_atm = put_ltp.get(atm_k)

    # Step 5: LTPs used for ATM
    _logger.info(
        "%s Step 5 - ATM option premiums (from ICICI): call_ltp = %s, put_ltp = %s",
        ATM_IV_TRACE_PREFIX, call_ltp_atm, put_ltp_atm,
    )

    # Step 6: IV from call and put (Black–Scholes inverse)
    _logger.info(
        "%s Step 6 - Black–Scholes IV inputs: spot=%.2f, strike=%.2f, t_years=%.6f, r=%.4f, q=%.4f",
        ATM_IV_TRACE_PREFIX, spot, atm_k, t_years, DEFAULT_R, DEFAULT_Q,
    )
    iv_call = implied_volatility(call_ltp_atm, spot, atm_k, t_years, "call") if call_ltp_atm else None
    iv_put = implied_volatility(put_ltp_atm, spot, atm_k, t_years, "put") if put_ltp_atm else None
    _logger.info(
        "%s Step 6 - implied_volatility(call): price=%.4f -> iv_call = %s (annualized)",
        ATM_IV_TRACE_PREFIX, call_ltp_atm or 0, iv_call,
    )
    _logger.info(
        "%s Step 6 - implied_volatility(put):  price=%.4f -> iv_put  = %s (annualized)",
        ATM_IV_TRACE_PREFIX, put_ltp_atm or 0, iv_put,
    )

    # Step 7: ATM IV = average of call and put IV
    atm_iv = strike_to_iv.get(atm_k)
    _logger.info(
        "%s Step 7 - ATM IV = (iv_call + iv_put) / 2 = (%s + %s) / 2 = %s (displayed as %.2f%%)",
        ATM_IV_TRACE_PREFIX, iv_call, iv_put, atm_iv, (atm_iv * 100) if atm_iv else 0,
    )

    # Step 8: Expected range from ATM IV
    if atm_iv and atm_iv > 0 and t_years > 0:
        import math
        lower, upper, move_pct = expected_range_from_atm_iv(spot, atm_iv, t_years)
        sqrt_t = math.sqrt(t_years)
        _logger.info(
            "%s Step 8 - Expected range: sigma*sqrt(T) = %.6f; lower = spot*exp(-sigma*sqrt(T)) = %.2f; upper = spot*exp(sigma*sqrt(T)) = %.2f",
            ATM_IV_TRACE_PREFIX, atm_iv * sqrt_t, lower, upper,
        )
        _logger.info(
            "%s Step 8 - expected_range = [%.2f, %.2f], expected_move_pct = %.2f%%",
            ATM_IV_TRACE_PREFIX, lower, upper, move_pct * 100,
        )
    _logger.info("%s === end ATM IV trace ===", ATM_IV_TRACE_PREFIX)


def fetch_vix_core(user_id: str, processor) -> Dict[str, Any]:
    """
    Fast payload: current_vix, nifty_spot, vix_30d only (no option chains).
    Use so the page can show VIX and NIFTY quickly; then call fetch_vix_options for ATM IV etc.
    """
    result = {
        "current_vix": None,
        "nifty_spot": None,
        "vix_30d": [],
        "error": None,
    }
    breeze = processor.get_session_breeze(user_id)
    if breeze is None:
        result["error"] = "Session unavailable. Please log in again."
        return result

    vix_quote = _quote_nse_cash(breeze, INDVIX_SYMBOL)
    if vix_quote:
        try:
            result["current_vix"] = round(float(vix_quote.get("ltp")), 2)
        except (TypeError, ValueError):
            pass

    nifty_quote = _quote_nse_cash(breeze, NIFTY_SYMBOL)
    if nifty_quote:
        try:
            result["nifty_spot"] = round(float(nifty_quote.get("ltp") or nifty_quote.get("spot_price") or 0), 2)
        except (TypeError, ValueError):
            pass

    end_d = datetime.datetime.now()
    start_d = end_d - datetime.timedelta(days=VIX_HISTORY_DAYS)
    from_iso = start_d.strftime("%Y-%m-%dT05:30:00.000Z")
    to_iso = end_d.strftime("%Y-%m-%dT15:30:00.000Z")
    result["vix_30d"] = _historical_vix(breeze, from_iso, to_iso)
    return result


def fetch_vix_options(user_id: str, processor) -> Dict[str, Any]:
    """
    Payload: nifty_spot, atm_iv, expected_range, expected_move_pct, put_call_ratio, strike_highest_*. First expiry only.
    """
    result = {
        "nifty_spot": None,
        "next_expiry": None,
        "atm_iv": None,
        "expected_range": None,
        "expected_move_pct": None,
        "put_call_ratio": None,
        "strike_highest_call_oi": None,
        "strike_highest_put_oi": None,
        "error": None,
    }
    breeze = processor.get_session_breeze(user_id)
    if breeze is None:
        result["error"] = "Session unavailable. Please log in again."
        return result

    nifty_quote = _quote_nse_cash(breeze, NIFTY_SYMBOL)
    spot = None
    if nifty_quote:
        try:
            spot = float(nifty_quote.get("ltp") or nifty_quote.get("last") or nifty_quote.get("spot_price") or 0)
            result["nifty_spot"] = round(spot, 2)
        except (TypeError, ValueError):
            pass

    if spot is None or spot <= 0:
        return result

    expiries_display = _get_nifty_expiries(processor)
    if not expiries_display:
        return result

    exp = expiries_display[0]
    result["next_expiry"] = exp
    expiry_api = _expiry_display_to_api(exp)
    t_years = _expiry_to_t_years(exp)
    if t_years <= 0:
        return result

    calls = _option_chain(breeze, expiry_api, "call")
    puts = _option_chain(breeze, expiry_api, "put")
    pcr, strike_call_oi, strike_put_oi = _option_chain_oi_metrics(calls, puts)
    if pcr is not None:
        result["put_call_ratio"] = round(pcr, 2)
    result["strike_highest_call_oi"] = strike_call_oi
    result["strike_highest_put_oi"] = strike_put_oi
    strike_to_iv, atm_strikes = _compute_atm_iv_only(calls, puts, spot, t_years)
    if not strike_to_iv:
        return result

    atm_k = _atm_strike(spot, atm_strikes)
    if atm_k is not None:
        atm_iv = strike_to_iv.get(atm_k)
        if atm_iv is not None:
            result["atm_iv"] = round(atm_iv * 100, 2)
            lower, upper, move_pct = expected_range_from_atm_iv(spot, atm_iv, t_years)
            result["expected_range"] = [lower, upper]
            result["expected_move_pct"] = round(move_pct * 100, 2)
    return result


def fetch_vix_options_atm_skew(user_id: str, processor) -> Dict[str, Any]:
    """
    First-expiry only: nifty_spot, atm_iv, expected_range. Use so frontend can show these as soon as ready.
    """
    t_total = time.perf_counter()
    result = {
        "nifty_spot": None,
        "next_expiry": None,
        "atm_iv": None,
        "expected_range": None,
        "expected_move_pct": None,
        "put_call_ratio": None,
        "strike_highest_call_oi": None,
        "strike_highest_put_oi": None,
        "error": None,
    }
    breeze = processor.get_session_breeze(user_id)
    if breeze is None:
        result["error"] = "Session unavailable. Please log in again."
        return result

    t0 = time.perf_counter()
    nifty_quote = _quote_nse_cash(breeze, NIFTY_SYMBOL)
    _log_timing("atm_skew.nifty_quote", (time.perf_counter() - t0) * 1000)
    spot = None
    if nifty_quote:
        try:
            spot = float(nifty_quote.get("ltp") or nifty_quote.get("last") or nifty_quote.get("spot_price") or 0)
            result["nifty_spot"] = round(spot, 2)
        except (TypeError, ValueError):
            pass

    if spot is None or spot <= 0:
        result["error"] = "NIFTY quote unavailable. Try again during market hours or use Retry."
        return result

    t1 = time.perf_counter()
    expiries_display = _get_nifty_expiries(processor)
    _log_timing("atm_skew.get_expiries", (time.perf_counter() - t1) * 1000, f"{len(expiries_display or [])} expiries")
    if not expiries_display:
        result["error"] = "No option expiries found."
        return result

    exp = expiries_display[0]
    result["next_expiry"] = exp
    expiry_api = _expiry_display_to_api(exp)
    t_years = _expiry_to_t_years(exp)
    if t_years <= 0:
        result["error"] = "Expiry not valid for IV calculation."
        return result

    try:
        t2 = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            fut_call = executor.submit(_option_chain, breeze, expiry_api, "call")
            fut_put = executor.submit(_option_chain, breeze, expiry_api, "put")
            calls = fut_call.result()
            puts = fut_put.result()
        _log_timing("atm_skew.option_chain_parallel", (time.perf_counter() - t2) * 1000, f"call={len(calls)} put={len(puts)}")
        pcr, strike_call_oi, strike_put_oi = _option_chain_oi_metrics(calls, puts)
        if pcr is not None:
            result["put_call_ratio"] = round(pcr, 2)
        result["strike_highest_call_oi"] = strike_call_oi
        result["strike_highest_put_oi"] = strike_put_oi
        t4 = time.perf_counter()
        strike_to_iv, atm_strikes = _compute_atm_iv_only(calls, puts, spot, t_years)
        _log_timing("atm_skew.compute_iv_window", (time.perf_counter() - t4) * 1000)
    except Exception:
        raise

    if not strike_to_iv:
        result["error"] = "Option chain data unavailable. Try Retry."
        return result

    atm_k = _atm_strike(spot, atm_strikes)
    _log_atm_iv_trace(
        spot, nifty_quote, exp, t_years, calls, puts,
        atm_strikes, strike_to_iv, atm_k,
    )
    if atm_k is not None:
        atm_iv = strike_to_iv.get(atm_k)
        if atm_iv is not None:
            result["atm_iv"] = round(atm_iv * 100, 2)
            lower, upper, move_pct = expected_range_from_atm_iv(spot, atm_iv, t_years)
            result["expected_range"] = [lower, upper]
            result["expected_move_pct"] = round(move_pct * 100, 2)
    _log_timing("atm_skew.TOTAL", (time.perf_counter() - t_total) * 1000)
    return result


def fetch_vix_data(user_id: str, processor) -> Dict[str, Any]:
    """
    Build full dashboard VIX payload (core + options). Kept for backward compatibility.
    """
    core = fetch_vix_core(user_id, processor)
    if core.get("error"):
        return {**core, "expected_move_pct": None, "expected_range": None, "atm_iv": None}
    opts = fetch_vix_options(user_id, processor)
    return {
        "current_vix": core.get("current_vix"),
        "nifty_spot": opts.get("nifty_spot") or core.get("nifty_spot"),
        "next_expiry": opts.get("next_expiry"),
        "expected_move_pct": opts.get("expected_move_pct"),
        "expected_range": opts.get("expected_range"),
        "vix_30d": core.get("vix_30d", []),
        "atm_iv": opts.get("atm_iv"),
        "put_call_ratio": opts.get("put_call_ratio"),
        "strike_highest_call_oi": opts.get("strike_highest_call_oi"),
        "strike_highest_put_oi": opts.get("strike_highest_put_oi"),
        "error": core.get("error") or opts.get("error"),
    }
