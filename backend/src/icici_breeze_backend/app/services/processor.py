import hashlib
import math
import copy
import datetime
import sys
import time
from breeze_connect import BreezeConnect
import json
import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.strike import Strike, parse_strike, strike_for_broker, strike_key, strikes_sorted
from icici_breeze_backend.app.core.timezone import IST, now_ist, today_ist_date
import requests
import zipfile
import os
from pathlib import Path
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import csv
import re
from markupsafe import Markup

import logging
from typing import TYPE_CHECKING, Any

from icici_breeze_backend.app.repositories import parked_orders as parked_orders_repo
if TYPE_CHECKING:
    from icici_breeze_backend.audit.strategy_builder_audit import StrategyBuilderAuditSession
from icici_breeze_backend.app.services.market_calendar import (
    is_market_open,
    market_closed_reason,
)
from icici_breeze_backend.app.domain.order import ParkedOrderItem, ParkedOrderListItem

# Import ICICI client for real-time portfolio/orders fetch (Phase 5 US3)
from icici_breeze_backend.core.icici_client import icici_client  # tests patch app.services.processor.icici_client
from icici_breeze_backend.app.services.nsccl_baseline import (
    MARGIN_SOURCE_BREEZE,
    MARGIN_SOURCE_EXCHANGE,
    resolve_exchange_baseline_margin,
)
from icici_breeze_backend.app.services.options_strategy_engine.helpers import elm_addon
from icici_breeze_backend.app.services.options_strategy_engine.types import TradeLeg
from icici_breeze_backend.app.services.reference_data.bhavcopy_store import _lookup_bhav_row
from icici_breeze_backend.app.services.leg_order_redistribution import LegModStep

_logger = logging.getLogger(__name__)

from icici_breeze_backend.app.services.icici_api_pacing import (
    GlobalIciciApiLimiter,
    is_breeze_rate_limited,
    is_icici_daily_limit_exceeded,
)


def _scrip_master_connection():
    """Return a new scrip_master DB connection (use in with-block to avoid shared connection)."""
    return sqlite3.connect(cfg.DATA_PATH + cfg.SCRIP_DB)


def _expiry_display_to_api(expiry: str) -> str:
    """Convert display format (DD-Mon-YYYY) to API format (YYYY-MM-DDT06:00:00.000Z)."""
    return datetime.datetime.strptime(expiry, "%d-%b-%Y").strftime("%Y-%m-%d") + "T06:00:00.000Z"


def _icici_option_chain_enums(product_type: str, right: str) -> tuple[str, str]:
    """ICICI OptionChain expects lowercase product_type and right (matches dashboard_vix / API samples). NFO often accepts Title Case; BFO rejects it."""
    pt = str(product_type or "").strip().lower()
    if pt not in ("futures", "options"):
        pt = "options"
    rt = str(right or "").strip().lower()
    return pt, rt


def _positive_number(raw: Any) -> bool:
    try:
        return float(raw) > 0
    except (TypeError, ValueError):
        return False


def _uncovered_scan_row_has_bid_side(i: dict, exchange_code: str) -> bool:
    """Selling needs bid-side interest; BFO often returns total_buy_qty=0 in chain while LTP/bid exist (see get_full_option_chain BFO note)."""
    try:
        if int(i.get("total_buy_qty") or 0) > 0:
            return True
    except (TypeError, ValueError):
        pass
    # `None` means the source carried no order book (bhavcopy, or BSE post-close),
    # which is not evidence that nobody is bidding -- fall through to price
    # evidence on every exchange rather than declaring the strike unsellable.
    if i.get("total_buy_qty") is not None and exchange_code != cfg.BFO:
        return False
    try:
        if float(i.get("best_bid_price") or 0) > 0:
            return True
    except (TypeError, ValueError):
        pass
    try:
        if float(i.get("ltp") or 0) > 0:
            return True
    except (TypeError, ValueError):
        pass
    return False


def _expiry_api_to_display(expiry: str) -> str:
    """Convert API format (YYYY-MM-DDT06:00:00.000Z or YYYY-MM-DD) to display format (DD-Mon-YYYY)."""
    s = expiry.removesuffix("T06:00:00.000Z")
    fmt = "%Y-%m-%d" if len(s.split("-")[0]) == 4 else "%d-%b-%Y"
    return datetime.datetime.strptime(s, fmt).strftime("%d-%b-%Y")


def _scrip_master_expiry_sql_values(expiry: str) -> tuple[str, ...]:
    from icici_breeze_backend.app.services.reference_data.scrip_master_sql import (
        scrip_master_expiry_sql_values,
    )

    return scrip_master_expiry_sql_values(expiry)


def _expiry_to_breeze_place_order(expiry_date) -> str:
    """ICICI Breeze place_order expects API expiry; accept DD-Mon-YYYY or YYYY-MM-DD… from callers."""
    s = str(expiry_date).strip()
    if not s:
        return ""
    try:
        return _expiry_display_to_api(s)
    except ValueError:
        return _expiry_display_to_api(_expiry_api_to_display(s))


def _days_to_expiry(expiry_str: str) -> int:
    """Days until expiry: (expiry date − today) + 1. Accepts DD-Mon-YYYY or YYYY-MM-DD or YYYY-MM-DDT06:00:00.000Z."""
    s = expiry_str.removesuffix("T06:00:00.000Z")
    try:
        if len(s.split("-")[0]) == 4:  # YYYY-MM-DD
            future_d = datetime.datetime.strptime(s, "%Y-%m-%d").date()
        else:  # DD-Mon-YYYY
            future_d = datetime.datetime.strptime(s, "%d-%b-%Y").date()
    except ValueError:
        return 0
    return (future_d - today_ist_date()).days + 1


def _parse_option_expiry_date(raw) -> datetime.date | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    s = s.removesuffix("T06:00:00.000Z")
    try:
        if len(s.split("-")[0]) == 4:
            return datetime.datetime.strptime(s[:10], "%Y-%m-%d").date()
        return datetime.datetime.strptime(s, "%d-%b-%Y").date()
    except ValueError:
        return None


def _trade_days_to_expiry_at_fill(trade: dict) -> int | None:
    """Calendar days from trade_date to expiry_date (≥1), when both are present."""
    try:
        td_raw = trade.get("trade_date") or trade.get("trade_book_date")
        if not td_raw:
            return None
        td = datetime.datetime.strptime(str(td_raw).strip(), "%d-%b-%Y").date()
        ed = _parse_option_expiry_date(trade.get("expiry_date"))
        if ed is None:
            return None
        d = (ed - td).days
        if d < 0:
            return None
        return max(1, d)
    except (ValueError, TypeError):
        return None


def _annualized_carry_percent_on_span(
    premium: float, days_to_expiry: int, span_margin: float
) -> float:
    """(Premium / DTE) * 365 / total margin * 100, as a display percentage (e.g. 15.5 → 15.5%).

    The margin argument is SPAN alone for scans/strategies, or SPAN + ELM for portfolio carry returns.
    """
    dte = max(1, int(days_to_expiry))
    try:
        sm = float(span_margin)
        pr = float(premium)
    except (TypeError, ValueError):
        return 0.0
    if sm <= 0 or not math.isfinite(sm) or not math.isfinite(pr):
        return 0.0
    return (pr / dte) * (365.0 / sm) * 100.0


def _annualized_roi_fraction_on_span(
    pnl: float, elapsed_days: int, total_margin: float
) -> float:
    """(P&L / Total margin) * (365 / elapsed days), returned as a fraction (without *100)."""
    days = max(1, int(elapsed_days))
    try:
        margin = float(total_margin)
        net = float(pnl)
    except (TypeError, ValueError):
        return 0.0
    if margin <= 0 or not math.isfinite(margin) or not math.isfinite(net):
        return 0.0
    return (net / margin) * (365.0 / days)


def _icici_error(error_msg: str, status: int = 400) -> dict:
    """Standard ICICI API error response dict."""
    return {"Status": status, "Error": error_msg}


AGGRESSIVE_LIMIT_DISABLED_MESSAGE = (
    "Aggressive limit orders are temporarily disabled until ICICI implements native support. "
    "Place the order with an explicit limit price instead."
)


def _single_line_preview(text: str, max_len: int = 400) -> str:
    """Collapse whitespace for compact log lines."""
    one = " ".join(str(text).split())
    return one if len(one) <= max_len else one[: max_len - 3] + "..."


def _format_indian_integer_digits(n: int) -> str:
    """Format integer with Indian-style grouping (e.g. 1234567 → 12,34,567)."""
    n = int(n)
    sign = "-" if n < 0 else ""
    n = abs(n)
    s = str(n)
    if len(s) <= 3:
        return sign + s
    last3 = s[-3:]
    rest = s[:-3]
    parts = [last3]
    while rest:
        if len(rest) >= 2:
            parts.insert(0, rest[-2:])
            rest = rest[:-2]
        else:
            parts.insert(0, rest)
            rest = ""
    return sign + ",".join(parts)


def _format_inr_integer_indian(value) -> str:
    """Rupees, rounded to integer, with ₹ and Indian-style commas."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "₹0"
    if math.isnan(v) or math.isinf(v):
        return "₹0"
    return "₹" + _format_indian_integer_digits(int(round(v)))


def _format_inr_2dp_indian(value) -> str:
    """Rupees with 2 decimal places and Indian-style commas on the integer part."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "₹0.00"
    if math.isnan(v) or math.isinf(v):
        return "₹0.00"
    sign = "-" if v < 0 else ""
    cents = round(abs(v) * 100)
    rupees, paise = divmod(cents, 100)
    return f"{sign}₹{_format_indian_integer_digits(rupees)}.{paise:02d}"


def _normalize_icici_response(data: dict) -> tuple:
    """Extract status, success payload, and error message from ICICI response (handles Status/status, etc.)."""
    status = data.get("Status") or data.get("status")
    success = data.get("Success") or data.get("success") or {}
    err_msg = data.get("Error") or data.get("error") or "Unknown error"
    return status, success, err_msg


def _is_empty_portfolio_positions_response(data: dict | None) -> bool:
    """True when ICICI reports no open positions (Success null or empty list) with Status 200."""
    if not isinstance(data, dict):
        return False
    status = data.get("Status") or data.get("status")
    if status != 200:
        return False
    if "Success" in data:
        raw = data.get("Success")
    else:
        raw = data.get("success")
    if raw is None:
        return True
    return isinstance(raw, list) and len(raw) == 0


def _empty_portfolio_positions_result() -> dict:
    return {"Status": 200, "Success": [], "Error": None}


def _quote_success_rows(quote: dict | None) -> list:
    rows = (quote or {}).get("Success")
    return rows if isinstance(rows, list) else []


# Margin only needs recomputing when a leg's identity actually changes (new
# contract, qty, or action) -- not on a timer. The cache key encodes that
# identity, so an unchanged leg reuses the same span_margin_required across
# repeated /portfolio/data refreshes instead of re-calling margin_calculator.
_PORTFOLIO_MARGIN_CACHE_TTL_SECONDS = 24 * 60 * 60

# get_positions() and get_margin_situation() are each independently called by
# GET /home/data (navbar, every non-dashboard page), GET /dashboard/bootstrap, GET
# /portfolio/data, and the PB/SL protection guard's 60s warm loop -- none of which
# know about the others. A short shared cache collapses whichever of those land
# within the same few seconds into one broker round-trip. TTL is kept below the
# protection guard's minimum tick interval (15s, see squareoff_protection_guard.py
# _tick_interval_seconds) so the guard is never the one *reusing* an entry older
# than its own cadence -- worst case it free-rides on a very recent fetch someone
# else already paid for, never skips a live check on its own schedule. Only
# successful (Status 200) responses are cached: a failure must always be visible
# live so the guard's suspend/alert path is never masked or delayed by a stale hit.
_LIVE_SNAPSHOT_CACHE_TTL_SECONDS = 15


def _cached_live_snapshot(cache_key: str) -> dict | None:
    from icici_breeze_backend.app.db.redis_client import cache_get_json

    try:
        cached = cache_get_json(cache_key)
    except Exception:
        return None
    return cached if isinstance(cached, dict) else None


def _remember_live_snapshot(cache_key: str, data: dict) -> None:
    from icici_breeze_backend.app.db.redis_client import cache_set_json

    try:
        cache_set_json(cache_key, data, ex=_LIVE_SNAPSHOT_CACHE_TTL_SECONDS)
    except Exception:
        pass


def _cached_span_margin_required(cache_key: str) -> float | None:
    from icici_breeze_backend.app.db.redis_client import cache_get_json

    try:
        cached = cache_get_json(cache_key)
    except Exception:
        return None
    if cached is None:
        return None
    try:
        return float(cached)
    except (TypeError, ValueError):
        return None


def _remember_span_margin_required(cache_key: str, span_margin_required: float) -> None:
    from icici_breeze_backend.app.db.redis_client import cache_set_json

    try:
        cache_set_json(cache_key, span_margin_required, ex=_PORTFOLIO_MARGIN_CACHE_TTL_SECONDS)
    except Exception:
        pass


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "" or isinstance(v, bool):
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        if v is None or v == "" or isinstance(v, bool):
            return default
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _sum_leg_elm(legs: list) -> float | None:
    """Additive ELM across legs; None only when no leg carries an ELM figure."""
    total = 0.0
    any_elm = False
    for leg in legs:
        elm = leg.get("elm_margin_required")
        if elm is None:
            continue
        any_elm = True
        total += _safe_float(elm)
    return total if any_elm else None


def _portfolio_netted_cache_key(user_id: str, exchange_code: str, legs: list) -> str:
    """Cache key encoding the exact leg composition of a netted SPAN call, so an
    unchanged group/portfolio reuses its result across polls but any change to a
    leg's contract/qty/action busts it."""
    ident = sorted(
        f"{l.get('stock_code')}:{l.get('expiry_date')}:{l.get('strike_price')}:"
        f"{l.get('right')}:{l.get('action')}:{l.get('quantity')}"
        for l in legs
    )
    return f"portfolio_netmargin:{user_id}:{exchange_code}:" + "|".join(ident)


def build_margin_situation_from_raw(margin: dict | None, *, target_margin_ute: float = 100) -> dict:
    """Normalize raw ICICI margin API response into get_margin_situation Success shape."""
    margin_situation: dict = {}
    if margin is None:
        margin = {}
    status, success, err_msg = _normalize_icici_response(margin)
    if status == 200 and success:
        margin_situation["Status"] = 200
        margin_situation["Success"] = {}
        limit_list = success.get("limit_list") or []
        actual_margin_ute = 0
        for i in limit_list:
            actual_margin_ute += int(i.get("amount", 0))
        cash_limit = float(success.get("cash_limit", 0) or 0)
        margin_situation["Success"]["actual_margin_ute"] = actual_margin_ute
        margin_situation["Success"]["cash_limit"] = cash_limit
        margin_situation["Success"]["actual_margin_avl"] = cash_limit + actual_margin_ute
        margin_situation["Success"]["target_margin_free"] = cash_limit * (100 - target_margin_ute) / 100
        margin_situation["Success"]["limits"] = (
            margin_situation["Success"]["actual_margin_avl"] - margin_situation["Success"]["target_margin_free"]
        )
        margin_situation["Success"]["last_refresh"] = now_ist().strftime("%d-%b-%Y %H:%M:%S")
    else:
        margin_situation["Status"] = status if status is not None else 400
        margin_situation["Error"] = err_msg
    return margin_situation


def _is_icici_limit_exceeded(error_text: str) -> bool:
    return is_icici_daily_limit_exceeded(error_text)


def _breeze_limit_error(user_id: str | None = None) -> dict:
    return GlobalIciciApiLimiter.build_throttle_error(
        user_id,
        broker_error_text="daily limit exceeded",
    )


def _is_broker_rate_limited(response: dict | None) -> bool:
    """True when ICICI / Breeze indicates HTTP 429 or equivalent HTML body in Error text."""
    if not response:
        return False
    if response.get("icici_throttled"):
        return True
    try:
        st = int(response.get("Status") or response.get("status") or 0)
    except (TypeError, ValueError):
        st = 0
    err = str(response.get("Error") or response.get("error") or "")
    return is_breeze_rate_limited(st, err)


def _order_rate_limit_flags(response: dict | None) -> tuple[bool, bool]:
    """Return (rate_limited, daily_limit_exhausted) for client pacing flows."""
    if not response:
        return False, False
    if response.get("icici_throttled"):
        return True, bool(response.get("daily_limit_exhausted"))
    return _is_broker_rate_limited(response), False


from icici_breeze_backend.app.external.icici_api import fetch_customerdetails_session_token as _fetch_customerdetails_session_token
from icici_breeze_backend.app.external.icici_api import call_icici_api_direct as _call_icici_api_direct


class processor():

    def __init__(self):
        super().__init__()
        self.errors: list = []

    def get_login_url(self,user_id):
        data = self.fetch_credentials(user_id)

        if data['Status'] == 200:
            api_key = data['Success']['broker_api_key']
            login_url = "https://api.icicidirect.com/apiuser/login?api_key="+api_key
        else:
            login_url = None

        return login_url

    def fetch_credentials(self, user_id):
        """Retrieve stored credential record from SQLite database.
        The application stores only a partial secret fragment encrypted at rest.
        This method returns the database row so that calling code can decrypt the
        fragment and combine with user-provided fragment.
        
        Args:
            user_id: ID of the user whose credentials are needed
        
        Returns:
            dict with 'Status' 200 and 'Success' key containing row data or
            'Status' 400 with 'Error' on failure.
        """
        result = {}
        try:
            with sqlite3.connect(cfg.DATA_PATH + cfg.USERS_DB) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT broker_api_key, secret_fragment, encryption_salt, fragment_position "
                    "FROM user_credentials WHERE user_id = ? AND is_active = 1",
                    (user_id,)
                )
                row = cursor.fetchone()
            if row:
                result['Status'] = 200
                result['Success'] = {
                    'broker_api_key': row[0],
                    'secret_fragment': row[1],
                    'encryption_salt': row[2],
                    'fragment_position': row[3],
                }
            else:
                _logger.warning("No active credentials for user_id=%s", user_id)
                result['Status'] = 400
                result['Error'] = "No credentials found. Please register your broker API credentials."
        except Exception as e:
            _logger.exception("Error reading credentials from DB for user_id=%s: %s", user_id, e)
            result['Status'] = 400
            result['Error'] = "Unable to load credentials. Please try again later."
        return result

    def get_strategy_builder_margin_source(self, user_id: str) -> str:
        try:
            with sqlite3.connect(cfg.DATA_PATH + cfg.USERS_DB) as conn:
                row = conn.execute(
                    "SELECT strategy_builder_margin_source FROM user_account WHERE user_id = ?",
                    (user_id,),
                ).fetchone()
            source = str(row[0]).strip().lower() if row and row[0] else MARGIN_SOURCE_BREEZE
            if source not in (MARGIN_SOURCE_BREEZE, MARGIN_SOURCE_EXCHANGE):
                return MARGIN_SOURCE_BREEZE
            return source
        except Exception:
            return MARGIN_SOURCE_BREEZE

    def _portfolio_baseline_span_margin(
        self,
        exchange_code: str,
        legs: list,
        *,
        spot: float | None = None,
        iv: float | None = None,
        time_years: float | None = None,
    ) -> dict[str, Any]:
        """Portfolio SPAN from exchange baseline when every leg resolves with risk arrays."""
        from icici_breeze_backend.app.services.reference_data.span_portfolio_scan import (
            resolve_portfolio_span_margin,
        )

        if not legs:
            return {"found": False}
        stock_code = str(legs[0].get("stock_code") or "").strip()
        expiry_raw = str(legs[0].get("expiry_date") or "").strip()
        if not stock_code or not expiry_raw:
            return {"found": False}
        if "T" in expiry_raw:
            expiry_display = _expiry_api_to_display(expiry_raw)
        else:
            expiry_display = expiry_raw
        for leg in legs[1:]:
            sc = str(leg.get("stock_code") or "").strip()
            ed = str(leg.get("expiry_date") or "").strip()
            if "T" in ed:
                ed = _expiry_api_to_display(ed)
            if sc != stock_code or ed != expiry_display:
                return {"found": False}
        if time_years is None and expiry_display:
            from icici_breeze_backend.app.services.options_strategy_engine.helpers import (
                days_to_expiry,
            )

            time_years = max(1, days_to_expiry(expiry_display)) / 365.0
        return resolve_portfolio_span_margin(
            exchange_code,
            stock_code,
            expiry_display,
            legs,
            spot=spot,
            time_years=time_years,
            sigma=iv,
        )

    def _resolve_leg_margin_with_source(
        self,
        user_id: str,
        exchange_code: str,
        stock_code: str,
        expiry_display: str,
        strike_price: Strike,
        right: str,
        quantity: int,
        margin_source: str,
        action: str = cfg.SELL,
        product: str = cfg.OPTIONS,
    ) -> tuple[dict, list[dict]]:
        warnings: list[dict] = []
        if margin_source == MARGIN_SOURCE_EXCHANGE:
            baseline = resolve_exchange_baseline_margin(
                exchange_code=exchange_code,
                stock_code=stock_code,
                expiry_display=expiry_display,
                strike_price=parse_strike(strike_price) or 0,
                right=right,
                quantity=int(quantity),
            )
            if baseline.get("found"):
                return (
                    {
                        "Status": 200,
                        "Success": {"span_margin_required": baseline["span_margin_required"]},
                        "Error": "",
                    },
                    warnings,
                )
            warnings.append(
                {
                    "type": "baseline_missing_contract",
                    "stock_code": stock_code,
                    "expiry_date": expiry_display,
                    "strike_price": parse_strike(strike_price) or 0,
                    "right": right,
                    "message": "Contract missing in Exchange Risk Baseline; Breeze fallback used.",
                }
            )

        breeze = self.get_session_breeze(user_id)
        expiry_api = _expiry_display_to_api(expiry_display)
        try:
            out = breeze.margin_calculator(
                [
                    {
                        "strike_price": strike_for_broker(strike_price),
                        "quantity": int(quantity),
                        "product": product,
                        "action": action,
                        "expiry_date": expiry_api,
                        "stock_code": stock_code,
                        "right": right,
                    }
                ],
                exchange_code=exchange_code,
            )
        except Exception as e:
            out = _icici_error(f"Error calling ICICI Breeze API margin_calculator: {e}")
        return out, warnings

    def _get_full_secret_for_user(self, user_id: str, user_fragment: str = ""):
        """Reconstruct full API secret for user. Returns (full_secret, cred_data) or (None, error_result)."""
        cred_data = self.fetch_credentials(user_id)
        if cred_data.get("Status") != 200:
            return None, cred_data
        from icici_breeze_backend.app.auth.credentials import CredentialManager
        from icici_breeze_backend.app.auth.context import get_full_secret_for_request
        enc_key = (cfg.JWT_SECRET or "").strip()
        if not enc_key:
            return None, {"Status": 400, "Error": "JWT_SECRET not set"}
        mgr = CredentialManager(encryption_key=enc_key)
        full_secret = (get_full_secret_for_request() or mgr.reconstruct_full_api_secret(user_id, user_fragment) or "").strip()
        if not full_secret:
            return None, {"Status": 400, "Error": "Could not reconstruct API secret"}
        return full_secret, cred_data

    def initiate_session(self, user_id, apisession, secret_user):
        """Establish Breeze session using stored credential fragment + user-provided fragment."""
        result = {}
        data = self.fetch_credentials(user_id)

        if data['Status'] != 200:
            result['Status'] = 400
            result['Error'] = data['Error']
            return result

        api_key = data['Success']['broker_api_key']
        from icici_breeze_backend.app.auth.credentials import CredentialManager
        enc_key = (cfg.JWT_SECRET or "").strip()
        if not enc_key:
            result['Status'] = 400
            result['Error'] = "JWT_SECRET or ENCRYPTION_KEY not set in environment"
            return result
        mgr = CredentialManager(encryption_key=enc_key)
        secret_key = mgr.reconstruct_full_api_secret(user_id, secret_user)
        if not secret_key:
            result['Status'] = 400
            result['Error'] = "Could not reconstruct API secret (invalid or missing stored fragment)"
            return result

        breeze = BreezeConnect(api_key=api_key)
        try:
            breeze.generate_session(api_secret=secret_key, session_token=apisession)
            _logger.info("Breeze session connected for user_id=%s", user_id)
            result['Status'] = 200
            result['Success'] = {'user_id': user_id}
        except Exception as e:
            _logger.warning("Breeze session failed for user_id=%s: %s", user_id, e, exc_info=True)
            result['Status'] = 400
            result['Error'] = "Unable to connect to broker. Please check your credentials and try again."
        return result

    def destroy_session(self,id):
        # No-op for stateless mode; ensure any in-memory attributes are cleared
        try:
            if hasattr(self, 'session_store'):
                del self.session_store
        except Exception:
            pass

    # Legacy session storage removed. Sessions are created per-request using credentials.

    def store_session(self,session_data):
        # No-op: session persistence removed in stateless design
        return True

    def retrieve_session(self, user_id):
        # Session retrieval removed. Callers should pass broker token per-request.
        return {'Status': 400, 'Error': 'Session persistence disabled; use per-request tokens'}

    def _resolve_broker_token(self, user_id):
        """Broker token for the current call: prefer the live request's cookie-derived
        ContextVar; fall back to the persisted, encrypted per-user token (written once
        at login, cleared on logout -- see app/repositories/broker_session.py) so
        background work with no HTTP request in scope (e.g. PB/SL square-off dispatch)
        can still get a session for the rest of the trading day. The persisted token
        expires with the token's own end-of-day IST lifetime regardless of whether any
        request has come in recently."""
        from icici_breeze_backend.app.auth.context import get_broker_token_for_request
        from icici_breeze_backend.app.repositories.broker_session import get_broker_session_token

        return get_broker_token_for_request() or get_broker_session_token(user_id) or ""

    def get_session_breeze(self, user_id):
        """Create one BreezeConnect session per request and reuse it (ICICI Invalid Checksum if we call generate_session multiple times).
        Uses broker token from HttpOnly cookie (or, absent a request, the persisted per-user token); fetches api_key and reconstructs secret from DB.
        Cross-request: consults breeze_session_cache (keyed by user_id + broker_token, TTL till midnight IST or config).
        """
        try:
            from icici_breeze_backend.app.auth.context import get_breeze_session_for_request, set_breeze_session_for_request
            from icici_breeze_backend.app.services.breeze_session_cache import get as cache_get, set as cache_set

            # Reuse session created earlier in this request
            cached = get_breeze_session_for_request()
            if cached is not None:
                return cached
            broker_token = self._resolve_broker_token(user_id)
            if not broker_token:
                _logger.warning("get_session_breeze: no broker token in request or persisted store user_id=%s", user_id)
                return None
            if getattr(cfg, "ICICI_BROKER_MODE", "live") == "mock":
                from icici_breeze_backend.dev.mock_broker import MockBreezeSdk

                breeze = cache_get(user_id, broker_token)
                if breeze is not None:
                    set_breeze_session_for_request(breeze)
                    return breeze
                breeze = MockBreezeSdk()
                breeze.user_id = user_id
                set_breeze_session_for_request(breeze)
                cache_set(user_id, broker_token, breeze)
                return breeze
            # Cross-request cache: reuse session for same user+token within TTL
            breeze = cache_get(user_id, broker_token)
            if breeze is not None:
                set_breeze_session_for_request(breeze)
                return breeze
            full_secret, cred_data = self._get_full_secret_for_user(user_id)
            if full_secret is None:
                _logger.warning("get_session_breeze: _get_full_secret_for_user failed user_id=%s error=%s", user_id, cred_data.get("Error", ""))
                return None
            api_key = cred_data["Success"]["broker_api_key"]
            breeze = BreezeConnect(api_key=api_key)
            breeze.user_id = user_id
            breeze.generate_session(api_secret=full_secret, session_token=broker_token)
            set_breeze_session_for_request(breeze)
            cache_set(user_id, broker_token, breeze)
            _logger.info("get_session_breeze: session created for user_id=%s", user_id)
            return breeze
        except Exception as e:
            _logger.warning("get_session_breeze: failed for user_id=%s: %s", user_id, e, exc_info=True)
            return None

    def get_session_token(self, user_id):
        """Return broker token from request context (HttpOnly cookie), or the last-seen token if none."""
        try:
            return self._resolve_broker_token(user_id) or None
        except Exception:
            return None

    def _maybe_evict_session(self, user_id: str, response: dict | None) -> None:
        """If ICICI response indicates auth/session failure, evict session cache so next request creates fresh session."""
        try:
            from icici_breeze_backend.app.services.breeze_session_cache import evict_if_icici_auth_failure, is_icici_auth_failure
            broker_token = self._resolve_broker_token(user_id)
            evict_if_icici_auth_failure(user_id, broker_token, response)
            if is_icici_auth_failure(response):
                from icici_breeze_backend.app.services.customer_details_cache import evict as evict_customer_details
                evict_customer_details(user_id, broker_token)
        except Exception:
            pass

    def get_customer_details(self, user_id):
        from icici_breeze_backend.app.services import customer_details_cache

        session_token = self.get_session_token(user_id)
        cached = customer_details_cache.get(user_id, session_token or "")
        if cached is not None:
            return cached

        breeze = self.get_session_breeze(user_id)
        if breeze is None:
            return None
        max_retries = getattr(cfg, "ICICI_MAX_RETRIES", 3) or 3

        def _is_transient(e):
            if isinstance(e, (ConnectionError, ConnectionResetError, OSError)):
                return True
            msg = str(e).lower()
            return any(x in msg for x in ("connection", "reset", "aborted", "timeout", "peer"))

        for attempt in range(max_retries):
            try:
                customer = breeze.get_customer_details(session_token)
                _s = customer.get("Status") if customer else None
                _e = (customer or {}).get("Error", "")
                if _s != 200:
                    _logger.warning("get_customer_details: API returned status=%s error=%r user_id=%s", _s, _e, user_id)
                self._maybe_evict_session(user_id, customer)
                if _s == 200:
                    customer_details_cache.set(user_id, session_token or "", customer)
                return customer
            except Exception as e:
                if _is_transient(e) and attempt < max_retries - 1:
                    _logger.warning("get_customer_details: transient error attempt %d/%d user_id=%s: %s", attempt + 1, max_retries, user_id, e)
                    time.sleep(2**attempt)
                else:
                    _logger.warning("get_customer_details: exception user_id=%s: %s", user_id, e, exc_info=True)
                    return {"Status": 400, "Error": "Unable to fetch account details. Please try again or re-login."}
        return {"Status": 400, "Error": "Unable to fetch account details. Please try again or re-login."}

    def uncovered_shorts(
        self,
        user_id,
        stock_code=None,
        expiry_date=None,
        limits=None,
        elm=None,
        otm_call_min=10,
        otm_call_max=10,
        otm_put_min=10,
        otm_put_max=10,
        top=10,
        exchange_code: str = cfg.NFO,
        margin_source: str = MARGIN_SOURCE_BREEZE,
    ):
        uncovered_shorts_result = {}
        uncovered_shorts_result['ce_options'] = {}
        uncovered_shorts_result['pe_options'] = {}
        if stock_code is None or expiry_date is None:
            return uncovered_shorts_result
        lot_size = self.fetch_lot_size(stock_code, expiry_date, exchange_code=exchange_code)
        expiry_date = _expiry_display_to_api(expiry_date)

        # Getting OTM CALL chain
        right = cfg.CALL
        uncovered_shorts_result['ce_options'] = self.get_options(
            user_id,
            right,
            stock_code,
            lot_size,
            expiry_date,
            limits,
            elm,
            otm_call_min,
            otm_call_max,
            top,
            exchange_code=exchange_code,
            margin_source=margin_source,
        )
        if _is_icici_limit_exceeded(uncovered_shorts_result['ce_options'].get("Error")):
            uncovered_shorts_result['ce_options'] = _breeze_limit_error(user_id)
            uncovered_shorts_result['pe_options'] = _breeze_limit_error(user_id)
            return uncovered_shorts_result

        # Getting OTM PUT chain
        right = cfg.PUT
        uncovered_shorts_result['pe_options'] = self.get_options(
            user_id,
            right,
            stock_code,
            lot_size,
            expiry_date,
            limits,
            elm,
            otm_put_min,
            otm_put_max,
            top,
            exchange_code=exchange_code,
            margin_source=margin_source,
        )
        if _is_icici_limit_exceeded(uncovered_shorts_result['pe_options'].get("Error")):
            uncovered_shorts_result['pe_options'] = _breeze_limit_error(user_id)

        return uncovered_shorts_result

    def strat_bull_spread(self,user_id,stock_code,expiry_date,limits,elm,range_lower,range_upper,top, exchange_code: str = cfg.NFO):
        trades = {}
        lot_size = self.fetch_lot_size(stock_code, expiry_date, exchange_code=exchange_code)
        expiry_date = _expiry_display_to_api(expiry_date)

        # Getting OTM CALL chain
        right = cfg.CALL
        product_type = cfg.OPTIONS
        strike_price = 0
        quote = self.get_quote(user_id,stock_code,expiry_date,product_type,right,strike_price, exchange_code=exchange_code)
        
        if quote['Status'] == 200:
            spot_price = float(quote['Success'][0]['spot_price'])
            otm_distance = (range_upper/spot_price - 1)*100
            sell_options = self.get_options(user_id,right,stock_code,lot_size,expiry_date,limits,elm,otm_distance,1, exchange_code=exchange_code)

            if sell_options['Status'] == 200:
                sell_leg = sell_options['Success'][0] # only one sell leg
                from icici_breeze_backend.app.services.quote_source_router import fetch_chain_side_icici_response

                options_chain = fetch_chain_side_icici_response(
                    self, user_id, stock_code, exchange_code, expiry_date, right
                )

                # Identifying the best option to BUY inside the defined range
                options = []
                for i in options_chain.get('Success') or []:
                    strike_f = parse_strike(i["strike_price"])
                    if strike_f is None:
                        continue
                    # None = unknown book (bhavcopy / BSE post-close), which must not
                    # exclude the strike the way a confirmed zero does.
                    _buy_qty = i.get("total_buy_qty")
                    _has_bid_side = _buy_qty is None or int(_buy_qty or 0) > 0
                    if _has_bid_side and ((right == cfg.CALL and strike_f < range_lower and strike_f > float(i['spot_price'])) or (right == cfg.PUT and strike_f > range_upper and strike_f < float(i['spot_price'])) ):
                        temp = {}
                        temp['stock_code'] = stock_code
                        temp['sell_leg'] = sell_leg
                        temp['buy_leg'] = {}
                        temp['buy_leg']['strike_price'] = strike_f
                        temp['buy_leg']['ltp'] = i['ltp']
                        temp['buy_leg']['best_bid_price'] = i['best_bid_price']
                        temp['buy_leg']['best_offer_price'] = i['best_offer_price']
                        temp['buy_leg']['total_buy_qty'] = i['total_buy_qty']
                        temp['buy_leg']['total_sell_qty'] = i['total_sell_qty']
                        temp['buy_leg']['spot_price'] = i['spot_price']
                        temp['buy_leg']['spot_distance'] = abs(float(i['spot_price']) - float(i["strike_price"])) / float(i["strike_price"])
                        _sell_qty = i.get('total_sell_qty')
                        temp['buy_leg']['buy_sell_ratio'] = (
                            int(_buy_qty) / int(_sell_qty)
                            if _buy_qty is not None and _sell_qty not in (None, 0)
                            else None
                        )
                        temp['expiry_date'] = _expiry_api_to_display(expiry_date)
                        temp['right'] = right

                        temp['buy_leg']['quantity'] = math.floor(sell_leg['premium'] / temp['buy_leg']['best_offer_price'] / lot_size) * lot_size
                        if _sell_qty is None or temp['buy_leg']['quantity'] <= int(_sell_qty):
                            temp['buy_leg']['best_offer_price'] = i["best_offer_price"]
                            if right == cfg.CALL:
                                temp['profit'] = temp['buy_leg']['quantity'] * (range_lower - strike_f)
                            else:
                                temp['profit'] = temp['buy_leg']['quantity'] * (strike_f - range_upper)
                            days_to_expiry = _days_to_expiry(expiry_date)
                            temp['carry_returns'] = (temp['profit']/(limits * 100000)) * (365/days_to_expiry) * 100
                            options.append(copy.deepcopy(temp))

                # Sort the top call options by premium
                if len(options) > 0:
                    options = sorted(options, key=lambda x: x["profit"], reverse=True)
                    trades['Success'] = options[:top]
                    # Combine sell_leg and buy_legs into trades
                    trades['Status'] = 200
                else:
                    trades['Error'] = "No suitable options found to BUY within the defined range"
                    trades['Status'] = 400
            else:
                trades['Error'] = "No suitable options found to SELL beyond Range - Upper"
                trades['Status'] = 400
        else:
            trades['Error'] = "Unable to fetch spot price for calculating OTM distance"
            trades['Status'] = 400

        return trades

    def get_margin_situation(self, user_id, target_margin_ute):
        margin_situation = {}
        breeze = self.get_session_breeze(user_id)
        if breeze is None:
            _logger.warning("get_margin_situation: no session for user_id=%s", user_id)
            margin_situation["Status"] = 400
            margin_situation["Error"] = "Unable to connect to broker. Please log out and log back in."
            return margin_situation

        try:
            full_secret, cred_data = self._get_full_secret_for_user(user_id)
            if full_secret is None:
                if getattr(cfg, "ICICI_BROKER_MODE", "live") == "mock":
                    margin_situation["Status"] = 200
                    margin_situation["Success"] = {
                        "actual_margin_ute": 500000,
                        "cash_limit": 1_000_000.0,
                        "actual_margin_avl": 1_500_000.0,
                        "target_margin_free": 900_000.0,
                        "limits": 600_000.0,
                        "last_refresh": now_ist().strftime("%d-%b-%Y %H:%M:%S"),
                    }
                    return margin_situation
                margin_situation["Status"] = cred_data.get("Status", 400)
                margin_situation["Error"] = cred_data.get("Error", "Could not fetch credentials")
                return margin_situation
            live_cache_key = f"margin_situation_snapshot:{user_id}:{target_margin_ute}"
            cached_snapshot = _cached_live_snapshot(live_cache_key)
            if cached_snapshot is not None:
                return cached_snapshot
            # Try SDK first (uses patched requests for GET+body)
            margin = None
            try:
                sdk_resp = breeze.get_margin(exchange_code=cfg.NFO)
                if isinstance(sdk_resp, dict) and (sdk_resp.get("Status") == 200 or sdk_resp.get("Success")):
                    margin = sdk_resp
            except Exception:
                pass
            if margin is None:
                # Fallback to direct API. Use raw session_token from CustomerDetails (avoids encode/decode mismatch)
                broker_token = self.get_session_token(user_id) or ""
                raw_session = (
                    _fetch_customerdetails_session_token(
                        cred_data["Success"]["broker_api_key"], broker_token, user_id=user_id
                    )
                    if broker_token
                    else ""
                )
                session_key = getattr(breeze, "session_key", None) or broker_token or ""
                margin = _call_icici_api_direct(
                    "https://api.icicidirect.com/breezeapi/api/v1/margin",
                    {"exchange_code": "NFO"},
                    cred_data["Success"]["broker_api_key"],
                    full_secret,
                    session_key,
                    user_id=user_id,
                    x_session_token=raw_session if raw_session else None,
                )
            if margin is None:
                margin = {}
            margin_situation = build_margin_situation_from_raw(
                margin, target_margin_ute=target_margin_ute
            )
            if margin_situation.get("Status") == 200:
                # DEBUG, and once rather than twice: the second line was a strict superset
                # of the first, and the payload is the customer's cash limit and margin
                # utilisation — which `/diagnostics/logs/download` hands to support.
                _logger.debug(
                    "get_margin_situation: success=%s",
                    margin_situation.get("Success"),
                )
            else:
                _logger.warning(
                    "get_margin_situation: API returned status=%s error=%r user_id=%s",
                    margin_situation.get("Status"),
                    margin_situation.get("Error"),
                    user_id,
                )
        except Exception as e:
            _logger.warning("get_margin_situation: exception user_id=%s: %s", user_id, e, exc_info=True)
            margin_situation["Status"] = 400
            margin_situation["Error"] = "Unable to fetch margin information. Please try again or re-login."

        self._maybe_evict_session(user_id, margin_situation)
        if margin_situation.get("Status") == 200:
            _remember_live_snapshot(live_cache_key, margin_situation)
        return margin_situation

    def get_options(
        self,
        user_id,
        right,
        stock_code,
        lot_size,
        expiry_date,
        limits,
        elm,
        otm_min,
        otm_max,
        top,
        exchange_code: str = cfg.NFO,
        margin_source: str = MARGIN_SOURCE_BREEZE,
    ):
        product_type = cfg.OPTIONS
        action = cfg.SELL
        sorted_options = {}
        from icici_breeze_backend.app.services.quote_source_router import fetch_chain_side_icici_response

        options_chain = fetch_chain_side_icici_response(
            self, user_id, stock_code, exchange_code, expiry_date, right
        )

        if options_chain.get('Status') == 200:
            # Calculating the premium that can be collected for every Liquid OTM CE option
            options = []
            total_chain_rows = len(options_chain.get('Success') or [])
            range_eligible_rows = 0
            margin_calls_made = 0
            min_pct = max(0.0, float(otm_min))
            max_pct = max(min_pct, float(otm_max))
            for i in options_chain.get('Success') or []:
                spot = float(i["spot_price"])
                strike = parse_strike(i["strike_price"])
                if strike is None:
                    continue
                in_range = (
                    (right == cfg.CALL and strike >= spot * (1 + min_pct / 100) and strike <= spot * (1 + max_pct / 100))
                    or (right == cfg.PUT and strike <= spot * (1 - min_pct / 100) and strike >= spot * (1 - min_pct / 100))
                )
                if _uncovered_scan_row_has_bid_side(i, exchange_code) and in_range:
                    range_eligible_rows += 1
                    margin_calls_made += 1
                    option_margin, _warnings = self._resolve_leg_margin_with_source(
                        user_id=user_id,
                        exchange_code=exchange_code,
                        stock_code=stock_code,
                        expiry_display=_expiry_api_to_display(expiry_date),
                        strike_price=strike,
                        right=right,
                        quantity=int(lot_size),
                        margin_source=margin_source,
                        action=action,
                        product=product_type,
                    )
                    if _warnings:
                        sorted_options.setdefault("Warnings", [])
                        sorted_options["Warnings"].extend(_warnings)

                    temp = {}
                    temp['stock_code'] = stock_code
                    temp['strike_price'] = strike
                    temp['ltp'] = i['ltp']
                    temp['best_bid_price'] = i['best_bid_price']
                    temp['best_offer_price'] = i['best_offer_price']
                    temp['total_buy_qty'] = i['total_buy_qty']
                    temp['total_sell_qty'] = i['total_sell_qty']
                    temp['spot_price'] = i['spot_price']
                    temp['expiry_date'] = _expiry_api_to_display(expiry_date)
                    temp['right'] = right
                    if option_margin.get('Status') == 200:
                        margin = float(option_margin['Success']['span_margin_required'])
                        if elm == cfg.CHECKED:
                            margin = margin + (float(temp['spot_price']) * float(lot_size) * cfg.ELM)
                        temp['quantity'] = math.floor(limits * 100000 / margin) * lot_size
                        depth_known = i.get("total_buy_qty") is not None
                        buy_cap = int(i.get("total_buy_qty") or 0)
                        book_ok = buy_cap > 0 and temp["quantity"] <= buy_cap
                        # Can't size against a book we don't have: an unknown or
                        # BSE-wiped book must not cap the line to zero.
                        if not depth_known and temp["quantity"] > 0:
                            book_ok = True
                        elif exchange_code == cfg.BFO and buy_cap == 0 and temp["quantity"] > 0:
                            book_ok = True
                        if book_ok:
                            bid_for_prem = float(i.get("best_bid_price") or 0)
                            if bid_for_prem <= 0:
                                bid_for_prem = float(i.get("ltp") or 0)
                            temp['best_bid_price'] = i["best_bid_price"]
                            temp['premium'] = temp['quantity'] * bid_for_prem
                            # Annualised carry on SPAN: (premium / DTE) * 365 / span for this line.
                            num_lots = (
                                (temp["quantity"] // lot_size)
                                if lot_size
                                else 0
                            )
                            span_total = float(num_lots) * margin
                            dte = max(1, _days_to_expiry(temp["expiry_date"]))
                            if span_total > 0:
                                temp["carry_returns"] = (
                                    _annualized_carry_percent_on_span(
                                        float(temp["premium"]),
                                        dte,
                                        span_total,
                                    )
                                )
                                temp["span_margin_total"] = span_total
                                temp["days_to_expiry"] = dte
                            else:
                                temp["carry_returns"] = 0.0
                            options.append(copy.deepcopy(temp))
                    else:
                        err = option_margin.get('Error', '')
                        if _is_icici_limit_exceeded(err):
                            _logger.warning(
                                "uncovered_shorts_scan_limit side=%s stock=%s exchange=%s expiry=%s chain_rows=%s range_rows=%s margin_calls=%s",
                                right, stock_code, exchange_code, expiry_date, total_chain_rows, range_eligible_rows, margin_calls_made,
                            )
                            return _breeze_limit_error(user_id)
                        sorted_options['Error'] = "Error calling margin_calculator : " + err
                        sorted_options['Status'] = option_margin.get('Status', 400)

            # Sort the top call options by premium
            options = sorted(options, key=lambda x: x["premium"], reverse=True)
            if len(options) > 0:
                sorted_options['Success'] = options[:top]
                sorted_options['Status'] = 200
            else:
                sorted_options['Error'] = "No suitable options found to SELL within the selected OTM range"
                sorted_options['Status'] = 400
            _logger.info(
                "uncovered_shorts_scan_diag side=%s stock=%s exchange=%s expiry=%s otm_min=%s otm_max=%s chain_rows=%s range_rows=%s margin_calls=%s shortlisted=%s status=%s",
                right, stock_code, exchange_code, expiry_date, min_pct, max_pct,
                total_chain_rows, range_eligible_rows, margin_calls_made, len(options), sorted_options.get("Status"),
            )
        else:
            sorted_options['Error'] = right + " : " + options_chain.get('Error', '')
            sorted_options['Status'] = options_chain.get('Status', 400)

        if sorted_options['Status'] == 200:
            for option in sorted_options['Success']:
                if option['ltp'] == 0:
                    stock_code = option['stock_code']
                    expiry_date = option['expiry_date']
                    right = option['right']
                    strike_price = option['strike_price']
                    try:
                        quote = self.get_quote(user_id,stock_code,expiry_date,product_type,right,strike_price, exchange_code=exchange_code)
                        option['ltp'] = quote['Success'][0]['ltp']
                        option['best_bid_price'] = quote['Success'][0]['best_bid_price']
                        option['best_offer_price'] = quote['Success'][0]['best_offer_price']
                        option['total_buy_qty'] = quote['Success'][0]['total_buy_qty']
                        option['total_sell_qty'] = quote['Success'][0]['total_sell_qty']
                        option['buy_sell_ratio'] = quote['Success'][0]['buy_sell_ratio']
                        option['spot_distance'] = abs(float(quote['Success'][0]['spot_price']) - strike_price) / strike_price
                    except Exception as e:
                        sorted_options['Status'] = 400
                        sorted_options['Error'] = f"Error calling ICICI Breeze API get_quote({stock_code},{expiry_date},{product_type},{right},{strike_price}): {e}"
                else:
                    if option.get('total_sell_qty') is None or option.get('total_buy_qty') is None:
                        option['buy_sell_ratio'] = None
                    elif int(option['total_sell_qty']) > 0:
                        option['buy_sell_ratio'] = int(option['total_buy_qty'])/int(option['total_sell_qty'])
                    else:
                        option['buy_sell_ratio'] = "NA"
                    option['spot_distance'] = abs(float(option['spot_price']) - option['strike_price']) / option['strike_price']
        
        return sorted_options

    def get_orders(self, user_id, start, end, *, exchange_codes=None):
        """Fetch orders. ICICI API limits date range to 10 days; we chunk and merge when needed.

        `exchange_codes` restricts which exchanges are queried. Every exchange costs a
        separate `get_order_list` call, so a caller that already knows its orders are all
        NFO (an SG carries its own `exchange_code`) can halve the cost by saying so.
        Defaults to both, because a caller that does not know — the Orders page — must not
        silently lose BFO rows.
        """
        breeze = self.get_session_breeze(user_id)
        if breeze is None:
            return _icici_error("Unable to connect to broker. Please log out and log back in.")

        # ICICI API: max 10 days between from_date and to_date
        _MAX_DAYS = 10
        start_dt = datetime.datetime.strptime(start, "%Y-%m-%d")
        end_dt = datetime.datetime.strptime(end, "%Y-%m-%d")
        total_days = (end_dt - start_dt).days + 1
        if exchange_codes:
            allowed = {str(e).strip().upper() for e in exchange_codes}
            exchange_codes = [e for e in (cfg.NFO, cfg.BFO) if str(e).upper() in allowed]
        if not exchange_codes:
            exchange_codes = [cfg.NFO, cfg.BFO]

        all_orders: list[dict] = []
        first_error: dict | None = None

        for exchange_code in exchange_codes:
            if total_days <= _MAX_DAYS:
                try:
                    orders_resp = breeze.get_order_list(exchange_code=exchange_code, from_date=start, to_date=end)
                except Exception as e:
                    orders_resp = _icici_error(f"Error calling ICICI Breeze API get_order_list: {e}")
                self._maybe_evict_session(user_id, orders_resp)

                if orders_resp.get("Status") == 200 and orders_resp.get("Success"):
                    for o in orders_resp.get("Success") or []:
                        o["exchange_code"] = o.get("exchange_code") or exchange_code
                        all_orders.append(o)
                elif first_error is None and orders_resp.get("Status") != 200:
                    first_error = orders_resp
            else:
                chunk_start = start_dt
                while chunk_start <= end_dt:
                    chunk_end = min(chunk_start + datetime.timedelta(days=_MAX_DAYS - 1), end_dt)
                    cs = chunk_start.strftime("%Y-%m-%d")
                    ce = chunk_end.strftime("%Y-%m-%d")
                    try:
                        chunk = breeze.get_order_list(exchange_code=exchange_code, from_date=cs, to_date=ce)
                    except Exception as e:
                        return _icici_error(f"Error calling ICICI Breeze API get_order_list: {e}")

                    if chunk.get("Status") != 200:
                        self._maybe_evict_session(user_id, chunk)
                        if first_error is None:
                            first_error = chunk
                        chunk_start = chunk_end + datetime.timedelta(days=1)
                        continue

                    for o in (chunk.get("Success") or []):
                        o["exchange_code"] = o.get("exchange_code") or exchange_code
                        all_orders.append(o)
                    chunk_start = chunk_end + datetime.timedelta(days=1)

        seen = set()
        deduped = []
        for o in all_orders:
            oid = o.get("order_id")
            if oid and oid not in seen:
                seen.add(oid)
                deduped.append(o)

        if not deduped:
            return first_error or {"Status": 200, "Success": None, "Error": None}

        orders = {"Status": 200, "Success": deduped, "Error": None}
        for order in orders['Success']:
            order['option'] = order['stock_code']+"-"+order['expiry_date']+"-"+"{:.0f}".format(order['strike_price'])+"-"+order['right']
            if order.get('status') == cfg.REQUESTED or order.get('status') == cfg.ORDERED or order.get('status') == cfg.PARTIAL_EXECUTED:
                order['cancelable'] = True
            else:
                order['cancelable'] = False
            order['modifiable'] = order['cancelable']

        return orders

    def get_trades(self, user_id, from_date, to_date, product_type=None):
        """Executed trades (ICICI get_trade_list) across NFO+BFO for a date range.

        Concurrent per-exchange calls, merged; each row tagged with its exchange_code.
        Dates are 'YYYY-MM-DD'. Used by the Dashboard Day's P&L mark-to-market calc,
        which filters to a single trade_date itself. Mirrors the get_performance fan-out.
        """
        breeze = self.get_session_breeze(user_id)
        if breeze is None:
            return _icici_error("Unable to connect to broker. Please log out and log back in.")

        start_date = from_date + "T06:00:00.000Z"
        end_date = to_date + "T06:00:00.000Z"
        product = product_type if product_type is not None else cfg.OPTIONS
        exchanges = (cfg.NFO, cfg.BFO)

        def _fetch(exchange_code):
            try:
                resp = breeze.get_trade_list(
                    from_date=start_date,
                    to_date=end_date,
                    exchange_code=exchange_code,
                    product_type=product,
                    action="",
                    stock_code="",
                )
            except Exception as e:
                resp = _icici_error(f"Error calling ICICI Breeze API get_trade_list: {e}")
            self._maybe_evict_session(user_id, resp)
            return resp

        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                responses = list(executor.map(_fetch, exchanges))
        except Exception as e:
            return _icici_error(f"Error calling ICICI Breeze API get_trade_list: {e}")

        all_trades: list[dict] = []
        first_error: dict | None = None
        for exchange_code, resp in zip(exchanges, responses):
            if resp.get("Status") == 200 and resp.get("Success"):
                for t in resp.get("Success") or []:
                    t["exchange_code"] = t.get("exchange_code") or exchange_code
                    all_trades.append(t)
            elif first_error is None and resp.get("Status") != 200:
                first_error = resp

        if not all_trades and first_error is not None:
            return first_error
        return {"Status": 200, "Success": all_trades, "Error": None}

    def group_orders(self, user_id, orders, *, fetch_ltp: bool = False):
        orders = sorted(orders['Success'], key=lambda x: (x.get("exchange_code", ""), x["option"]))
        grouped_orders = []
        group = None

        if len(orders) > 0:
            for order in orders:
                group_key = order['option'] + order['action'] + "-" + str(order.get('exchange_code', ''))
                if group != group_key:
                    if group != None:
                        grouped_orders.append(group_order)
                    group_order = {}
                    group = group_key
                    # Unique id per row so same (option, action) in non-consecutive order don't duplicate HTML ids
                    group_order['group'] = group + '-g' + str(len(grouped_orders))
                    group_order['group_option'] = order['option']
                    group_order['group_action'] = order['action']
                    group_order['group_exchange'] = order.get('exchange_code', '')
                    group_order['group_ordered'] = 0
                    group_order['group_cancelled'] = 0
                    group_order['group_expired'] = 0
                    group_order['group_open'] = 0
                    group_order['group_executed'] = 0
                    group_order['group_orders'] = []
                    if fetch_ltp:
                        try:
                            quote = self.get_quote(
                                user_id,
                                order['stock_code'],
                                order['expiry_date'],
                                order['product_type'],
                                order['right'],
                                order['strike_price'],
                                exchange_code=order.get('exchange_code', cfg.NFO),
                            )
                        except Exception as e:
                            quote = _icici_error(f"Error calling ICICI Breeze API get_quote({order['stock_code']},{order['expiry_date']},{order['product_type']},{order['right']},{order['strike_price']}): {e}")

                        if quote['Status'] == 200:
                            group_order['group_ltp'] = quote['Success'][0]['ltp']
                        else:
                            group_order['group_ltp'] = 0
                    else:
                        group_order['group_ltp'] = None

                group_order['group_ordered'] = group_order['group_ordered'] + int(order['quantity'])
                if order['status'] == cfg.EXECUTED:
                    group_order['group_executed'] = group_order['group_executed'] + int(order['quantity'])
                elif order['status'] == cfg.CANCELLED:
                    group_order['group_cancelled'] = group_order['group_cancelled'] + int(order['quantity'])
                elif order['status'] == cfg.EXPIRED:
                    group_order['group_expired'] = group_order['group_expired'] + int(order['quantity'])
                elif order['status'] == cfg.PARTIAL_EXECUTED_CANCELED:
                    group_order['group_executed'] = group_order['group_executed'] + int(order['quantity']) - int(order['pending_quantity'])
                    group_order['group_cancelled'] = group_order['group_cancelled'] + int(order['pending_quantity'])
                elif order['status'] == cfg.PARTIAL_EXECUTED_EXPIRED:
                    group_order['group_executed'] = group_order['group_executed'] + int(order['quantity']) - int(order['pending_quantity'])
                    group_order['group_expired'] = group_order['group_expired'] + int(order['pending_quantity'])
                else:
                    group_order['group_executed'] = group_order['group_executed'] + int(order['quantity']) - int(order['pending_quantity'])                    
                    group_order['group_open'] = group_order['group_open'] + int(order['pending_quantity'])
                    order['open_quantity'] = order['pending_quantity']
                group_order['group_orders'].append(order)
            
            grouped_orders.append(group_order)

        return grouped_orders

    def fetch_group_ltps_batch(
        self,
        user_id: str,
        groups: list[dict[str, Any]],
    ) -> dict[str, float | None]:
        """Batch LTP lookup for order-book groups (dedupe contracts, one chain per CE/PE bucket)."""
        if not groups:
            return {}

        def _strike_key(raw: Any) -> str:
            key = strike_key(raw)
            return key if key else str(raw or "").strip()

        def _expiry_display(raw: str) -> str:
            s = str(raw or "").strip()
            if not s:
                return s
            if "T" in s:
                return _expiry_api_to_display(s)
            if len(s.split("-")[0]) == 4:
                return _expiry_api_to_display(s)
            return s

        # group_id -> contract key
        group_contract: dict[str, tuple[str, str, str, str, str]] = {}
        contract_groups: dict[tuple[str, str, str, str, str], list[str]] = {}
        for item in groups:
            group_id = str(item.get("group") or "").strip()
            if not group_id:
                continue
            stock_code = str(item.get("stock_code") or "").strip()
            expiry_display = _expiry_display(str(item.get("expiry_date") or ""))
            strike = _strike_key(item.get("strike_price"))
            right = str(item.get("right") or "").strip()
            exchange_code = str(item.get("exchange_code") or cfg.NFO).strip() or cfg.NFO
            if not stock_code or not expiry_display or not strike or not right:
                group_contract[group_id] = ("", "", "", "", "")
                continue
            contract_key = (stock_code, expiry_display, strike, right, exchange_code)
            group_contract[group_id] = contract_key
            contract_groups.setdefault(contract_key, []).append(group_id)

        # Unique contracts -> chain buckets, keyed WITHOUT `right`: one chain build
        # serves both CE and PE, so bucketing per right built the same chain twice.
        chain_buckets: dict[tuple[str, str, str], dict[str, set[str]]] = {}
        for contract_key in contract_groups:
            stock_code, expiry_display, strike, right, exchange_code = contract_key
            if not stock_code:
                continue
            bucket = (stock_code, expiry_display, exchange_code)
            chain_buckets.setdefault(bucket, {}).setdefault(right, set()).add(strike)

        from icici_breeze_backend.app.services.quote_source_router import (
            fetch_chain_sides_icici_response,
        )

        strike_ltps: dict[tuple[str, str, str, str, str], float | None] = {}

        for (stock_code, expiry_display, exchange_code), strikes_by_right in chain_buckets.items():
            rights = sorted(strikes_by_right)
            try:
                chains = fetch_chain_sides_icici_response(
                    self,
                    user_id,
                    stock_code,
                    exchange_code,
                    expiry_display,
                    rights,
                )
            except Exception as e:
                _logger.warning(
                    "fetch_group_ltps_batch: chain failed user=%s stock=%s expiry=%s rights=%s: %s",
                    user_id,
                    stock_code,
                    expiry_display,
                    ",".join(rights),
                    e,
                )
                chains = {right: _icici_error(str(e)) for right in rights}

            for right, strikes in strikes_by_right.items():
                chain = chains.get(right)
                ltp_by_strike: dict[str, float | None] = {}
                if isinstance(chain, dict) and chain.get("Status") == 200:
                    for row in chain.get("Success") or []:
                        sk = _strike_key(row.get("strike_price"))
                        ltp_raw = row.get("ltp")
                        try:
                            ltp_by_strike[sk] = float(ltp_raw) if ltp_raw is not None else None
                        except (TypeError, ValueError):
                            ltp_by_strike[sk] = None

                for strike in strikes:
                    strike_ltps[(stock_code, expiry_display, strike, right, exchange_code)] = (
                        ltp_by_strike.get(strike)
                    )

        out: dict[str, float | None] = {}
        for group_id, contract_key in group_contract.items():
            if not contract_key[0]:
                out[group_id] = None
            else:
                out[group_id] = strike_ltps.get(contract_key)
        return out

    def build_cancel_order_messages(
        self,
        success_idx: list[int],
        failures: list[tuple[str, str]],
        orders: list,
        cancel_details: list | None,
    ) -> list[dict]:
        messages: list[dict] = []
        meta_ok = (
            cancel_details is not None
            and len(cancel_details) == len(orders)
        )
        if success_idx:
            n = len(success_idx)
            if meta_ok:
                opts = {
                    str((cancel_details[j] or {}).get("option") or "").strip()
                    for j in success_idx
                }
                opts.discard("")
                total_qty = sum(
                    int((cancel_details[j] or {}).get("open_quantity") or 0)
                    for j in success_idx
                )
                if len(opts) == 1 and total_qty > 0:
                    opt = next(iter(opts))
                    line = (
                        f"Cancelled {_format_indian_integer_digits(n)} orders for {opt} "
                        f"totalling {_format_indian_integer_digits(total_qty)} quantity"
                    )
                elif total_qty > 0:
                    line = (
                        f"Cancelled {_format_indian_integer_digits(n)} orders totalling "
                        f"{_format_indian_integer_digits(total_qty)} quantity"
                    )
                else:
                    line = (
                        f"Cancelled {_format_indian_integer_digits(n)} "
                        f"order{'s' if n != 1 else ''}."
                    )
            else:
                line = (
                    f"Cancelled {_format_indian_integer_digits(n)} "
                    f"order{'s' if n != 1 else ''}."
                )
            messages.append({"type": cfg.SUCCESS, "message": line})

        if failures:
            fail_parts = [f"{oid}: {em}" for oid, em in failures[:5]]
            extra = len(failures) - 5
            tail = f" (+{extra} more)" if extra > 0 else ""
            messages.append(
                {
                    "type": cfg.DANGER,
                    "message": "Failed to cancel "
                    f"{_format_indian_integer_digits(len(failures))} order(s): "
                    + "; ".join(fail_parts)
                    + tail,
                }
            )

        return messages

    def build_modify_leg_messages(
        self,
        contract_label: str,
        old_quantity: int,
        new_quantity: int,
        old_price: float | None,
        new_price: float | None,
        result: dict,
    ) -> list[dict]:
        messages: list[dict] = []
        did_anything = bool(result.get("cancelled") or result.get("modified") or result.get("placed"))
        if did_anything:
            parts = [f"Modified {contract_label}"]
            if old_quantity != new_quantity:
                parts.append(
                    f"quantity {_format_indian_integer_digits(old_quantity)} "
                    f"→ {_format_indian_integer_digits(new_quantity)}"
                )
            if old_price is not None and new_price is not None and old_price != new_price:
                parts.append(
                    f"price {_format_inr_2dp_indian(old_price)} → {_format_inr_2dp_indian(new_price)}"
                )
            messages.append({"type": cfg.SUCCESS, "message": ": ".join([parts[0], ", ".join(parts[1:])]) if len(parts) > 1 else parts[0]})

        failures = result.get("failures") or []
        if failures:
            fail_parts = [f"{ref}: {err}" for ref, err in failures[:5]]
            extra = len(failures) - 5
            tail = f" (+{extra} more)" if extra > 0 else ""
            messages.append(
                {
                    "type": cfg.DANGER,
                    "message": f"Failed to modify {len(failures)} order(s) for {contract_label}: "
                    + "; ".join(fail_parts)
                    + tail,
                }
            )
        return messages

    def cancel_order_single(self, user_id, order_ref: str) -> dict:
        """Cancel one order; used for client-paced cancels on 429."""
        breeze = self.get_session_breeze(user_id)
        if breeze is None:
            return {
                "success": False,
                "rate_limited": False,
                "error": "Unable to connect to broker. Please log out and log back in.",
            }
        order_id = order_ref
        exchange_code = cfg.NFO
        if isinstance(order_ref, str) and "|" in order_ref:
            order_id, exchange_code = order_ref.split("|", 1)
        try:
            response = breeze.cancel_order(
                exchange_code=exchange_code, order_id=order_id
            )
        except Exception as e:
            response = _icici_error(
                f"Error calling ICICI Breeze API cancel_order(exchange_code={exchange_code},order_id={order_id}): {e}"
            )

        self._maybe_evict_session(user_id, response)
        if response.get("Status") == 200:
            return {"success": True, "rate_limited": False, "daily_limit_exhausted": False, "error": None}
        err = str(response.get("Error") or "Unknown error")
        rl, daily_exhausted = _order_rate_limit_flags(response)
        _logger.warning(
            "cancel_order_single failed: user_id=%s order_id=%s exchange_code=%s status=%s error=%s rate_limited=%s",
            user_id, order_id, exchange_code, response.get("Status"), err, rl,
        )
        return {
            "success": False,
            "rate_limited": rl,
            "daily_limit_exhausted": daily_exhausted,
            "error": err,
        }

    def modify_order_single(
        self,
        user_id,
        order_ref: str,
        *,
        quantity: str | None = None,
        price: str | None = None,
    ) -> dict:
        """Modify quantity/price of one order; building block for leg-modify execution."""
        breeze = self.get_session_breeze(user_id)
        if breeze is None:
            return {
                "success": False,
                "rate_limited": False,
                "error": "Unable to connect to broker. Please log out and log back in.",
            }
        order_id = order_ref
        exchange_code = cfg.NFO
        if isinstance(order_ref, str) and "|" in order_ref:
            order_id, exchange_code = order_ref.split("|", 1)
        try:
            # ICICI's modify_order rejects a sparse patch (order_id/exchange_code/
            # quantity/price only) with a generic 500 — it needs the same full
            # field set as place_order, or it 500s with no useful detail.
            response = breeze.modify_order(
                order_id=order_id,
                exchange_code=exchange_code,
                order_type=str(cfg.LIMIT).strip().lower(),
                stoploss="",
                quantity=str(quantity) if quantity not in (None, "") else "",
                price=str(price) if price not in (None, "") else "",
                validity="day",
                disclosed_quantity="0",
                validity_date=str(today_ist_date()) + "T06:00:00.000Z",
            )
        except Exception as e:
            response = _icici_error(
                f"Error calling ICICI Breeze API modify_order(exchange_code={exchange_code},order_id={order_id}): {e}"
            )

        self._maybe_evict_session(user_id, response)
        if response.get("Status") == 200:
            return {"success": True, "rate_limited": False, "daily_limit_exhausted": False, "error": None}
        err = str(response.get("Error") or "Unknown error")
        rl, daily_exhausted = _order_rate_limit_flags(response)
        _logger.warning(
            "modify_order_single failed: user_id=%s order_id=%s exchange_code=%s quantity=%s price=%s status=%s error=%s rate_limited=%s",
            user_id, order_id, exchange_code, quantity, price, response.get("Status"), err, rl,
        )
        return {
            "success": False,
            "rate_limited": rl,
            "daily_limit_exhausted": daily_exhausted,
            "error": err,
        }

    def cancel_orders(self, user_id, orders, cancel_details: list | None = None):
        success_idx: list[int] = []
        failures: list[tuple[str, str]] = []
        for i, order in enumerate(orders):
            one = self.cancel_order_single(user_id, str(order))
            if one["success"]:
                success_idx.append(i)
            else:
                failures.append((str(order), str(one.get("error") or "Unknown error")))
        return self.build_cancel_order_messages(
            success_idx, failures, list(orders), cancel_details
        )

    def execute_leg_modification_step(
        self,
        user_id,
        step: LegModStep,
        *,
        contract: dict,
        new_price: str | None,
        current_price: str | None,
    ) -> dict:
        """Carry out exactly one step (cancel/modify/place) of a leg-modify plan.

        Each call to `plan_leg_redistribution` + `plan_steps` is pure/deterministic given the
        same (orders, new_quantity, new_price) inputs, so the caller re-derives the same ordered
        step list every request and executes step_index once — no cross-request state is kept
        here, mirroring `break_order_place_chunk`.
        """
        from icici_breeze_backend.audit.logger import AuditLogger, OperationType

        audit = AuditLogger(None)
        price_changed = (
            new_price is not None
            and str(new_price).strip() != ""
            and str(new_price) != str(current_price)
        )

        if step.kind == "cancel":
            order_id = step.order_id
            one = self.cancel_order_single(user_id, order_id)
            if one["success"]:
                audit.log_operation(user_id, OperationType.ORDER_CANCEL, "Order", order_id, action_status="success")
                return {"success": True, "order_id": order_id, "quantity": None, "price": None, "error": None, "rate_limited": False}
            audit.log_operation(
                user_id, OperationType.ORDER_CANCEL, "Order", order_id,
                action_status="failure", error_details=str(one.get("error") or ""),
            )
            return {
                "success": False, "order_id": order_id, "quantity": None, "price": None,
                "error": str(one.get("error") or "Unknown error"),
                "rate_limited": bool(one.get("rate_limited")),
            }

        if step.kind == "modify":
            order_id = step.order_id
            # ICICI's modify_order rejects a quantity-only change with a generic
            # error unless price is resent too — omitting it isn't treated as
            # "leave price alone", so always resend the (possibly unchanged) price.
            price_to_send = str(new_price) if price_changed else (
                str(current_price) if current_price not in (None, "") else None
            )
            one = self.modify_order_single(
                user_id,
                f"{order_id}|{step.exchange_code}",
                quantity=str(step.quantity),
                price=price_to_send,
            )
            out_price = new_price if price_changed else current_price
            if one["success"]:
                audit.log_operation(user_id, OperationType.ORDER_MODIFY, "Order", order_id, action_status="success")
                return {"success": True, "order_id": order_id, "quantity": step.quantity, "price": out_price, "error": None, "rate_limited": False}
            audit.log_operation(
                user_id, OperationType.ORDER_MODIFY, "Order", order_id,
                action_status="failure", error_details=str(one.get("error") or ""),
            )
            return {
                "success": False, "order_id": order_id, "quantity": step.quantity, "price": out_price,
                "error": str(one.get("error") or "Unknown error"),
                "rate_limited": bool(one.get("rate_limited")),
            }

        # step.kind == "place"
        qty = step.quantity
        out_price = new_price if price_changed else current_price
        response = self.place_order(
            user_id=user_id,
            product_type=contract["product_type"],
            stock_code=contract["stock_code"],
            action=contract["action"],
            strike_price=contract["strike_price"],
            right=contract["right"],
            price=str(out_price),
            expiry_date=contract["expiry_date"],
            quantity=qty,
            exchange_code=contract["exchange_code"],
        )
        ok = isinstance(response, dict) and response.get("Status") == 200
        order_id = (response or {}).get("Success", {}).get("order_id") if ok else None
        if ok and order_id:
            audit.log_operation(user_id, OperationType.ORDER_PLACE, "Order", str(order_id), action_status="success")
            return {"success": True, "order_id": str(order_id), "quantity": qty, "price": out_price, "error": None, "rate_limited": False}
        err = str((response or {}).get("Error") or "Unknown error")
        audit.log_operation(user_id, OperationType.ORDER_PLACE, "Order", None, action_status="failure", error_details=err)
        rl, _ = _order_rate_limit_flags(response)
        _logger.warning(
            "execute_leg_modification_step place_order failed: user_id=%s qty=%s status=%s error=%s rate_limited=%s",
            user_id, qty, (response or {}).get("Status"), err, rl,
        )
        return {"success": False, "order_id": None, "quantity": qty, "price": out_price, "error": err, "rate_limited": rl}

    def list_parked_orders(self, user_id: str) -> list[ParkedOrderListItem]:
        return parked_orders_repo.list_parked_orders(user_id)

    def create_parked_orders(
        self,
        user_id: str,
        items: list[ParkedOrderItem],
        replace_ids: list[str] | None = None,
    ) -> list[ParkedOrderListItem]:
        return parked_orders_repo.create_parked_orders(user_id, items, replace_ids)

    def update_parked_order(
        self,
        user_id: str,
        order_id: str,
        *,
        quantity: str | None = None,
        price: str | None = None,
        chunk_qty: str | None = None,
    ) -> ParkedOrderListItem | None:
        return parked_orders_repo.update_parked_order(
            user_id,
            order_id,
            quantity=quantity,
            price=price,
            chunk_qty=chunk_qty,
        )

    def delete_parked_orders(self, user_id: str, ids: list[str]) -> int:
        return parked_orders_repo.delete_parked_orders(user_id, ids)

    def delete_parked_order(self, user_id: str, order_id: str) -> bool:
        return parked_orders_repo.delete_parked_order(user_id, order_id)

    def get_positions(self, user_id, *, session_token: str | None = None):
        breeze = self.get_session_breeze(user_id)
        if breeze is None:
            _logger.warning("get_positions: no session for user_id=%s", user_id)
            return {"Status": 400, "Error": "Unable to connect to broker. Please log out and log back in.", "Success": None}
        full_secret, cred_data = self._get_full_secret_for_user(user_id)
        positions = None
        if full_secret is None:
            if getattr(cfg, "ICICI_BROKER_MODE", "live") == "mock":
                from icici_breeze_backend.dev.fixtures import responses as _fx

                positions = {
                    "Status": 200,
                    "Success": _fx.mock_portfolio_position_rows(),
                    "Error": None,
                }
            else:
                return {"Status": cred_data.get("Status", 400), "Error": cred_data.get("Error", "Could not fetch credentials"), "Success": None}
        else:
            live_cache_key = f"portfolio_positions_snapshot:{user_id}"
            cached_snapshot = _cached_live_snapshot(live_cache_key)
            if cached_snapshot is not None:
                return cached_snapshot
            # Same SDK entry point as Breeze API tester (get_portfolio_positions).
            try:
                positions = breeze.get_portfolio_positions()
            except Exception as e:
                _logger.warning("get_positions: exception user_id=%s: %s", user_id, e, exc_info=True)
                positions = {"Status": 400, "Error": "Unable to fetch positions. Please try again or re-login.", "Success": None}

        self._maybe_evict_session(user_id, positions)
        if positions is None:
            positions = {"Status": 400, "Error": "ICICI Breeze API get_portfolio_positions() returned NULL", "Success": None}
        if _is_empty_portfolio_positions_response(positions):
            empty_result = _empty_portfolio_positions_result()
            if full_secret is not None:
                _remember_live_snapshot(live_cache_key, empty_result)
            return empty_result
        if positions is not None:
            status, success_data, _ = _normalize_icici_response(positions)
            if status == 200 and success_data is not None:
                positions["Success"] = [
                    d for d in (success_data if isinstance(success_data, list) else [])
                    if (d.get("product_type") == cfg.OPTIONS and d.get("exchange_code") in (cfg.NFO, cfg.BFO))
                ]
                from icici_breeze_backend.app.services.quote_source_router import (
                    cached_chain_spot,
                    fetch_quote_icici_response,
                )

                for i in positions["Success"]:
                    stock_code = i['stock_code']
                    exchange_code = i['exchange_code']
                    expiry_date = datetime.datetime.strptime(i['expiry_date'],"%d-%b-%Y").strftime("%Y-%m-%d")+"T06:00:00.000Z"
                    product_type = i['product_type']
                    right = i['right']
                    strike_price = i['strike_price']
                    try:
                        # Cache-first (WS tick -> bhavcopy -> ICICI REST) instead of a raw
                        # get_quotes() call every poll -- same router other panels on this
                        # page (payoff chart, live overlay, PoP) already read from.
                        quote = fetch_quote_icici_response(
                            self, user_id, stock_code, exchange_code, expiry_date, right, strike_price,
                            product_type=product_type,
                        )
                    except Exception as e:
                        quote = _icici_error(f"Error resolving quote via router({stock_code},{exchange_code},{expiry_date},{product_type},{right},{strike_price}): {e}")
                    quote_rows = _quote_success_rows(quote)
                    resolved_spot: float | None = None
                    if quote.get("Status") == 200 and quote_rows:
                        try:
                            row_spot = float(quote_rows[0].get("spot_price"))
                        except (TypeError, ValueError):
                            row_spot = None
                        if row_spot is not None and row_spot > 0:
                            resolved_spot = row_spot
                        # ICICI's own get_portfolio_positions() ltp can be stale/wrong for
                        # illiquid contracts (observed post-close for BFO index options) --
                        # prefer the router's cache-first (WS -> bhavcopy -> REST) option ltp,
                        # the same source already trusted for spot_price above and for the
                        # frontend's live overlay. Fall back to the broker's raw ltp only when
                        # the router has no usable quote for this contract.
                        try:
                            router_ltp = float(quote_rows[0].get("ltp"))
                        except (TypeError, ValueError):
                            router_ltp = None
                        if router_ltp is not None and router_ltp > 0:
                            i["ltp"] = router_ltp

                    # The per-option quote can miss (deep OTM/ITM strike with no cached
                    # cell, bhavcopy row, or REST quote) even while the underlying's spot
                    # is live -- index_spot_feed keeps NIFTY/SENSEX index ticks warm in
                    # the same cache `cached_chain_spot` reads. Use that before surfacing
                    # "Err", so a healthy WS spot isn't hidden by an unrelated option-quote
                    # miss. Only truly-unavailable spot falls through to "Err".
                    if resolved_spot is None:
                        resolved_spot = cached_chain_spot(exchange_code, stock_code)
                    i["spot_price"] = resolved_spot if resolved_spot is not None else "Err"

                    if i['product_type'] == cfg.OPTIONS:
                        i['option'] = i['stock_code']+"-"+i['expiry_date']+"-"+i['strike_price']+"-"+i['right']
                        if int(i['quantity']) == 0:
                            i['current_profit'] = 0
                            i['carry_profit'] = 0
                        else:
                            if i['action'] == cfg.SELL:
                                i['current_profit'] = (float(i['average_price']) - float(i['ltp'])) * int(i['quantity'])
                                # Carry = P&L if this leg expires worthless (full premium kept) minus MTM already captured.
                                worthless_value = float(i['average_price']) * int(i['quantity'])
                                i['carry_profit'] = worthless_value - i['current_profit']

                                # SPAN is a portfolio risk model, not an additive per-leg number:
                                # a single short's naked SPAN ignores the offsetting risk of the
                                # other legs (e.g. a strangle can only be breached on one side).
                                # It is therefore netted once per Strategy Group and once per
                                # exchange for the whole portfolio in _compute_netted_margins()
                                # below -- per-leg SPAN and carry-return are intentionally left
                                # blank (the UI shows them at group/portfolio level only).
                                i['span_margin_required'] = None
                                i['carry_margin_returns'] = None
                                i["days_to_expiry"] = max(1, _days_to_expiry(i["expiry_date"]))

                                # Extreme Loss Margin (ELM) is additive (a flat % of notional per
                                # index short), not netted -- so it is computed per leg here and
                                # summed into the group/portfolio totals. Applicable for Index
                                # shorts only, and waived on the option's own expiry date (no
                                # overnight risk to cover).
                                if (i['stock_index_indicator'] == cfg.INDEX and i['action'] == cfg.SELL):
                                    if i['spot_price'] in (None, "Err"):
                                        i['elm_margin_required'] = None
                                    elif _parse_option_expiry_date(i['expiry_date']) == today_ist_date():
                                        i['elm_margin_required'] = 0.0
                                    else:
                                        i['elm_margin_required'] = float(i['quantity']) * float(i['spot_price']) * cfg.ELM
                                else:
                                    i['elm_margin_required'] = None
                            else:
                                i['current_profit'] = (float(i['ltp']) - float(i['average_price'])) * int(i['quantity'])
                                # Carry = P&L if this leg expires worthless (full premium lost) minus MTM already captured.
                                worthless_value = - float(i['average_price']) * int(i['quantity'])
                                i['carry_profit'] = worthless_value - i['current_profit']
                                i['span_margin_required'] = None
                                i['elm_margin_required'] = None
                                i['carry_margin_returns'] = None

                # Netted SPAN once per Strategy Group and once per exchange for the
                # whole portfolio (see _compute_netted_margins) -- replaces the old
                # sum-of-naked-legs, which over-stated margin by ignoring offsets.
                if positions["Success"]:
                    netted = self._compute_netted_margins(breeze, user_id, positions["Success"])
                    positions["groups"] = netted["groups"]
                    positions["portfolio"] = netted["portfolio"]
                if positions.get("Status") == 200 and positions.get("Success") == []:
                    positions["Error"] = None
            else:
                err = positions.get("Error") or positions.get("error") or "No data"
                _logger.warning("get_positions: API returned status=%s error=%r user_id=%s", status, err, user_id)

        if full_secret is not None and positions.get("Status") == 200:
            _remember_live_snapshot(live_cache_key, positions)
        return positions

    def _netted_span_for_legs(
        self, breeze, user_id: str, exchange_code: str, legs: list,
    ) -> float | None:
        """Netted multi-leg SPAN for a set of open legs on ONE exchange.

        Every leg (buys included) is sent in a single margin_calculator call so
        ICICI nets the offsetting risk -- unlike the retired per-leg path, which
        priced each short as naked and summed. Cached by leg composition (24h) so
        repeated /portfolio/data polls don't re-hit the rate-limited broker API
        unless a leg's identity or quantity actually changes. Returns None on any
        failure -- callers surface "—" rather than falling back to a naked sum.
        """
        active = [l for l in legs if _safe_int(l.get("quantity")) != 0]
        if not active:
            return None
        cache_key = _portfolio_netted_cache_key(user_id, exchange_code, active)
        cached = _cached_span_margin_required(cache_key)
        if cached is not None:
            return cached
        margin_input = [
            {
                "strike_price": l["strike_price"],
                "quantity": l["quantity"],
                "right": l["right"],
                "action": l["action"],
                "product": l["product_type"],
                "expiry_date": l["expiry_date"],
                "stock_code": l["stock_code"],
                "cover_order_flow": "N",
                "fresh_order_type": "N",
                "cover_limit_rate": "0",
                "cover_sltp_price": "0",
                "fresh_limit_rate": "0",
                "open_quantity": "0",
            }
            for l in active
        ]
        try:
            margins = breeze.margin_calculator(margin_input, exchange_code=exchange_code)
        except Exception as e:
            _logger.warning(
                "netted margin_calculator failed exchange=%s legs=%d: %s",
                exchange_code, len(active), e,
            )
            return None
        if isinstance(margins, dict) and margins.get("Status") == 200:
            try:
                span = float((margins.get("Success") or {}).get("span_margin_required"))
            except (TypeError, ValueError):
                return None
            _remember_span_margin_required(cache_key, span)
            return span
        return None

    def _compute_netted_margins(self, breeze, user_id: str, legs: list) -> dict:
        """Group-level and portfolio-level netted SPAN + additive ELM.

        - One netted SPAN call per Strategy Group (exchange|stock|expiry).
        - One netted SPAN call per underlying for the portfolio, summed across
          underlyings. Netting is deliberately capped at a single underlying: an
          underlying's own legs (across expiries) net against each other, but
          distinct underlyings never offset -- the conservative choice, and it
          also avoids relying on ICICI's inter-commodity treatment in a mixed
          call.
        - ELM stays additive (sum of per-leg ELM); carry is the sum of per-leg
          carry_profit; carry-return uses the netted (SPAN + ELM) denominator.
        """
        from collections import OrderedDict

        groups_map: "OrderedDict[tuple, list]" = OrderedDict()
        for l in legs:
            gkey = (l.get("exchange_code"), l.get("stock_code"), l.get("expiry_date"))
            groups_map.setdefault(gkey, []).append(l)

        groups_out = []
        for (exch, stock, expiry), grp_legs in groups_map.items():
            span = self._netted_span_for_legs(breeze, user_id, exch, grp_legs)
            elm = _sum_leg_elm(grp_legs)
            carry = sum(_safe_float(l.get("carry_profit")) for l in grp_legs)
            total_margin = (span + (elm or 0.0)) if span is not None else None
            dte = max(1, _days_to_expiry(expiry))
            carry_ret = (
                _annualized_carry_percent_on_span(carry, dte, total_margin)
                if total_margin and total_margin > 0
                else None
            )
            groups_out.append(
                {
                    "key": f"{stock}|{exch}|{expiry}",
                    "stock_code": stock,
                    "exchange_code": exch,
                    "expiry_date": expiry,
                    "span_margin_required": span,
                    "elm_margin_required": elm,
                    "carry_margin_returns": carry_ret,
                }
            )

        # Portfolio SPAN: netted per underlying, then summed across underlyings
        # (netting capped at one underlying -- no cross-underlying offset).
        by_underlying: "OrderedDict[tuple, list]" = OrderedDict()
        for l in legs:
            by_underlying.setdefault(
                (l.get("exchange_code"), l.get("stock_code")), []
            ).append(l)
        port_span: float | None = None
        for (exch, _stock), u_legs in by_underlying.items():
            s = self._netted_span_for_legs(breeze, user_id, exch, u_legs)
            if s is not None:
                port_span = (port_span or 0.0) + s
        port_elm = _sum_leg_elm(legs)

        # Portfolio carry-return: margin-weighted average of the group carry-returns.
        # Each group has a single DTE so its rate is well-defined; a single
        # portfolio DTE is not, since groups can span different expiries.
        num = 0.0
        den = 0.0
        for g in groups_out:
            gm = g["span_margin_required"]
            cr = g["carry_margin_returns"]
            if gm is not None and cr is not None:
                weight = gm + (g["elm_margin_required"] or 0.0)
                if weight > 0:
                    num += cr * weight
                    den += weight
        port_carry_ret = (num / den) if den > 0 else None

        return {
            "groups": groups_out,
            "portfolio": {
                "span_margin_required": port_span,
                "elm_margin_required": port_elm,
                "carry_margin_returns": port_carry_ret,
            },
        }

    def is_valid_hedge(self,strike_price,right,option):
        valid_hedge = True

        # For CALL exclude any hedges that are below the Spot Price or above the Strike Price of the position being hedged
        if (right == cfg.CALL and (strike_price <= option['strike_price'] or option['strike_price'] <= option['spot_price'])):
            valid_hedge = False

        # For PUT exclude any hedges that are above the Spot Price or below the Strike Price of the position being hedged
        if (right == cfg.PUT and (strike_price >= option['strike_price'] or option['strike_price'] >= option['spot_price'])):
            valid_hedge = False
        
        # LTP being 0 indiciates an illiquid option?
        if option['ltp'] == 0:
            valid_hedge = False
        
        # Can't buy as hedge if there are no sellers -- but an unknown book (None:
        # bhavcopy has no depth, BSE wipes its book at close) is not the same as a
        # confirmed empty one, so only disqualify on a real zero.
        if option.get('total_sell_qty') is not None and int(option['total_sell_qty']) == 0:
            valid_hedge = False
        
        # Can't buy hedge if option is not liquid on the day. This also means you can't be the first buyer in the market for the hedge
        # if (option['ltt'] == "" or datetime.datetime.strptime(option['ltt'],"%d-%b-%Y %H:%M:%S").date() != datetime.datetime.today().date()):
        #     valid_hedge = False

        return valid_hedge

    def hedge(self,user_id,right,action,stock_code,quantity,expiry_date,strike_price,top, exchange_code: str = cfg.NFO):
        sorted_hedges = {}
        hedgeable_set = (cfg.HEDGEABLE_UNDERLYINGS or {}).get(exchange_code or "", set())
        if stock_code in hedgeable_set and action == cfg.SELL:
            
            try:
                from icici_breeze_backend.app.services.quote_source_router import fetch_chain_side_icici_response

                full_chain = fetch_chain_side_icici_response(
                    self, user_id, stock_code, exchange_code, expiry_date, right
                )
                if full_chain.get("Status") != 200:
                    raise ValueError(full_chain.get("Error") or "chain fetch failed")

                hedge_chain = []
                for option in full_chain.get("Success") or []:
                    strike_price = parse_strike(strike_price)
                    opt_strike = parse_strike(option.get('strike_price'))
                    if strike_price is None or opt_strike is None:
                        continue
                    option['strike_price'] = opt_strike
                    option['spot_price'] = float(option['spot_price'])

                    if self.is_valid_hedge(strike_price,right,option) == True:
                        option['distance_from_spot'] = abs(option['strike_price'] - option['spot_price'])
                        hedge_chain.append(option)

                # build the ATM premium ratio curve
                hedge_chain = sorted(hedge_chain, key=lambda x: x['distance_from_spot'], reverse=False)
                if not hedge_chain:
                    sorted_hedges['Success'] = None
                    sorted_hedges['Error'] = (
                        "No hedge candidates between spot and short strike "
                        "(liquidity / LTP / hedge rules)"
                    )
                    sorted_hedges['Status'] = 400
                    return sorted_hedges
                atm_premium = hedge_chain[0]['best_offer_price']
                try:
                    atm_pf = float(atm_premium)
                except (TypeError, ValueError):
                    atm_pf = 0.0
                if atm_pf <= 0:
                    sorted_hedges['Success'] = None
                    sorted_hedges['Error'] = "ATM premium is zero; cannot size hedge"
                    sorted_hedges['Status'] = 400
                    return sorted_hedges
                premium_curve = []
                for option in hedge_chain:
                    premium = {}
                    premium['distance_from_spot'] = option['distance_from_spot']
                    premium['premium_ratio'] = float(option['best_offer_price']) / atm_pf
                    premium_curve.append(premium)

                # scrip_master uses DD-Mon-YYYY; hedge() often receives API expiry — normalize like get_full_option_chain.
                lot_expiry = _expiry_api_to_display(str(expiry_date).strip()) if expiry_date else ""
                lot_size = self.fetch_lot_size(
                    stock_code=stock_code,
                    expiry_date=lot_expiry,
                    exchange_code=exchange_code,
                )
                if lot_size is None or int(lot_size) <= 0:
                    sorted_hedges['Success'] = None
                    sorted_hedges['Error'] = "Could not resolve lot size for expiry (scrip master)"
                    sorted_hedges['Status'] = 400
                    return sorted_hedges

                # calculate hedging quantity and therefore hedging premium across the hedge option chain
                for option in hedge_chain:
                    strike_distance = abs(option['strike_price'] - strike_price)
                    for premium in premium_curve: # find the premium ratio at the strike distance
                        premium['difference'] = abs(strike_distance - premium['distance_from_spot'])

                    premium_curve = sorted(premium_curve, key=lambda x: x['difference'], reverse=False)
                    option['hedge_quantity'] = float(quantity) * premium_curve[0]['premium_ratio']
                    option['hedge_quantity'] = math.ceil(option['hedge_quantity']/lot_size) * lot_size
                    option['hedge_premium'] = option['hedge_quantity'] * option['best_offer_price']

                hedge_chain = sorted(hedge_chain, key=lambda x: x['hedge_premium'], reverse=False)
                top_chain = hedge_chain[:top]
                for opt in top_chain:
                    opt["lot_size"] = int(lot_size)
                sorted_hedges["Success"] = top_chain
                sorted_hedges['Status'] = 200
                sorted_hedges['Error'] = ""
            except Exception as e:  # noqa: BLE001
                _logger.warning("hedge(): %s", e, exc_info=True)
                sorted_hedges['Success'] = None
                sorted_hedges['Error'] = "Error fetching hedging options"
                sorted_hedges['Status'] = 400
        else:
            sorted_hedges['Success'] = None
            sorted_hedges['Error'] = "Hedging not available except for NIFTY/BANKNIFTY (NFO) and SENSEX/BANKEX (BFO) SELL positions"
            sorted_hedges['Status'] = 400
        return sorted_hedges

    def store_messages(self, user_id: str, messages: list):
        """Store transient UI messages for user (order cancel/break feedback)."""
        from icici_breeze_backend.app.repositories.message_repository import store_messages as _store
        _store(user_id, messages)

    def retrieve_messages(self, user_id: str):
        """Retrieve and flush transient messages for user."""
        from icici_breeze_backend.app.repositories.message_repository import retrieve_and_flush_messages as _retrieve
        return _retrieve(user_id)

    def get_quote(
        self,
        user_id,
        stock_code,
        expiry_date,
        product_type,
        right,
        strike_price,
        exchange_code: str = cfg.NFO,
        *,
        audit: "StrategyBuilderAuditSession | None" = None,
        audit_rationale: str | None = None,
        audit_request: dict[str, Any] | None = None,
    ):
        from icici_breeze_backend.app.services.quote_source_router import fetch_quote_icici_response

        return fetch_quote_icici_response(
            self,
            user_id,
            stock_code,
            exchange_code,
            expiry_date,
            right,
            strike_price,
            product_type=product_type,
            audit=audit,
            audit_rationale=audit_rationale,
            audit_request=audit_request,
        )

    def _transform_icici_chain_rows(
        self,
        stock_code: str,
        expiry_display: str,
        exchange_code: str,
        right: str,
        raw_rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        lot_size = self.fetch_lot_size(stock_code, expiry_display, exchange_code=exchange_code)
        lot_val = lot_size if lot_size is not None else 0
        rows: list[dict[str, Any]] = []
        market_closed = not is_market_open()
        for i in raw_rows:
            try:
                total_buy: int | None = int(i.get("total_buy_qty") or 0)
                total_sell: int | None = int(i.get("total_sell_qty") or 0)
                # BSE resets its entire market-depth block the moment the session
                # closes, so a post-close REST quote reports a zero book for every
                # BFO contract regardless of how liquid it actually was. Report
                # that as unknown rather than as "nobody is trading this".
                if (
                    market_closed
                    and exchange_code == cfg.BFO
                    and total_buy == 0
                    and total_sell == 0
                ):
                    total_buy = None
                    total_sell = None
                    # The same reset zeroes the quoted prices, not just the sizes.
                    if not _positive_number(i.get("best_bid_price")):
                        i = {**i, "best_bid_price": None}
                    if not _positive_number(i.get("best_offer_price")):
                        i = {**i, "best_offer_price": None}
                if total_buy is None or total_sell is None:
                    ratio = None
                elif total_sell > 0:
                    ratio = total_buy / total_sell
                else:
                    ratio = 0.0 if total_buy == 0 else None
                oi = i.get("open_interest")
                try:
                    oi_val = int(oi) if oi is not None else 0
                except (TypeError, ValueError):
                    oi_val = 0
                rows.append(
                    {
                        "stock_code": stock_code,
                        "strike_price": parse_strike(i.get("strike_price")) or 0.0,
                        "right": right,
                        "expiry_date": expiry_display,
                        "ltp": i.get("ltp"),
                        "open_interest": oi_val,
                        "total_buy_qty": total_buy,
                        "total_sell_qty": total_sell,
                        # None only when depth is unknown; "NA" still means the real
                        # "bids exist, no offers" case the frontend already renders.
                        "buy_sell_ratio": (
                            None
                            if total_buy is None or total_sell is None
                            else (ratio if ratio is not None else "NA")
                        ),
                        "best_bid_price": i.get("best_bid_price"),
                        "best_offer_price": i.get("best_offer_price"),
                        "spot_price": i.get("spot_price"),
                        "lot_size": lot_val,
                    }
                )
            except (KeyError, TypeError, ValueError):
                continue
        return rows

    def _fetch_icici_chain_side_raw(
        self,
        user_id: str,
        stock_code: str,
        exchange_code: str,
        expiry_api: str,
        right: str,
    ) -> dict[str, Any]:
        breeze = self.get_session_breeze(user_id)
        if breeze is None:
            return _icici_error("Unable to connect to broker. Please log out and log back in.")
        try:
            _pt, _rt = _icici_option_chain_enums(cfg.OPTIONS, right)
            r = icici_client.get_option_chain_quotes(
                breeze,
                user_id=user_id,
                stock_code=stock_code,
                exchange_code=exchange_code,
                product_type=_pt,
                expiry_date=expiry_api,
                right=_rt,
            )
        except Exception as e:
            return _icici_error(f"get_option_chain_quotes({right}): {e}")
        if not isinstance(r, dict) or r.get("Status") != 200:
            return r if isinstance(r, dict) else _icici_error(f"get_option_chain_quotes({right}): invalid response")
        return {"Status": 200, "Success": list(r.get("Success") or []), "Error": None}

    def get_full_option_chain(
        self,
        user_id: str,
        stock_code: str,
        exchange_code: str,
        expiry_date: str,
        *,
        holder_id: str | None = None,
    ):
        """Fetch full CE + PE option chain for order page. Expiry can be YYYY-MM-DD or DD-Mon-YYYY."""
        expiry_display = expiry_date
        if expiry_date and len(expiry_date) == 10 and expiry_date[4] == "-":  # YYYY-MM-DD
            try:
                expiry_display = datetime.datetime.strptime(expiry_date, "%Y-%m-%d").strftime("%d-%b-%Y")
            except ValueError:
                pass
        from icici_breeze_backend.app.services.quote_source_router import assemble_chain_with_router

        return assemble_chain_with_router(
            self, user_id, stock_code, exchange_code, expiry_display, holder_id=holder_id
        )

    def get_payoff_quote(
        self,
        user_id: str,
        stock_code: str,
        exchange_code: str,
        expiry_date: str,
        *,
        holder_id: str | None = None,
    ):
        """Minimal spot + ATM-strike quote + lot size for the portfolio payoff
        panel — see `quote_source_router.fetch_payoff_quote_routed` for why this
        doesn't need to wait for the rest of the chain like get_full_option_chain does."""
        expiry_display = expiry_date
        if expiry_date and len(expiry_date) == 10 and expiry_date[4] == "-":  # YYYY-MM-DD
            try:
                expiry_display = datetime.datetime.strptime(expiry_date, "%Y-%m-%d").strftime("%d-%b-%Y")
            except ValueError:
                pass
        from icici_breeze_backend.app.services.quote_source_router import assemble_payoff_quote_with_router

        return assemble_payoff_quote_with_router(
            self, user_id, stock_code, exchange_code, expiry_display, holder_id=holder_id
        )

    def _get_full_option_chain_icici_rest(
        self,
        user_id: str,
        stock_code: str,
        exchange_code: str,
        expiry_display: str,
        *,
        strikes: list[Strike] | None = None,
        lot_size: int | None = None,
        freeze_quantity: int | None = None,
    ):
        """REST fallback: two get_option_chain_quotes calls (all CE strikes, all PE
        strikes -- no per-strike looping) used when neither the live WebSocket nor
        the day's bhavcopy is available (post-close, pre-bhavcopy gap -- see
        quote_source_router.resolve_quote_source). Builds a full strike skeleton
        (one chain_row per tradeable strike, nulls where ICICI returned nothing)
        the same way bhavcopy_store.build_chain_from_bhavcopy does, so the result
        satisfies chain_readiness.is_chain_complete's structural check. Does not
        filter rows by live order-book depth -- after market close there is no
        live depth even for strikes with a perfectly good closing LTP, so a
        depth-based illiquidity filter would silently empty the whole chain;
        completeness is judged downstream by is_chain_complete/_cell_has_quote,
        which check ltp first."""
        expiry_api = _expiry_display_to_api(expiry_display)

        ce_res = self._fetch_icici_chain_side_raw(
            user_id, stock_code, exchange_code, expiry_api, cfg.CALL
        )
        pe_res = self._fetch_icici_chain_side_raw(
            user_id, stock_code, exchange_code, expiry_api, cfg.PUT
        )
        if ce_res.get("Status") != 200:
            return ce_res
        if pe_res.get("Status") != 200:
            return pe_res
        ce_raw = ce_res.get("Success") or []
        pe_raw = pe_res.get("Success") or []

        calls = self._transform_icici_chain_rows(
            stock_code, expiry_display, exchange_code, cfg.CALL, ce_raw
        )
        puts = self._transform_icici_chain_rows(
            stock_code, expiry_display, exchange_code, cfg.PUT, pe_raw
        )

        from icici_breeze_backend.app.services.reference_data.tradable_contracts import (
            is_tradeable_contract,
            list_tradeable_strikes,
        )

        if strikes:
            strike_list = sorted(set(strikes))
        else:
            strike_list = list_tradeable_strikes(
                stock_code, expiry_display, exchange_code=exchange_code
            )
            if not strike_list:
                strike_list = sorted(
                    set(r["strike_price"] for r in calls) | set(r["strike_price"] for r in puts)
                )
        call_by_strike = {r["strike_price"]: r for r in calls}
        put_by_strike = {r["strike_price"]: r for r in puts}

        max_call_oi = max((r["open_interest"] for r in calls), default=0)
        max_put_oi = max((r["open_interest"] for r in puts), default=0)

        chain_rows = []
        for k in strike_list:
            call_cell = call_by_strike.get(k)
            put_cell = put_by_strike.get(k)
            if call_cell and not is_tradeable_contract(
                stock_code, expiry_display, k, cfg.CALL, exchange_code=exchange_code
            ):
                call_cell = None
            if put_cell and not is_tradeable_contract(
                stock_code, expiry_display, k, cfg.PUT, exchange_code=exchange_code
            ):
                put_cell = None
            chain_rows.append({
                "strike_price": k,
                "call": call_cell,
                "put": put_cell,
            })

        spot_price = None
        if calls:
            raw = calls[0].get("spot_price")
            if raw is not None:
                try:
                    spot_price = float(raw)
                except (TypeError, ValueError):
                    pass
        if spot_price is None and puts:
            raw = puts[0].get("spot_price")
            if raw is not None:
                try:
                    spot_price = float(raw)
                except (TypeError, ValueError):
                    pass
        atm_strike = None
        chain_strike_prices = [r["strike_price"] for r in chain_rows]
        if spot_price is not None and chain_strike_prices:
            atm_strike = min(chain_strike_prices, key=lambda s: abs(s - spot_price))

        if lot_size is None:
            lot_size = self.fetch_lot_size(stock_code, expiry_display, exchange_code=exchange_code)
        if freeze_quantity is None:
            try:
                if lot_size is not None:
                    ls = int(lot_size)
                    if ls > 0:
                        qty_limits = self.fetch_qty_limits(
                            stock_code, exchange_code=exchange_code
                        )
                        if qty_limits is not None:
                            freeze_quantity = (max(1, int(qty_limits)) // ls) * ls
            except (TypeError, ValueError):
                freeze_quantity = None

        success_payload: dict[str, Any] = {
            "chain_rows": chain_rows,
            "max_call_oi": max_call_oi,
            "max_put_oi": max_put_oi,
            "expiry_display": expiry_display,
            "stock_code": stock_code,
            "exchange_code": exchange_code,
            "spot_price": spot_price,
            "atm_strike": atm_strike,
            "lot_size": int(lot_size) if lot_size is not None else None,
            "freeze_quantity": freeze_quantity,
            "quote_source": "icici_api",
        }

        return {
            "Status": 200,
            "Error": None,
            "Success": success_payload,
        }

    def place_order(self,user_id,product_type,stock_code,action,strike_price,right,price,expiry_date,quantity, exchange_code: str = cfg.NFO, aggressive_limit: bool = False):
        if aggressive_limit and not cfg.AGGRESSIVE_LIMIT_ORDER_ENABLED:
            return _icici_error(AGGRESSIVE_LIMIT_DISABLED_MESSAGE)
        breeze = self.get_session_breeze(user_id)
        if breeze is None:
            return _icici_error(
                "Unable to connect to broker. Please log out and log back in.",
            )
        try:
            expiry_api = _expiry_to_breeze_place_order(expiry_date)
        except (ValueError, TypeError) as e:
            return _icici_error(f"Invalid expiry_date for order: {expiry_date!r} ({e})")

        # Breeze validates with .lower() but forwards the raw strings in JSON; ICICI often expects lowercase enums.
        prod = str(product_type or "").strip().lower()
        act = str(action or "").strip().lower()
        rgt = str(right or "").strip().lower() if right not in (None, "") else ""
        try:
            strike = strike_for_broker(strike_price)
        except (TypeError, ValueError):
            strike = str(strike_price)
        try:
            qty_str = str(int(quantity))
        except (TypeError, ValueError):
            qty_str = str(quantity)
        prc = "0" if aggressive_limit else str(price).strip()
        order_type = str(cfg.MARKET if aggressive_limit else cfg.LIMIT).strip().lower()

        place_order_sdk_exception = False
        try:
            response = breeze.place_order(
                stock_code=stock_code,
                action=act,
                strike_price=strike,
                right=rgt,
                price=prc,
                expiry_date=expiry_api,
                validity="day",
                order_type=order_type,
                quantity=qty_str,
                validity_date=str(today_ist_date()) + "T06:00:00.000Z",
                stoploss="",
                disclosed_quantity="0",
                exchange_code=exchange_code,
                product=prod,
            )
        except Exception as e:
            place_order_sdk_exception = True
            err = str(e)
            _logger.warning(
                "place_order: Breeze SDK exception user_id=%s stock_code=%s exchange=%s action=%s "
                "qty=%s strike=%s right=%r price=%r expiry_api=%r exc_type=%s err=%s",
                user_id,
                stock_code,
                exchange_code,
                act,
                qty_str,
                strike,
                rgt,
                prc,
                expiry_api,
                type(e).__name__,
                err,
                exc_info=True,
            )
            if "Expecting value" in err or "JSONDecodeError" in type(e).__name__:
                response = _icici_error(
                    "Broker returned an empty or non-JSON response when placing the order "
                    "(session may have expired — re-login to ICICI — or the request was rejected upstream).",
                )
            else:
                response = _icici_error(f"Error calling ICICI Breeze API place_order: {e}")

        api_st = (response or {}).get("Status")
        if api_st != 200 and not place_order_sdk_exception:
            _succ = (response or {}).get("Success")
            _succ_hint = (
                "none"
                if _succ is None
                else (
                    f"dict_keys={list(_succ.keys())[:10]}"
                    if isinstance(_succ, dict)
                    else f"{type(_succ).__name__}_len={len(_succ)}"
                    if hasattr(_succ, "__len__") and not isinstance(_succ, (str, bytes))
                    else type(_succ).__name__
                )
            )
            _logger.warning(
                "place_order: broker returned non-success user_id=%s stock_code=%s exchange=%s action=%s "
                "qty=%s strike=%s right=%r price=%r expiry_api=%s api_status=%s api_error=%r success=%s",
                user_id,
                stock_code,
                exchange_code,
                act,
                qty_str,
                strike,
                rgt,
                prc,
                expiry_api,
                api_st,
                (response or {}).get("Error"),
                _succ_hint,
            )

        self._maybe_evict_session(user_id, response)
        return response

    def place_gtt_oco_exit_order(
        self,
        user_id,
        product_type,
        stock_code,
        exchange_code,
        strike_price,
        right,
        quantity,
        expiry_date,
        close_action,
        target_trigger_price,
        target_limit_price,
        stop_trigger_price,
        stop_limit_price,
    ):
        """Place a broker-monitored GTT OCO (target + stoploss) bracket to exit one held leg.
        Unlike the group Exit Rule, ICICI itself watches the triggers — no local P&L polling."""
        breeze = self.get_session_breeze(user_id)
        if breeze is None:
            return _icici_error("Unable to connect to broker. Please log out and log back in.")
        try:
            expiry_api = _expiry_to_breeze_place_order(expiry_date)
        except (ValueError, TypeError) as e:
            return _icici_error(f"Invalid expiry_date for GTT order: {expiry_date!r} ({e})")
        try:
            strike = strike_for_broker(strike_price)
        except (TypeError, ValueError):
            return _icici_error(f"Invalid strike_price for GTT order: {strike_price!r}")
        try:
            qty_str = str(abs(int(quantity)))
        except (TypeError, ValueError):
            return _icici_error(f"Invalid quantity for GTT order: {quantity!r}")

        act = str(close_action or "").strip().lower()
        rgt = str(right or "").strip().lower()
        prod = str(product_type or cfg.OPTIONS).strip().lower()
        index_or_stock = "index" if cfg.is_index_symbol(stock_code) else "stock"

        try:
            response = breeze.gtt_three_leg_place_order(
                exchange_code=exchange_code,
                stock_code=stock_code,
                product=prod,
                quantity=qty_str,
                expiry_date=expiry_api,
                right=rgt,
                strike_price=strike,
                gtt_type="oco",
                index_or_stock=index_or_stock,
                trade_date=str(today_ist_date()) + "T06:00:00.000Z",
                order_details=[
                    {
                        "gtt_leg_type": "target",
                        "action": act,
                        "limit_price": str(target_limit_price),
                        "trigger_price": str(target_trigger_price),
                    },
                    {
                        "gtt_leg_type": "stoploss",
                        "action": act,
                        "limit_price": str(stop_limit_price),
                        "trigger_price": str(stop_trigger_price),
                    },
                ],
            )
        except Exception as e:
            _logger.warning(
                "place_gtt_oco_exit_order: Breeze SDK exception user_id=%s stock_code=%s exchange=%s "
                "action=%s qty=%s strike=%s right=%r exc_type=%s err=%s",
                user_id, stock_code, exchange_code, act, qty_str, strike, rgt, type(e).__name__, e,
                exc_info=True,
            )
            return _icici_error(f"Error calling ICICI Breeze API gtt_three_leg_place_order: {e}")
        return response or _icici_error("Empty response from ICICI Breeze API gtt_three_leg_place_order")

    def get_gtt_order_book(self, user_id, exchange_code, from_date, to_date):
        breeze = self.get_session_breeze(user_id)
        if breeze is None:
            return _icici_error("Unable to connect to broker. Please log out and log back in.")
        try:
            return breeze.gtt_order_book(exchange_code=exchange_code, from_date=from_date, to_date=to_date)
        except Exception as e:
            _logger.warning(
                "get_gtt_order_book: Breeze SDK exception user_id=%s exc_type=%s err=%s",
                user_id, type(e).__name__, e, exc_info=True,
            )
            return _icici_error(f"Error calling ICICI Breeze API gtt_order_book: {e}")

    def cancel_gtt_order(self, user_id, exchange_code, gtt_order_id):
        breeze = self.get_session_breeze(user_id)
        if breeze is None:
            return _icici_error("Unable to connect to broker. Please log out and log back in.")
        try:
            return breeze.gtt_three_leg_cancel_order(exchange_code=exchange_code, gtt_order_id=gtt_order_id)
        except Exception as e:
            _logger.warning(
                "cancel_gtt_order: Breeze SDK exception user_id=%s gtt_order_id=%s exc_type=%s err=%s",
                user_id, gtt_order_id, type(e).__name__, e, exc_info=True,
            )
            return _icici_error(f"Error calling ICICI Breeze API gtt_three_leg_cancel_order: {e}")

    def break_order_finalize_user_messages(
        self,
        contract_label: str,
        price_f: float,
        success_qty_chunks: list[int],
        danger_messages: list[str],
        aggressive_limit: bool = False,
    ) -> list[dict]:
        """Success + failure toasts matching break_order (used by /order/break-finalize)."""
        if aggressive_limit and not cfg.AGGRESSIVE_LIMIT_ORDER_ENABLED:
            aggressive_limit = False
        messages: list[dict] = []
        if success_qty_chunks:
            n_orders = len(success_qty_chunks)
            total_q = sum(success_qty_chunks)
            if aggressive_limit:
                price_clause = "at ICICI aggressive limit prices (LTP-derived)"
            else:
                total_prem = total_q * price_f
                prem_s = _format_inr_integer_indian(total_prem)
                price_clause = f"for a total premium of {prem_s}"
            messages.append(
                {
                    "type": cfg.SUCCESS,
                    "message": (
                        f"Successfully placed {_format_indian_integer_digits(n_orders)} orders "
                        f"for {contract_label} totaling "
                        f"{_format_indian_integer_digits(total_q)} quantity {price_clause}"
                    ),
                }
            )
        if danger_messages:
            k = len(danger_messages)
            preview = "; ".join(danger_messages[:4])
            tail = (
                f" (+{_format_indian_integer_digits(k - 4)} more)"
                if k > 4
                else ""
            )
            messages.append(
                {
                    "type": cfg.DANGER,
                    "message": (
                        f"Failed to place {_format_indian_integer_digits(k)} order(s): "
                        f"{preview}{tail}"
                    ),
                }
            )
        return messages

    def _park_placement_for_execution(
        self,
        user_id: str,
        *,
        product_type: str,
        stock_code: str,
        exchange_code: str,
        expiry_date: str,
        right: str,
        strike_price: str,
        total_qty: str,
        price: str,
        action: str,
        aggressive_limit: bool = False,
        chunk_qty: str | None = None,
        batch_group_id: str | None = None,
    ) -> list[str]:
        item = ParkedOrderItem(
            product_type=product_type or "Options",
            stock_code=stock_code,
            exchange_code=exchange_code,
            expiry_date=expiry_date,
            right=right,
            strike_price=str(strike_price),
            quantity=str(total_qty),
            price="0" if aggressive_limit else str(price),
            action=action,  # type: ignore[arg-type]
            aggressive_limit=aggressive_limit,
            chunk_qty=chunk_qty,
            batch_group_id=batch_group_id,
        )
        rows = self.create_parked_orders(user_id, [item])
        return [r.id for r in rows]

    @staticmethod
    def _market_closed_park_message(reason: str | None = None) -> str:
        r = reason or market_closed_reason()
        return (
            f"Market is closed ({r}). Order parked for execution — "
            "execute it from Parked Execution when the market opens."
        )

    def break_order_chunk_defaults(
        self, stock_code: str, expiry_date: str, exchange_code: str
    ) -> dict:
        """Lot size and freeze-aligned max units per order (for confirm UIs)."""
        sym = str(stock_code or "").strip()
        exp = str(expiry_date or "").strip()
        ex = str(exchange_code or cfg.NFO).strip() or cfg.NFO
        if not sym or not exp:
            return {
                "ok": False,
                "error": "stock_code and expiry_date are required.",
            }
        qty_limits = self.fetch_qty_limits(sym, exchange_code=ex)
        if qty_limits is None:
            return {
                "ok": False,
                "error": (
                    "No entries for stock code "
                    + sym
                    + " in the Options Quantity Limits file"
                ),
            }
        lot_size = self.fetch_lot_size(sym, exp, exchange_code=ex)
        try:
            ls = int(lot_size)
        except (TypeError, ValueError):
            return {"ok": False, "error": "Could not resolve lot size for this contract."}
        if ls <= 0:
            return {"ok": False, "error": "Invalid lot size for this contract."}
        freeze_aligned = (max(1, int(qty_limits)) // ls) * ls
        return {
            "ok": True,
            "lot_size": ls,
            "default_chunk_qty": int(freeze_aligned),
            "max_chunk_qty": int(freeze_aligned),
        }

    def break_order_place_chunk(
        self,
        user_id,
        stock_code,
        expiry_date,
        product_type,
        right,
        strike_price,
        total_qty,
        price,
        action,
        exchange_code: str,
        chunk_index: int,
        chunk_qty: str | None = None,
        aggressive_limit: bool = False,
        from_parked_execution: bool = False,
        batch_group_id: str | None = None,
    ) -> dict:
        """Place a single slice of a split order for client-driven pacing on 429."""
        contract_label = f"{stock_code}-{expiry_date}-{strike_price}-{right}"
        try:
            price_f = float(str(price).strip() or 0)
        except (TypeError, ValueError):
            price_f = 0.0

        def _terminal(msg: str) -> dict:
            return {
                "terminal_messages": [
                    {"type": cfg.DANGER, "message": msg},
                ],
                "chunk_index": chunk_index,
                "total_chunks": 0,
                "contract_label": contract_label,
                "price_f": price_f,
                "rate_limited": False,
                "parked_for_execution": False,
                "success": False,
                "placed_quantity": 0,
                "danger_line": None,
            }

        if aggressive_limit and not cfg.AGGRESSIVE_LIMIT_ORDER_ENABLED:
            return _terminal(AGGRESSIVE_LIMIT_DISABLED_MESSAGE)

        if chunk_index == 0 and not is_market_open():
            closed_reason = market_closed_reason()
            if from_parked_execution:
                return _terminal(
                    f"Market is still closed ({closed_reason}). Your order remains parked."
                )
            parked_ids = self._park_placement_for_execution(
                user_id,
                product_type=product_type,
                stock_code=stock_code,
                exchange_code=exchange_code,
                expiry_date=expiry_date,
                right=right,
                strike_price=strike_price,
                total_qty=total_qty,
                price=price,
                action=action,
                aggressive_limit=aggressive_limit,
                chunk_qty=chunk_qty,
                batch_group_id=batch_group_id,
            )
            return {
                "terminal_messages": [],
                "chunk_index": 0,
                "total_chunks": 1,
                "contract_label": contract_label,
                "price_f": price_f,
                "rate_limited": False,
                "parked_for_execution": True,
                "parked_order_ids": parked_ids,
                "market_closed_reason": closed_reason,
                "success": False,
                "placed_quantity": 0,
                "danger_line": None,
            }

        try:
            total_i = int(str(total_qty).strip())
        except (TypeError, ValueError):
            return _terminal("Invalid total quantity.")

        if total_i <= 0:
            return _terminal("Quantity must be a positive integer.")

        qty_limits = self.fetch_qty_limits(stock_code, exchange_code=exchange_code)
        if qty_limits is None:
            return _terminal(
                "No entries for stock code "
                + stock_code
                + " in the Options Quantity Limits file"
            )

        lot_size = self.fetch_lot_size(stock_code, expiry_date, exchange_code=exchange_code)
        default_per = (max(1, int(qty_limits)) // lot_size) * lot_size
        qty_per_order = default_per

        raw_chunk = str(chunk_qty).strip() if chunk_qty is not None else ""
        if raw_chunk:
            try:
                want = int(raw_chunk)
            except (TypeError, ValueError):
                return _terminal("Invalid chunk quantity.")
            if want <= 0:
                return _terminal("Chunk quantity must be a positive integer.")
            qty_per_order = (want // lot_size) * lot_size
            if qty_per_order < lot_size:
                qty_per_order = lot_size
            if qty_per_order > default_per:
                return _terminal(
                    "Chunk size cannot exceed "
                    + _format_indian_integer_digits(int(default_per))
                    + " (exchange freeze for this contract)."
                )

        iterations = int(total_i / qty_per_order)
        remainder = int(total_i) % int(qty_per_order)
        total_chunks = iterations + (1 if remainder > 0 else 0)

        if chunk_index < 0 or chunk_index >= total_chunks:
            return _terminal("Invalid chunk index for this order size.")

        if chunk_index < iterations:
            qty_this = qty_per_order
        else:
            qty_this = remainder

        response = self.place_order(
            user_id=user_id,
            product_type=product_type,
            stock_code=stock_code,
            action=action,
            strike_price=strike_price,
            right=right,
            price=price,
            expiry_date=expiry_date,
            quantity=qty_this,
            exchange_code=exchange_code,
            aggressive_limit=aggressive_limit,
        )
        rl, daily_exhausted = _order_rate_limit_flags(response)
        ok = bool(response and response.get("Status") == 200)
        danger_line = None
        if not ok and not rl:
            err = (response or {}).get("Error") or "Unknown error"
            price_label = (
                "Aggressive limit (LTP-derived)"
                if aggressive_limit
                else _format_inr_integer_indian(price_f)
            )
            danger_line = (
                str(err)
                + contract_label
                + " | Qty = "
                + _format_indian_integer_digits(int(qty_this))
                + " | Price = "
                + price_label
            )
        elif rl and daily_exhausted:
            danger_line = (response or {}).get("Error") or "Broker daily limit reached."

        return {
            "terminal_messages": [],
            "chunk_index": chunk_index,
            "total_chunks": total_chunks,
            "contract_label": contract_label,
            "price_f": price_f,
            "rate_limited": rl,
            "daily_limit_exhausted": daily_exhausted,
            "parked_for_execution": False,
            "success": ok,
            "placed_quantity": int(qty_this) if ok else 0,
            "danger_line": danger_line,
            "default_chunk_qty": int(default_per),
            "effective_chunk_qty": int(qty_per_order),
        }

    def break_order(self,user_id,stock_code,expiry_date,product_type,right,strike_price,total_qty,price,action, exchange_code: str = cfg.NFO, aggressive_limit: bool = False):
        if aggressive_limit and not cfg.AGGRESSIVE_LIMIT_ORDER_ENABLED:
            return [{"type": cfg.DANGER, "message": AGGRESSIVE_LIMIT_DISABLED_MESSAGE}]
        messages: list[dict] = []
        contract_label = f"{stock_code}-{expiry_date}-{strike_price}-{right}"
        try:
            price_f = float(str(price).strip() or 0)
        except (TypeError, ValueError):
            price_f = 0.0

        qty_limits = self.fetch_qty_limits(stock_code, exchange_code=exchange_code)
        if qty_limits is None:
            messages.append(
                {
                    "type": cfg.DANGER,
                    "message": "No entries for stock code "
                    + stock_code
                    + " in the Options Quantity Limits file",
                }
            )
        elif not is_market_open():
            closed_reason = market_closed_reason()
            self._park_placement_for_execution(
                user_id,
                product_type=product_type,
                stock_code=stock_code,
                exchange_code=exchange_code,
                expiry_date=expiry_date,
                right=right,
                strike_price=strike_price,
                total_qty=str(total_qty),
                price=price,
                action=action,
                aggressive_limit=aggressive_limit,
            )
            messages.append(
                {
                    "type": cfg.INFO,
                    "message": self._market_closed_park_message(closed_reason),
                }
            )
        else:
            lot_size = self.fetch_lot_size(stock_code, expiry_date, exchange_code=exchange_code)
            qty_per_order = (max(1, int(qty_limits)) // lot_size) * lot_size  # avoid ZeroDivisionError when qty_limits is 1
            iterations = int(int(total_qty) / qty_per_order)
            remainder = int(total_qty) % int(qty_per_order)
            success_qty_chunks: list[int] = []
            danger_messages: list[str] = []
            price_label = (
                "Aggressive limit (LTP-derived)"
                if aggressive_limit
                else _format_inr_integer_indian(price_f)
            )

            while iterations > 0:
                response = self.place_order(
                    user_id=user_id,
                    product_type=product_type,
                    stock_code=stock_code,
                    action=action,
                    strike_price=strike_price,
                    right=right,
                    price=price,
                    expiry_date=expiry_date,
                    quantity=qty_per_order,
                    exchange_code=exchange_code,
                    aggressive_limit=aggressive_limit,
                )
                iterations -= 1
                if response and response.get("Status") == 200:
                    success_qty_chunks.append(int(qty_per_order))
                else:
                    err = (response or {}).get("Error") or "Unknown error"
                    danger_messages.append(
                        str(err)
                        + contract_label
                        + " | Qty = "
                        + _format_indian_integer_digits(int(qty_per_order))
                        + " | Price = "
                        + price_label
                    )

            if remainder > 0:
                response = self.place_order(
                    user_id=user_id,
                    product_type=product_type,
                    stock_code=stock_code,
                    action=action,
                    strike_price=strike_price,
                    right=right,
                    price=price,
                    expiry_date=expiry_date,
                    quantity=remainder,
                    exchange_code=exchange_code,
                    aggressive_limit=aggressive_limit,
                )
                if response and response.get("Status") == 200:
                    success_qty_chunks.append(int(remainder))
                else:
                    err = (response or {}).get("Error") or "Unknown error"
                    danger_messages.append(
                        str(err)
                        + contract_label
                        + " | Qty = "
                        + _format_indian_integer_digits(int(remainder))
                        + " | Price = "
                        + price_label
                    )

            if danger_messages:
                k = len(danger_messages)
                preview = "; ".join(danger_messages[:4])
                tail = (
                    f" (+{_format_indian_integer_digits(k - 4)} more)"
                    if k > 4
                    else ""
                )
                _logger.warning(
                    "break_order: failed_chunks user_id=%s contract=%s failed_count=%s "
                    "total_qty_requested=%s qty_per_order=%s remainder=%s lot_size=%s freeze_limit=%s "
                    "preview=%r",
                    user_id,
                    contract_label,
                    k,
                    total_qty,
                    qty_per_order,
                    remainder,
                    lot_size,
                    qty_limits,
                    _single_line_preview(preview + tail, 400),
                )
            messages.extend(
                self.break_order_finalize_user_messages(
                    contract_label,
                    price_f,
                    success_qty_chunks,
                    danger_messages,
                    aggressive_limit=aggressive_limit,
                )
            )

        return messages

    def get_ICICImaster_date(self):
        # Get the creation time of the file
        result = {}
        try:
            creation_timestamp = os.path.getmtime(cfg.DATA_PATH+cfg.SCRIP_DB)
            creation_utc = datetime.datetime.fromtimestamp(
                creation_timestamp, tz=datetime.timezone.utc
            )
            creation_ist = creation_utc.astimezone(IST)
            creation = {}
            creation['date'] = creation_ist.strftime("%d-%b-%Y")
            creation['age'] = (today_ist_date() - creation_ist.date()).days
            
            result['Status'] = 200
            result['Error'] = None
            result['Success'] = creation
        except:
            result['Status'] = 400
            result['Error'] = "Error getting creating timestamp of master data file at : "+cfg.DATA_PATH+cfg.SCRIP_MASTER
            result['Success'] = None

        return result

    @staticmethod
    def _resolve_extracted_file(output_path: str, extracted_paths: list[str], filename: str) -> str:
        """Prefer DATA_PATH/filename, else any extracted path with matching basename (ZIP may use subfolders)."""
        direct = os.path.join(output_path, filename)
        if os.path.isfile(direct):
            return direct
        for p in extracted_paths:
            if os.path.isfile(p) and os.path.basename(p) == filename:
                return p
        return direct

    def update_ICICImaster(self, *, publish_scrip_index: bool = True):
        url = cfg.ICICI_MASTERFILE_URL
        output_path = cfg.DATA_PATH
        temp_zip_path = os.path.join(output_path, "temp_icici_master.zip")
        Path(output_path).mkdir(parents=True, exist_ok=True)

        extracted_paths = []
        try:
            _logger.info("Downloading ICICI master from %s", url)
            response = requests.get(url, stream=True)
            response.raise_for_status()
            with open(temp_zip_path, "wb") as temp_file:
                for chunk in response.iter_content(chunk_size=8192):
                    temp_file.write(chunk)
            _logger.info("ICICI master download completed")

            with zipfile.ZipFile(temp_zip_path, "r") as zip_ref:
                for name in zip_ref.namelist():
                    full = os.path.join(output_path, name)
                    extracted_paths.append(full)
                    # Include parent dirs for cleanup (ZIP may have folder/file structure)
                    head = os.path.dirname(full)
                    while head and head != output_path.rstrip(os.sep):
                        extracted_paths.append(head)
                        head = os.path.dirname(head)
                zip_ref.extractall(output_path)
                _logger.info("Extracted %d items to %s", len(zip_ref.namelist()), output_path)

            nse_limits_path = self._resolve_extracted_file(
                output_path, extracted_paths, cfg.LIMITS_MASTER_NSE
            )
            bse_limits_path = self._resolve_extracted_file(
                output_path, extracted_paths, cfg.LIMITS_MASTER_BSE
            )
            self.load_qty_limits([
                (nse_limits_path, cfg.NFO),
                (bse_limits_path, cfg.BFO),
            ])

            scrip_targets = [
                (cfg.SCRIP_MASTER_NSE, cfg.NFO),
                (cfg.SCRIP_MASTER_BSE, cfg.BFO),
            ]

            for scrip_filename, exchange_code in scrip_targets:
                target_path = next(
                    (p for p in extracted_paths if os.path.basename(p) == scrip_filename and os.path.isfile(p)),
                    os.path.join(output_path, scrip_filename),
                )
                if os.path.exists(target_path):
                    self.load_scrip_master(target_path, exchange_code=exchange_code)
                else:
                    _logger.warning("Scrip master file %s not found in ZIP", scrip_filename)
            if publish_scrip_index:
                try:
                    from icici_breeze_backend.app.services.reference_data.scrip_index import publish_scrip_index_from_db

                    publish_scrip_index_from_db()
                except Exception as exc:
                    _logger.warning("Scrip index publish failed after master load: %s", exc)
        except requests.RequestException as e:
            _logger.warning("Error downloading ICICI master: %s", e, exc_info=True)
        except zipfile.BadZipFile as e:
            _logger.warning("Invalid ZIP file for ICICI master: %s", e, exc_info=True)
        finally:
            # Remove temp ZIP
            if os.path.exists(temp_zip_path):
                try:
                    os.remove(temp_zip_path)
                    _logger.debug("Removed %s", temp_zip_path)
                except OSError as e:
                    _logger.warning("Could not remove %s: %s", temp_zip_path, e)
            # Remove all extracted files and dirs (deepest first)
            seen = set()
            for path in sorted(set(extracted_paths), key=lambda p: -len(p)):
                if path in seen or not os.path.exists(path):
                    continue
                seen.add(path)
                try:
                    if os.path.isfile(path):
                        os.remove(path)
                        _logger.debug("Removed %s", path)
                    elif os.path.isdir(path):
                        os.rmdir(path)
                        _logger.debug("Removed dir %s", path)
                except OSError as e:
                    _logger.warning("Could not remove %s: %s", path, e)

    def load_qty_limits(self, limits_specs: list[tuple[str, str]]):
        """One-time seed of raw_limits_data from the static NSE/BSE freeze-limit files
        checked into backend/data/ (NSEFreezeLimits.txt, BSEFreezeLimits.txt) -- these
        are not part of ICICI's downloaded SecurityMaster.zip, they're maintained
        locally. After this initial load, users edit quantities via the Settings page
        (`POST /quantity-limits`, route_settings.py) -- so once raw_limits_data already
        has rows, this must never touch it again, or the next scheduled/startup
        reference-data refresh would silently wipe out those edits.

        limits_specs: list of (file_path, segment_code) e.g. (path_to_NSEFreezeLimits.txt, NFO).
        """
        conn = _scrip_master_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS raw_limits_data (
                InstrumentName TEXT,
                ShortName TEXT,
                ExchangeCode TEXT,
                SegmentCode TEXT,
                QtyLimit INTEGER,
                PRIMARY KEY (ShortName, ExchangeCode, SegmentCode)
            )
            ''')

            already_seeded = cursor.execute("SELECT COUNT(*) FROM raw_limits_data").fetchone()[0]
            if already_seeded:
                _logger.debug(
                    "raw_limits_data already seeded (%d rows); skipping reload to preserve Settings edits",
                    already_seeded,
                )
                return

            rows = []
            for limits_path, segment_code in limits_specs:
                if not limits_path or not os.path.isfile(limits_path):
                    raise FileNotFoundError(
                        f"Quantity limits seed file missing (master load aborted): {limits_path}"
                    )
                with open(limits_path, newline='') as limitsfile:
                    reader = csv.DictReader(limitsfile)
                    for row in reader:
                        rows.append((
                            row.get('InstrumentName', ''),
                            row.get('ShortName', ''),
                            row.get('ExchangeCode', ''),
                            segment_code,
                            int(row.get('QtyLimit', 0) or 0),
                        ))

            if not rows:
                raise ValueError(
                    "No quantity limits rows loaded from NSE/BSE freeze limits seed files (files empty or invalid)."
                )

            cursor.executemany(
                "INSERT OR REPLACE INTO raw_limits_data (InstrumentName, ShortName, ExchangeCode, SegmentCode, QtyLimit) VALUES (?,?,?,?,?)",
                rows,
            )
            conn.commit()
            _logger.info("Seeded raw_limits_data from static freeze-limit files (%d rows, one-time)", len(rows))
        finally:
            conn.close()

    def get_funds(self,user_id):
        breeze = self.get_session_breeze(user_id)
        try:
            response = breeze.get_funds()
            if response is None:
                response = _icici_error("ICICI Breeze API get_funds() returned NULL")
        except Exception as e:
            response = _icici_error(f"Error calling ICICI Breeze API get_funds(): {e}")
        self._maybe_evict_session(user_id, response)
        return response

    def get_financial_years(self):
        # Get today's date (IST; India FY)
        today = now_ist()
        
        # Extract the current year
        current_year = today.year
        fys = []
        # Determine the start and end of the financial year
        for i in range(5):
            fy = {}
            if today.month >= 4:  # Financial year starts in April
                start_date = datetime.datetime(current_year - i, 4, 1)
                end_date = datetime.datetime(current_year - i + 1, 3, 31)
            else:  # Financial year for dates before April
                start_date = datetime.datetime(current_year - i - 1, 4, 1)
                end_date = datetime.datetime(current_year - i, 3, 31)        
            fy['year'] = f"{start_date.year}-{end_date.year - 2000}"
            fy['start'] = start_date.strftime("%Y-%m-%d")
            fy['end'] = end_date.strftime("%Y-%m-%d")
            fys.append(fy)
        return fys

    def get_performance(self,user_id,margin,start,end):
        breeze = self.get_session_breeze(user_id)
        # dates = self.get_current_fy_dates()
        start_date = start+"T06:00:00.000Z"
        end_date = end+"T06:00:00.000Z"
        # Annualised ROI denominator: elapsed FY length. In-progress FY → start→today only; closed FY
        # → full start→end (do not keep growing the denominator after the FY ends).
        start_d = datetime.datetime.strptime(start, "%Y-%m-%d").date()
        end_d = datetime.datetime.strptime(end, "%Y-%m-%d").date()
        today_d = today_ist_date()
        if today_d > end_d:
            period = max(1, (end_d - start_d).days)
        else:
            period = max(1, (today_d - start_d).days)
        product_type = cfg.OPTIONS

        try:
            def _fetch_trades(exchange_code):
                resp = breeze.get_trade_list(
                    from_date=start_date,
                    to_date=end_date,
                    exchange_code=exchange_code,
                    product_type=product_type,
                    action="",
                    stock_code="",
                )
                self._maybe_evict_session(user_id, resp)
                return resp

            # NFO and BFO trade lists are independent calls; fetch them concurrently
            # instead of waiting on two sequential ICICI round-trips.
            with ThreadPoolExecutor(max_workers=2) as executor:
                trade_responses = list(executor.map(_fetch_trades, (cfg.NFO, cfg.BFO)))

            all_trades = []
            first_error = None
            for trades_resp in trade_responses:
                if trades_resp.get("Status") == 200 and trades_resp.get("Success"):
                    all_trades.extend(trades_resp.get("Success") or [])
                elif first_error is None and trades_resp.get("Status") != 200:
                    first_error = trades_resp

            trades = {"Status": 200 if all_trades else (first_error.get("Status") if first_error else 400), "Success": all_trades, "Error": first_error.get("Error") if first_error else "No trades found"}
        except Exception as e:
            trades = _icici_error(f"Error calling ICICI Breeze API get_trade_list: {e}")
        self._maybe_evict_session(user_id, trades)
        performance = {}
        if trades['Status'] == 200:
            performance['Status'] = 200
            performance['Error'] = None
            performance['Success'] = {}
            performance['Success']['premium_earned'] = 0
            performance['Success']['premium_paid'] = 0
            performance['Success']['brokerage'] = 0
            performance['Success']['taxes'] = 0
            performance['Success']['net_pnl'] = 0

            per_month = defaultdict(lambda: {"pnl": 0.0, "brokerage": 0.0, "taxes": 0.0})
            # Weekly buckets are a strictly finer partition of the monthly ones — same
            # trades, same arithmetic, keyed by the Monday of the trade's week instead.
            per_week = defaultdict(lambda: {"pnl": 0.0, "brokerage": 0.0, "taxes": 0.0})

            for trade in trades['Success']:
                trade_date = trade['trade_date']
                trade_date = datetime.datetime.strptime(trade_date, "%d-%b-%Y")
                trade_month_name = trade_date.strftime("%b-%y")
                trade_week_start = (trade_date - datetime.timedelta(days=trade_date.weekday())).strftime("%Y-%m-%d")
                buckets = (per_month[trade_month_name], per_week[trade_week_start])
                premium = float(trade['quantity']) * float(trade['average_cost'])
                action = str(trade.get("action", "")).strip()
                if action == cfg.SELL:
                    performance['Success']['premium_earned'] += premium
                    performance['Success']['net_pnl'] += premium
                    for bucket in buckets:
                        bucket['pnl'] += premium

                if action == cfg.BUY:
                    performance['Success']['premium_paid'] += premium
                    performance['Success']['net_pnl'] -= premium
                    for bucket in buckets:
                        bucket['pnl'] -= premium

                performance['Success']['brokerage'] += float(trade['brokerage_amount'])
                performance['Success']['taxes'] += float(trade['total_taxes'])
                performance['Success']['net_pnl'] = performance['Success']['net_pnl'] - float(trade['brokerage_amount']) - float(trade['total_taxes'])
                for bucket in buckets:
                    bucket['brokerage'] += float(trade['brokerage_amount'])
                    bucket['taxes'] += float(trade['total_taxes'])
                    bucket['pnl'] = bucket['pnl'] - float(trade['brokerage_amount']) - float(trade['total_taxes'])

            net_pnl = float(performance["Success"]["net_pnl"])
            margin_denom = margin if margin and float(margin) > 0 else None
            elapsed_days = max(1, int(period))
            if margin_denom:
                performance["Success"]["annualised_roi_breakdown"] = {
                    "net_pnl": net_pnl,
                    "elapsed_days": elapsed_days,
                    "total_margin": float(margin_denom),
                }
            if margin_denom:
                performance["Success"]["annualised_roi"] = (
                    _annualized_roi_fraction_on_span(
                        net_pnl,
                        elapsed_days,
                        float(margin_denom),
                    )
                )
            else:
                performance["Success"]["annualised_roi"] = 0.0
            monthly = [{"month": month, "pnl": values["pnl"], "brokerage": values["brokerage"], "taxes": values["taxes"]}for month, values in per_month.items()]
            monthly.sort(key=lambda x: datetime.datetime.strptime(x["month"], "%b-%y"))
            performance['Success']['monthly'] = monthly
            # `week` is the ISO date of that week's Monday — the frontend needs real
            # dates (not a display label) to pad out the FY's full week grid.
            weekly = [{"week": week, "pnl": values["pnl"], "brokerage": values["brokerage"], "taxes": values["taxes"]}for week, values in per_week.items()]
            weekly.sort(key=lambda x: x["week"])
            performance['Success']['weekly'] = weekly

        else:
            performance['Success'] = None
            performance['Status'] = trades['Status']
            performance['Error'] = trades['Error']

        return performance

    def store_error(self, error):
        self.errors.append(error.copy() if isinstance(error, dict) else {"contents": str(error)})

    def retrieve_errors(self):
        error_log = self.errors
        self.errors = []
        return error_log

    def list_option_strikes(
        self, stock_code: str, expiry_date: str, exchange_code: str = cfg.NFO
    ) -> list[Strike]:
        """Distinct tradeable strike prices from in-memory scrip index."""
        expiry_sql_values = _scrip_master_expiry_sql_values(expiry_date)
        if not expiry_sql_values:
            return []
        expiry_display = expiry_sql_values[0]
        from icici_breeze_backend.app.services.reference_data.scrip_index import (
            list_tradeable_strikes_memory,
        )

        return list_tradeable_strikes_memory(
            stock_code, expiry_display, exchange_code=exchange_code
        )

    @staticmethod
    def strike_interval(strikes: list[Strike]) -> float:
        """Minimum positive gap between adjacent strikes (fallback 50)."""
        if len(strikes) < 2:
            return 50
        gaps = [strikes[i + 1] - strikes[i] for i in range(len(strikes) - 1) if strikes[i + 1] > strikes[i]]
        return min(gaps) if gaps else 50

    @staticmethod
    def search_interval(strikes: list[Strike], spot: float) -> float:
        """Modal strike gap near spot for range padding (fallback 50)."""
        if len(strikes) < 2:
            return 50
        atm_idx = min(range(len(strikes)), key=lambda i: abs(strikes[i] - spot))
        lo_idx = max(0, atm_idx - 10)
        hi_idx = min(len(strikes) - 1, atm_idx + 10)
        band = strikes[lo_idx : hi_idx + 1]
        if len(band) < 2:
            return processor.strike_interval(strikes)
        gaps = [band[i + 1] - band[i] for i in range(len(band) - 1) if band[i + 1] > band[i]]
        if not gaps:
            return 50
        return Counter(gaps).most_common(1)[0][0]

    def fetch_lot_size(self, stock_code, expiry_date, exchange_code: str = cfg.NFO):
        from icici_breeze_backend.app.services.reference_data.scrip_index import (
            get_lot_size_memory,
            list_tradeable_strikes_memory,
        )
        from icici_breeze_backend.app.services.reference_data.scrip_master_sql import (
            scrip_master_expiry_sql_values,
        )

        expiry_sql_values = _scrip_master_expiry_sql_values(expiry_date)
        if not expiry_sql_values:
            return None
        expiry_display = expiry_sql_values[0]
        lot = get_lot_size_memory(stock_code, expiry_display, exchange_code=exchange_code)
        if lot:
            return lot
        if not list_tradeable_strikes_memory(stock_code, expiry_display, exchange_code=exchange_code):
            return None
        return None

    def fetch_stock_codes(self, exchange_code: str = cfg.NFO):
        from icici_breeze_backend.app.services.reference_data.scrip_index import get_underlyings

        cached = get_underlyings(exchange_code)
        if cached:
            return cached
        with _scrip_master_connection() as conn:
            if exchange_code == cfg.NFO:
                cursor = conn.execute(
                    "SELECT DISTINCT ShortName, CompanyName, ExpiryDate FROM scrip_master WHERE SegmentCode = ? OR SegmentCode IS NULL",
                    (exchange_code,),
                )
            else:
                cursor = conn.execute(
                    "SELECT DISTINCT ShortName, CompanyName, ExpiryDate FROM scrip_master WHERE SegmentCode = ?",
                    (exchange_code,),
                )
            rows = cursor.fetchall()

        # Step 1: Group by (ShortName, LongName)
        grouped = defaultdict(list)
        for short, long, expiry in rows:
            grouped[(short, long)].append(expiry)

        # Step 2: Convert to desired list of dictionaries
        summary = [
            {"stock_code": short, "long_name": long, "expiry_dates": dates}
            for (short, long), dates in grouped.items()
        ]
        return summary

    def fetch_qty_limits(self, stock_code, exchange_code: str = cfg.NFO):
        # Fetches the quantity limit for the provided stock_code from the scrip_master table.
        with _scrip_master_connection() as conn:
            if exchange_code == cfg.NFO:
                cursor = conn.execute(
                    "SELECT QuantityLimit FROM scrip_master WHERE ShortName = ? AND (SegmentCode = ? OR SegmentCode IS NULL) LIMIT 1",
                    (stock_code, exchange_code),
                )
            else:
                cursor = conn.execute(
                    "SELECT QuantityLimit FROM scrip_master WHERE ShortName = ? AND SegmentCode = ? LIMIT 1",
                    (stock_code, exchange_code),
                )
            row = cursor.fetchone()
        return row[0] if row else None

    def load_scrip_master(self, scrip_master_path, exchange_code: str):
        conn = _scrip_master_connection()
        try:
            _logger.info("Loading scrip master from %s", scrip_master_path)
            cursor = conn.cursor()

            # Create tables if not exist
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS raw_scrip_data (
                Token INTEGER PRIMARY KEY,
                InstrumentName TEXT,
                ShortName TEXT,
                Series TEXT,
                ExpiryDate DATE,
                StrikePrice REAL,
                OptionType TEXT,
                CALevel INTEGER,
                PermittedToTrade INTEGER,
                IssueCapital INTEGER,
                WarningQty INTEGER,
                FreezeQty INTEGER,
                CreditRating TEXT,
                NormalMarketStatus INTEGER,
                OddLotMarketStatus INTEGER,
                SpotMarketStatus INTEGER,
                AuctionMarketStatus INTEGER,
                NormalMarketEligibility TEXT,
                OddLotMarketEligibility TEXT,
                SpotMarketEligibility TEXT,
                AuctionMarketEligibility TEXT,
                IssueRate INTEGER,
                IssueStartDate DATE,
                InterestPaymentDate DATE,
                IssueMaturityDate DATE,
                MarginPercentage INTEGER,
                MinimumLotQty INTEGER,
                LotSize INTEGER,
                TickSize INTEGER,
                CompanyName TEXT,
                ListingDate DATE,
                ExpulsionDate DATE,
                ReAdmissionDate DATE,
                RecordDate DATE,
                LowPriceRange REAL,
                HighPriceRange REAL,
                SecurityExpiryDate DATE,
                NoDeliveryStartDate DATE,
                NoDeliveryEndDate DATE,
                MF TEXT,
                AON TEXT,
                ParticipantInMarketIndex TEXT,
                BookClsStartDate DATE,
                BookClsEndDate DATE,
                ExcerciseStartDate DATE,
                ExcerciseEndDate DATE,
                OldToken INTEGER,
                AssetInstrument TEXT,
                AssetName TEXT,
                AssetToken INTEGER,
                IntrinsicValue INTEGER,
                ExtrinsicValue INTEGER,
                ExcerciseStyle TEXT,
                EGM TEXT,
                AGM TEXT,
                Interest TEXT,
                Bonus TEXT,
                Rights TEXT,
                Dividends TEXT,
                ExAllowed TEXT,
                ExRejectionAllowed TEXT,
                PlAllowed TEXT,
                IsThisAsset TEXT,
                IsCorpAdjusted TEXT,
                LocalUpdateDatetime TEXT,
                DeleteFlag TEXT,
                Remarks TEXT,
                BasePrice INTEGER,
                ExchangeCode TEXT
            )
            ''')
            _logger.debug("Created raw_scrip_data table")

            cursor.execute('''
            CREATE TABLE IF NOT EXISTS scrip_master (
                ShortName TEXT,
                ExpiryDate DATE,
                StrikePrice INTEGER,
                OptionType TEXT,
                LotSize INTEGER,
                QuantityLimit INTEGER,
                CompanyName TEXT,
                ExchangeCode TEXT,
                SegmentCode TEXT,
                MarginPercentage INTEGER
            )
            ''')
            _logger.debug("Created scrip_master table")

            # Backward compatible schema upgrade for existing DBs.
            try:
                cursor.execute("ALTER TABLE scrip_master ADD COLUMN SegmentCode TEXT")
            except Exception:
                pass
            try:
                cursor.execute("ALTER TABLE scrip_master ADD COLUMN MarginPercentage INTEGER")
            except Exception:
                pass

            # Empty tables before loading (raw_limits_data is persistent, not cleared).
            # Also delete legacy rows where SegmentCode IS NULL so we don't accumulate duplicates.
            cursor.execute('DELETE FROM raw_scrip_data')
            cursor.execute('DELETE FROM scrip_master WHERE SegmentCode = ? OR SegmentCode IS NULL', (exchange_code,))
            _logger.debug("Emptied raw tables")

            # Load raw_scrip_data from CSV
            with open(scrip_master_path, newline='') as csvfile:
                reader = csv.DictReader(csvfile)
                rows = [(int(row['Token']), row['InstrumentName'], row['ShortName'], row['Series'], 
                    row['ExpiryDate'], float(row['StrikePrice']), row['OptionType'], 
                    int(row['CALevel']), int(row['PermittedToTrade']), int(row['IssueCapital']),
                    int(row['WarningQty']), int(row['FreezeQty']), row['CreditRating'], 
                    int(row['NormalMarketStatus']), int(row['OddLotMarketStatus']),
                    int(row['SpotMarketStatus']), int(row['AuctionMarketStatus']), 
                    row['NormalMarketEligibility'], row['OddLotMarketEligibility'], 
                    row['SpotMarketEligibility'], row['AuctionMarketEligibility'], 
                    int(row['IssueRate']), row['IssueStartDate'], row['InterestPaymentDate'],
                    row['IssueMaturityDate'], int(row['MarginPercentage']), 
                    int(row['MinimumLotQty']), int(row['LotSize']), int(row['TickSize']),
                    row['CompanyName'], row['ListingDate'], row['ExpulsionDate'], 
                    row['ReAdmissionDate'], row['RecordDate'], float(row['LowPriceRange']),
                    float(row['HighPriceRange']), row['SecurityExpiryDate'], 
                    row['NoDeliveryStartDate'], row['NoDeliveryEndDate'], row['MF'],
                    row['AON'], row['ParticipantInMarketIndex'], row['BookClsStartDate'],
                    row['BookClsEndDate'], row['ExcerciseStartDate'],
                    row['ExcerciseEndDate'], int(row['OldToken']), row['AssetInstrument'],
                    row['AssetName'], int(row['AssetToken']), int(row['IntrinsicValue']),
                    int(row['ExtrinsicValue']), row['ExcerciseStyle'], row['EGM'], 
                    row['AGM'], row['Interest'], row['Bonus'], row['Rights'], 
                    row['Dividends'], row['ExAllowed'], row['ExRejectionAllowed'],
                    row['PlAllowed'], row['IsThisAsset'], row['IsCorpAdjusted'],
                    row['LocalUpdateDatetime'], row['DeleteFlag'], row['Remarks'],
                    int(row['BasePrice']), row['ExchangeCode']) for row in reader]
                cursor.executemany('INSERT INTO raw_scrip_data VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', rows)
            _logger.info("Loaded raw_scrip_data from CSV (%d rows)", len(rows))

            # Requires raw_limits_data populated by load_qty_limits (master load fails if limits missing).
            cursor.execute(
                """
            INSERT INTO scrip_master (ShortName, ExpiryDate, StrikePrice, OptionType, LotSize, QuantityLimit, CompanyName, ExchangeCode, SegmentCode, MarginPercentage)
            SELECT
                scrips.ShortName,
                scrips.ExpiryDate,
                scrips.StrikePrice,
                scrips.OptionType,
                scrips.LotSize,
                limits.QtyLimit AS QuantityLimit,
                scrips.CompanyName,
                scrips.ExchangeCode,
                limits.SegmentCode,
                scrips.MarginPercentage
            FROM raw_scrip_data scrips
            JOIN raw_limits_data limits
              ON scrips.ShortName = limits.ShortName
             AND scrips.ExchangeCode = limits.ExchangeCode
             AND limits.SegmentCode = ?
            WHERE scrips.Series = "OPTION"
            """,
                (exchange_code,),
            )
            _logger.info("Inserted filtered data into scrip_master")

            from icici_breeze_backend.app.services.reference_data.ws_token_index import (
                clear_token_lookup_cache,
                populate_ws_token_index_from_raw,
            )

            populate_ws_token_index_from_raw(cursor, exchange_code)
            _logger.info("Updated ws_token_index for %s", exchange_code)
            try:
                clear_token_lookup_cache()
            except Exception:
                pass

            # Drop temp table (raw_limits_data is persistent)
            cursor.execute('DROP TABLE IF EXISTS raw_scrip_data')
            conn.commit()
            _logger.info("Scrip master load completed successfully")

            # Remove scrip master txt after load (re-extracted from ZIP on next update)
            if os.path.exists(scrip_master_path):
                try:
                    os.remove(scrip_master_path)
                    _logger.debug("Removed %s after load", scrip_master_path)
                except OSError as e:
                    _logger.warning("Could not remove %s: %s", scrip_master_path, e)

            return True
        finally:
            conn.close()

    @staticmethod
    def format_inr(value):
        try:
            value = float(value)
        except (TypeError, ValueError):
            return value

        is_negative = value < 0
        abs_value = abs(value)

        if abs_value >= 1e7:
            formatted = f"₹{abs_value / 1e7:.2f} Cr"
        elif abs_value >= 1e5:
            formatted = f"₹{abs_value / 1e5:.2f} L"
        else:
            """Add commas in Indian number format."""
            s = f"{int(abs_value):,}"
            # Convert 1,234,567 → 12,34,567
            s = re.sub(r"(\d)(?=(\d\d)+\d$)", r"\1,", s.replace(",", ""))            
            formatted = f"₹{s}"

        if is_negative:
            return Markup(f'<span class="text-danger">({formatted})</span>')
        else:
            return Markup(f'<span>{formatted}</span>')

    def strategy_builder_margin(
        self,
        user_id: str,
        exchange_code: str,
        legs: list,
        *,
        margin_source_override: str | None = None,
        baseline_only: bool = False,
        spot: float | None = None,
        iv: float | None = None,
        time_years: float | None = None,
        audit: "StrategyBuilderAuditSession | None" = None,
        audit_context: dict[str, Any] | None = None,
        audit_rationale: str | None = None,
    ):
        """Compute SPAN margin for strategy-builder legs using selected source."""
        breeze = self.get_session_breeze(user_id)
        if breeze is None:
            return {
                "Status": 400,
                "Error": "Unable to connect to broker. Please check your credentials and re-login.",
                "Success": None,
            }
        margin_source = margin_source_override or self.get_strategy_builder_margin_source(user_id)
        if margin_source not in (MARGIN_SOURCE_BREEZE, MARGIN_SOURCE_EXCHANGE):
            margin_source = MARGIN_SOURCE_BREEZE
        margin_input = []
        baseline_total = 0.0
        warnings: list[dict] = []
        missing_baseline_count = 0
        can_use_baseline = margin_source == MARGIN_SOURCE_EXCHANGE
        elm_legs: list[TradeLeg] = []
        elm_stock_code: str | None = None
        elm_expiry_api: str | None = None
        for leg in legs:
            ed = (leg.get("expiry_date") or "").strip()
            if len(ed) >= 10 and ed[4] == "-" and "T" not in ed:
                expiry_api = ed + "T06:00:00.000Z"
            elif "T" in ed:
                expiry_api = ed
            else:
                try:
                    expiry_api = _expiry_display_to_api(ed)
                except Exception:
                    return {"Status": 400, "Error": f"Invalid expiry_date: {ed}", "Success": None}
            try:
                strike_price = parse_strike(leg["strike_price"])
                if strike_price is None:
                    return {"Status": 400, "Error": "Invalid strike_price", "Success": None}
                quantity = int(leg["quantity"])
                action = leg["action"]
                product = leg.get("product_type") or cfg.OPTIONS
                stock_code = leg["stock_code"]
                right = leg["right"]
            except (TypeError, ValueError) as e:
                return {"Status": 400, "Error": f"Invalid leg fields: {e}", "Success": None}
            elm_legs.append(TradeLeg(right, action, strike_price, quantity, 0.0))
            if elm_stock_code is None:
                elm_stock_code = stock_code
                elm_expiry_api = expiry_api
            if can_use_baseline:
                baseline_margin = resolve_exchange_baseline_margin(
                    exchange_code=exchange_code,
                    stock_code=stock_code,
                    expiry_display=_expiry_api_to_display(expiry_api),
                    strike_price=strike_price,
                    right=right,
                    quantity=quantity,
                )
                if baseline_margin.get("found"):
                    baseline_total += float(baseline_margin.get("span_margin_required") or 0.0)
                    continue
                missing_baseline_count += 1
                warnings.append(
                    {
                        "type": "baseline_missing_contract",
                        "stock_code": stock_code,
                        "expiry_date": _expiry_api_to_display(expiry_api),
                        "strike_price": strike_price,
                        "right": right,
                        "message": (
                            "Contract missing in Exchange Risk Baseline."
                            if baseline_only
                            else "Contract missing in Exchange Risk Baseline; Breeze fallback used."
                        ),
                    }
                )
                if baseline_only:
                    continue
            margin_input.append(
                {
                    "strike_price": strike_price,
                    "quantity": quantity,
                    "product": product,
                    "action": action,
                    "expiry_date": expiry_api,
                    "stock_code": stock_code,
                    "right": right,
                }
            )
        elm_requirement: float | None = None
        elm_is_index = False
        elm_approximate = False
        if elm_legs and elm_stock_code and elm_expiry_api and spot is not None:
            elm_is_index = cfg.is_index_symbol(elm_stock_code)
            elm_same_day = _parse_option_expiry_date(elm_expiry_api) == today_ist_date()
            elm_expiry_display = _expiry_api_to_display(elm_expiry_api)
            elm_previous_close: float | None = None
            if not elm_same_day:
                for elm_leg in elm_legs:
                    row = _lookup_bhav_row(
                        elm_stock_code, elm_expiry_display, elm_leg.right, elm_leg.strike, exchange_code
                    )
                    candidate = 0.0
                    if row and row.get("spot_price"):
                        try:
                            candidate = float(row["spot_price"])
                        except (TypeError, ValueError):
                            candidate = 0.0
                    if candidate > 0:
                        elm_previous_close = candidate
                        break
                if elm_previous_close is None:
                    elm_previous_close = spot
                    elm_approximate = True
            lot_size_for_elm = self.fetch_lot_size(
                elm_stock_code, elm_expiry_display, exchange_code=exchange_code
            )
            if lot_size_for_elm and lot_size_for_elm > 0:
                elm_requirement = round(
                    elm_addon(
                        spot,
                        int(lot_size_for_elm),
                        elm_legs,
                        provision_elm=True,
                        is_index=elm_is_index,
                        previous_close=elm_previous_close,
                        same_day_expiry=elm_same_day,
                    ),
                    2,
                )
                elm_approximate = elm_approximate or not elm_is_index

        def _attach_elm(success: dict[str, Any]) -> dict[str, Any]:
            if elm_requirement is not None:
                success["elm_requirement"] = elm_requirement
                success["elm_is_index"] = elm_is_index
                success["elm_approximate"] = elm_approximate
            return success

        if can_use_baseline and baseline_only and missing_baseline_count > 0:
            return {
                "Status": 200,
                "Error": "",
                "Success": {
                    "span_margin_required": None,
                    "margin_source": MARGIN_SOURCE_EXCHANGE,
                    "warnings": warnings,
                },
            }
        if can_use_baseline and not margin_input:
            portfolio_margin = self._portfolio_baseline_span_margin(
                exchange_code,
                legs,
                spot=spot,
                iv=iv,
                time_years=time_years,
            )
            if portfolio_margin.get("found"):
                success: dict[str, Any] = {
                    "span_margin_required": portfolio_margin.get("span_margin_required"),
                    "margin_source": MARGIN_SOURCE_EXCHANGE,
                    "scanning_risk": portfolio_margin.get("scanning_risk"),
                    "net_option_value": portfolio_margin.get("net_option_value"),
                    "margin_benefit": portfolio_margin.get("margin_benefit"),
                }
                if warnings:
                    success["warnings"] = warnings
                scan_warnings = portfolio_margin.get("warnings") or []
                if scan_warnings:
                    success.setdefault("warnings", [])
                    if isinstance(success["warnings"], list):
                        success["warnings"].extend(
                            {"type": "portfolio_span", "message": w} for w in scan_warnings
                        )
                return {"Status": 200, "Error": "", "Success": _attach_elm(success)}
            return {
                "Status": 200,
                "Error": "",
                "Success": _attach_elm(
                    {
                        "span_margin_required": baseline_total,
                        "margin_source": MARGIN_SOURCE_EXCHANGE,
                        "warnings": warnings,
                    }
                ),
            }
        margin_rationale = (
            audit_rationale or "Batch SPAN margin for unique proposed leg structure."
        )
        icici_request: dict[str, Any] = {
            "exchange_code": exchange_code,
            "margin_list": margin_input,
        }
        if audit_context:
            icici_request.update(audit_context)
        try:
            _t0 = time.perf_counter()
            margins = breeze.margin_calculator(margin_input, exchange_code=exchange_code)
            _latency_ms = (time.perf_counter() - _t0) * 1000
            if audit:
                audit.record_icici_api_call(
                    "margin_calculator",
                    icici_request,
                    margins if isinstance(margins, dict) else None,
                    rationale=margin_rationale,
                    latency_ms=_latency_ms,
                )
        except Exception as e:
            err = _icici_error(f"Error calling ICICI Breeze API margin_calculator: {e}")
            if audit:
                audit.record_icici_api_call(
                    "margin_calculator",
                    icici_request,
                    err,
                    rationale=margin_rationale,
                )
            return err
        if margins is None:
            return _icici_error("Error calling ICICI Breeze API margin_calculator: no response")
        self._maybe_evict_session(user_id, margins)
        if margins.get("Status") == 200:
            base_span = float((margins.get("Success") or {}).get("span_margin_required") or 0.0)
            margins.setdefault("Success", {})
            margins["Success"]["span_margin_required"] = base_span + baseline_total
            margins["Success"]["margin_source"] = margin_source
            if warnings:
                margins["Success"]["warnings"] = warnings
            _attach_elm(margins["Success"])
        return margins

    # Real-time portfolio fetch for US3 (called by tests)
    def get_portfolio_realtime(self, broker_token=None, *args, **kwargs):
        """Fetch portfolio directly from ICICI API, forwarding broker token if provided.

        The method should honour a module-level ``icici_client`` instance so that
        tests can patch ``core.processor.icici_client`` without needing to create
        a Processor object first. A freshly constructed processor() instance
        doesn't automatically inherit attributes set on the module, which caused
        the earlier unit test failure.
        """
        # prefer the module-level client when one exists; this mirrors the
        # behaviour of the earlier implementation at the top of the file.
        if broker_token is not None and icici_client is not None:
            user_id = kwargs.get("user_id") or (args[0] if args else None)
            if user_id:
                breeze = self.get_session_breeze(user_id)
                if breeze is None:
                    return {
                        "Status": 400,
                        "Error": "Unable to connect to broker. Please check your credentials and re-login.",
                    }
                return icici_client.get_portfolio(broker_token, user_id=user_id, breeze=breeze)
            return icici_client.get_portfolio(broker_token, *args, **kwargs)

        # fallback: if caller provided a user_id, use get_positions
        if len(args) > 0:
            user_id = args[0]
            return self.get_positions(user_id)
        return {}

    # Real-time orders fetch for US3 (T077)
    def get_orders_realtime(self, broker_token=None, *args, **kwargs):
        """Fetch orders directly from ICICI API, forwarding broker token if provided.

        Similar to get_portfolio_realtime but for order history.
        Uses the module-level icici_client if available.
        No DB cache - always fetches from ICICI Breeze API (FR-010).
        """
        if broker_token is not None and icici_client is not None:
            user_id = kwargs.get('user_id')
            start_date = kwargs.get('start_date')
            end_date = kwargs.get('end_date')
            # Default date range: last 90 days if not provided
            if not start_date or not end_date:
                today = today_ist_date()
                end_date = end_date or today.strftime("%Y-%m-%d")
                start_date = start_date or (today - datetime.timedelta(days=90)).strftime("%Y-%m-%d")
            if user_id:
                breeze = self.get_session_breeze(user_id)
                if breeze is None:
                    return {
                        "Status": 400,
                        "Error": "Unable to connect to broker. Please check your credentials and re-login.",
                    }
                return icici_client.get_orders(
                    broker_token,
                    user_id=user_id,
                    start_date=start_date,
                    end_date=end_date,
                    breeze=breeze,
                )
            return icici_client.get_orders(
                broker_token,
                user_id=user_id,
                start_date=start_date,
                end_date=end_date,
            )

        # fallback: if caller provided a user_id (args or kwargs), use legacy breeze path
        user_id = args[0] if args else kwargs.get('user_id')
        if user_id:
            start = kwargs.get('start_date') or (today_ist_date() - datetime.timedelta(days=90)).strftime("%Y-%m-%d")
            end = kwargs.get('end_date') or today_ist_date().strftime("%Y-%m-%d")
            return self.get_orders(user_id, start, end)
        return {'Status': 400, 'Error': 'broker_token or user_id required for real-time orders'}

# module-level helpers for ease of use

def get_portfolio_realtime(*args, **kwargs):
    """Module-level proxy to Processor.get_portfolio_realtime."""
    return processor().get_portfolio_realtime(*args, **kwargs)


def get_orders_realtime(*args, **kwargs):
    """Module-level proxy to Processor.get_orders_realtime."""
    return processor().get_orders_realtime(*args, **kwargs)


