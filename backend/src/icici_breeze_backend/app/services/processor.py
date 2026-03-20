import hashlib
import math
import copy
import datetime
import sys
import time
from breeze_connect import BreezeConnect
import json
import icici_breeze_backend.app.core.config as cfg
import requests
import zipfile
import os
from pathlib import Path
from collections import defaultdict
import sqlite3
import csv
from zoneinfo import ZoneInfo
import re
from markupsafe import Markup

import logging

# Import ICICI client for real-time portfolio/orders fetch (Phase 5 US3)
from icici_breeze_backend.core.icici_client import icici_client  # tests patch app.services.processor.icici_client

_logger = logging.getLogger(__name__)

def _scrip_master_connection():
    """Return a new scrip_master DB connection (use in with-block to avoid shared connection)."""
    return sqlite3.connect(cfg.DATA_PATH + cfg.SCRIP_DB)


def _expiry_display_to_api(expiry: str) -> str:
    """Convert display format (DD-Mon-YYYY) to API format (YYYY-MM-DDT06:00:00.000Z)."""
    return datetime.datetime.strptime(expiry, "%d-%b-%Y").strftime("%Y-%m-%d") + "T06:00:00.000Z"


def _expiry_api_to_display(expiry: str) -> str:
    """Convert API format (YYYY-MM-DDT06:00:00.000Z or YYYY-MM-DD) to display format (DD-Mon-YYYY)."""
    s = expiry.removesuffix("T06:00:00.000Z")
    fmt = "%Y-%m-%d" if len(s.split("-")[0]) == 4 else "%d-%b-%Y"
    return datetime.datetime.strptime(s, fmt).strftime("%d-%b-%Y")


def _days_to_expiry(expiry_str: str) -> int:
    """Days until expiry (include both start and end). Accepts DD-Mon-YYYY or YYYY-MM-DD or YYYY-MM-DDT06:00:00.000Z."""
    s = expiry_str.removesuffix("T06:00:00.000Z")
    try:
        if len(s.split("-")[0]) == 4:  # YYYY-MM-DD
            future = datetime.datetime.strptime(s, "%Y-%m-%d")
        else:  # DD-Mon-YYYY
            future = datetime.datetime.strptime(s, "%d-%b-%Y")
    except ValueError:
        return 0
    return (future - datetime.datetime.today()).days + 2


def _icici_error(error_msg: str, status: int = 400) -> dict:
    """Standard ICICI API error response dict."""
    return {"Status": status, "Error": error_msg}


def _normalize_icici_response(data: dict) -> tuple:
    """Extract status, success payload, and error message from ICICI response (handles Status/status, etc.)."""
    status = data.get("Status") or data.get("status")
    success = data.get("Success") or data.get("success") or {}
    err_msg = data.get("Error") or data.get("error") or "Unknown error"
    return status, success, err_msg


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
            with sqlite3.connect(cfg.DATA_PATH + "db.sqlite3") as conn:
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

    def get_session_breeze(self, user_id):
        """Create one BreezeConnect session per request and reuse it (ICICI Invalid Checksum if we call generate_session multiple times).
        Uses broker token from HttpOnly cookie; fetches api_key and reconstructs secret from DB.
        Cross-request: consults breeze_session_cache (keyed by user_id + broker_token, TTL till midnight IST or config).
        """
        try:
            from icici_breeze_backend.app.auth.context import get_broker_token_for_request, get_breeze_session_for_request, set_breeze_session_for_request
            from icici_breeze_backend.app.services.breeze_session_cache import get as cache_get, set as cache_set
            # Reuse session created earlier in this request
            cached = get_breeze_session_for_request()
            if cached is not None:
                return cached
            broker_token = get_broker_token_for_request() or ""
            if not broker_token:
                _logger.warning("get_session_breeze: no broker token in request (cookie missing or empty) user_id=%s", user_id)
                return None
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
        """Return broker token from request context (HttpOnly cookie)."""
        try:
            from icici_breeze_backend.app.auth.context import get_broker_token_for_request
            return get_broker_token_for_request()
        except Exception:
            return None

    def _maybe_evict_session(self, user_id: str, response: dict | None) -> None:
        """If ICICI response indicates auth/session failure, evict session cache so next request creates fresh session."""
        try:
            from icici_breeze_backend.app.auth.context import get_broker_token_for_request
            from icici_breeze_backend.app.services.breeze_session_cache import evict_if_icici_auth_failure
            broker_token = get_broker_token_for_request() or ""
            evict_if_icici_auth_failure(user_id, broker_token, response)
        except Exception:
            pass

    def get_customer_details(self, user_id):
        breeze = self.get_session_breeze(user_id)
        session_token = self.get_session_token(user_id)
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
                return customer
            except Exception as e:
                if _is_transient(e) and attempt < max_retries - 1:
                    _logger.warning("get_customer_details: transient error attempt %d/%d user_id=%s: %s", attempt + 1, max_retries, user_id, e)
                    time.sleep(2**attempt)
                else:
                    _logger.warning("get_customer_details: exception user_id=%s: %s", user_id, e, exc_info=True)
                    return {"Status": 400, "Error": "Unable to fetch account details. Please try again or re-login."}
        return {"Status": 400, "Error": "Unable to fetch account details. Please try again or re-login."}

    def uncovered_shorts(self, user_id, stock_code=None, expiry_date=None, limits=None, elm=None, otm_call_distance=10, otm_put_distance=10, top=10, exchange_code: str = cfg.NFO):
        uncovered_shorts_result = {}
        uncovered_shorts_result['ce_options'] = {}
        uncovered_shorts_result['pe_options'] = {}
        if stock_code is None or expiry_date is None:
            return uncovered_shorts_result
        lot_size = self.fetch_lot_size(stock_code, expiry_date, exchange_code=exchange_code)
        expiry_date = _expiry_display_to_api(expiry_date)

        # Getting OTM CALL chain
        right = cfg.CALL
        otm_distance = otm_call_distance
        uncovered_shorts_result['ce_options'] = self.get_options(user_id,right,stock_code,lot_size,expiry_date,limits,elm,otm_distance,top, exchange_code=exchange_code)

        # Getting OTM PUT chain
        right = cfg.PUT
        otm_distance = otm_put_distance
        uncovered_shorts_result['pe_options'] = self.get_options(user_id,right,stock_code,lot_size,expiry_date,limits,elm,otm_distance,top, exchange_code=exchange_code)

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
                breeze = self.get_session_breeze(user_id)
                try:
                    options_chain = breeze.get_option_chain_quotes(stock_code=stock_code,exchange_code=exchange_code,product_type=product_type,expiry_date=expiry_date,right=right)
                except Exception as e:
                    options_chain = _icici_error(f"Error calling ICICI Breeze API get_option_chain_quotes(stock_code={stock_code},exchange_code={exchange_code},product_type={product_type},expiry_date={expiry_date},right={right}): {e}")

                # Identifying the best option to BUY inside the defined range
                options = []
                for i in options_chain.get('Success') or []:
                    if int(i["total_buy_qty"]) > 0 and ((right == cfg.CALL and int(i["strike_price"]) < int(range_lower) and int(i["strike_price"]) > float(i['spot_price'])) or (right == cfg.PUT and int(i["strike_price"]) > int(range_upper) and int(i["strike_price"]) < float(i['spot_price'])) ):
                        temp = {}
                        temp['stock_code'] = stock_code
                        temp['sell_leg'] = sell_leg
                        temp['buy_leg'] = {}
                        temp['buy_leg']['strike_price'] = int(i["strike_price"])
                        temp['buy_leg']['ltp'] = i['ltp']
                        temp['buy_leg']['best_bid_price'] = i['best_bid_price']
                        temp['buy_leg']['best_offer_price'] = i['best_offer_price']
                        temp['buy_leg']['total_buy_qty'] = i['total_buy_qty']
                        temp['buy_leg']['total_sell_qty'] = i['total_sell_qty']
                        temp['buy_leg']['spot_price'] = i['spot_price']
                        temp['buy_leg']['spot_distance'] = abs(float(i['spot_price']) - float(i["strike_price"])) / float(i["strike_price"])
                        temp['buy_leg']['buy_sell_ratio'] = int(i['total_buy_qty'])/int(i['total_sell_qty'])
                        temp['expiry_date'] = _expiry_api_to_display(expiry_date)
                        temp['right'] = right

                        temp['buy_leg']['quantity'] = math.floor(sell_leg['premium'] / temp['buy_leg']['best_offer_price'] / lot_size) * lot_size
                        if temp['buy_leg']['quantity'] <= int(i['total_sell_qty']):
                            temp['buy_leg']['best_offer_price'] = i["best_offer_price"]
                            if right == cfg.CALL:
                                temp['profit'] = temp['buy_leg']['quantity'] * (range_lower - int(i["strike_price"]))
                            else:
                                temp['profit'] = temp['buy_leg']['quantity'] * (int(i["strike_price"]) - range_upper)
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
                margin_situation["Status"] = cred_data.get("Status", 400)
                margin_situation["Error"] = cred_data.get("Error", "Could not fetch credentials")
                return margin_situation
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
                raw_session = _fetch_customerdetails_session_token(cred_data["Success"]["broker_api_key"], broker_token) if broker_token else ""
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
                margin_situation["Success"]["limits"] = margin_situation["Success"]["actual_margin_avl"] - margin_situation["Success"]["target_margin_free"]
                ist = ZoneInfo("Asia/Kolkata")
                margin_situation["Success"]["last_refresh"] = datetime.datetime.now(ist).strftime("%d-%b-%Y %H:%M:%S")
            else:
                margin_situation["Status"] = status if status is not None else 400
                margin_situation["Error"] = err_msg
                _logger.warning("get_margin_situation: API returned status=%s error=%r user_id=%s", status, err_msg, user_id)
        except Exception as e:
            _logger.warning("get_margin_situation: exception user_id=%s: %s", user_id, e, exc_info=True)
            margin_situation["Status"] = 400
            margin_situation["Error"] = "Unable to fetch margin information. Please try again or re-login."

        self._maybe_evict_session(user_id, margin_situation)
        return margin_situation

    def get_options(self,user_id,right,stock_code,lot_size,expiry_date,limits,elm,otm_distance,top, exchange_code: str = cfg.NFO):
        product_type = cfg.OPTIONS
        action = cfg.SELL
        sorted_options = {}
        breeze = self.get_session_breeze(user_id)
        try:
            options_chain = breeze.get_option_chain_quotes(stock_code=stock_code,exchange_code=exchange_code,product_type=product_type,expiry_date=expiry_date,right=right)
        except Exception as e:
            options_chain = _icici_error(f"Error calling ICICI Breeze API get_option_chain_quotes(stock_code={stock_code},exchange_code={exchange_code},product_type={product_type},expiry_date={expiry_date},right={right}): {e}")

        if options_chain.get('Status') == 200:
            # Calculating the premium that can be collected for every Liquid OTM CE option
            options = []
            for i in options_chain.get('Success') or []:
                if int(i["total_buy_qty"]) > 0 and ((right == cfg.CALL and int(i["strike_price"]) > int(float(i["spot_price"]) * (1 + otm_distance / 100))) or (right == cfg.PUT and int(i["strike_price"]) < int(float(i["spot_price"]) * (1 - otm_distance / 100))) ):
                    try:
                        option_margin = breeze.margin_calculator([{"strike_price": int(i["strike_price"]),"quantity": lot_size,"product": product_type,"action": action,"expiry_date": expiry_date,"stock_code": stock_code,"right": right}],exchange_code = exchange_code)
                    except Exception as e:
                        option_margin = _icici_error(f"Error calling ICICI Breeze API margin_calculator: {e}")

                    temp = {}
                    temp['stock_code'] = stock_code
                    temp['strike_price'] = int(i["strike_price"])
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
                        if temp['quantity'] <= int(i['total_buy_qty']):
                            temp['best_bid_price'] = i["best_bid_price"]
                            temp['premium'] = temp['quantity'] * i["best_bid_price"]
                            temp['carry_returns'] = (temp['premium']/(limits * 100000)) * (365/_days_to_expiry(expiry_date)) * 100
                            options.append(copy.deepcopy(temp))
                    else:
                        sorted_options['Error'] = "Error calling margin_calculator : " + option_margin.get('Error', '')
                        sorted_options['Status'] = option_margin.get('Status', 400)

            # Sort the top call options by premium
            options = sorted(options, key=lambda x: x["premium"], reverse=True)
            if len(options) > 0:
                sorted_options['Success'] = options[:top]
                sorted_options['Status'] = 200
            else:
                sorted_options['Error'] = "No suitable options found to SELL beyond the defined OTM distance"
                sorted_options['Status'] = 400
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
                    if int(option['total_sell_qty']) > 0:
                        option['buy_sell_ratio'] = int(option['total_buy_qty'])/int(option['total_sell_qty'])
                    else:
                        option['buy_sell_ratio'] = "NA"
                    option['spot_distance'] = abs(float(option['spot_price']) - option['strike_price']) / option['strike_price']
        
        return sorted_options

    def get_orders(self,user_id,start,end):
        """Fetch orders. ICICI API limits date range to 10 days; we chunk and merge when needed."""
        breeze = self.get_session_breeze(user_id)
        if breeze is None:
            return _icici_error("Unable to connect to broker. Please log out and log back in.")

        # ICICI API: max 10 days between from_date and to_date
        _MAX_DAYS = 10
        start_dt = datetime.datetime.strptime(start, "%Y-%m-%d")
        end_dt = datetime.datetime.strptime(end, "%Y-%m-%d")
        total_days = (end_dt - start_dt).days + 1
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

        return orders

    def group_orders(self,user_id,orders):
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

    def cancel_orders(self,user_id,orders):
        breeze = self.get_session_breeze(user_id)
        message = {}
        messages = []        
        for order in orders:
            order_id = order
            exchange_code = cfg.NFO
            if isinstance(order, str) and "|" in order:
                # Book UI passes: "<order_id>|<exchange_code>"
                order_id, exchange_code = order.split("|", 1)
            try:
                response = breeze.cancel_order(exchange_code=exchange_code,order_id=order_id)
            except Exception as e:
                response = _icici_error(f"Error calling ICICI Breeze API cancel_order(exchange_code={exchange_code},order_id={order_id}): {e}")

            self._maybe_evict_session(user_id, response)
            if response.get('Status') == 200:
                message['type'] = cfg.SUCCESS
                message['message'] = order + " : " + response['Success']['message']
                messages.append(message.copy())
            else:
                message['type'] = cfg.DANGER
                message['message'] = order + " : " + response.get('Error', '')
                messages.append(message.copy())
        
        return messages

    def get_positions(self, user_id):
        breeze = self.get_session_breeze(user_id)
        if breeze is None:
            _logger.warning("get_positions: no session for user_id=%s", user_id)
            return {"Status": 400, "Error": "Unable to connect to broker. Please log out and log back in.", "Success": None}
        full_secret, cred_data = self._get_full_secret_for_user(user_id)
        if full_secret is None:
            return {"Status": cred_data.get("Status", 400), "Error": cred_data.get("Error", "Could not fetch credentials"), "Success": None}
        # Use direct CustomerDetails API (like margin) - SDK's get_customer_details may return different structure
        broker_token = self.get_session_token(user_id) or ""
        session_token = _fetch_customerdetails_session_token(cred_data["Success"]["broker_api_key"], broker_token) if broker_token else ""
        if not session_token:
            return {"Status": 400, "Error": "CustomerDetails did not return session_token", "Success": None}
        try:
            positions = _call_icici_api_direct(
                "https://api.icicidirect.com/breezeapi/api/v1/portfoliopositions",
                {},
                cred_data["Success"]["broker_api_key"],
                full_secret,
                session_token,
                user_id=user_id,
                x_session_token=session_token,
            )
        except Exception as e:
            _logger.warning("get_positions: exception user_id=%s: %s", user_id, e, exc_info=True)
            positions = {"Status": 400, "Error": "Unable to fetch positions. Please try again or re-login.", "Success": None}

        self._maybe_evict_session(user_id, positions)
        if positions is not None:
            status, success_data, _ = _normalize_icici_response(positions)
            if status == 200 and success_data is not None:
                positions["Success"] = [
                    d for d in (success_data if isinstance(success_data, list) else [])
                    if (d.get("product_type") == cfg.OPTIONS and d.get("exchange_code") in (cfg.NFO, cfg.BFO))
                ]
                for i in positions["Success"]:
                    stock_code = i['stock_code']
                    exchange_code = i['exchange_code']
                    expiry_date = datetime.datetime.strptime(i['expiry_date'],"%d-%b-%Y").strftime("%Y-%m-%d")+"T06:00:00.000Z"
                    product_type = i['product_type']
                    right = i['right']
                    strike_price = i['strike_price']
                    try:
                        quote = breeze.get_quotes(stock_code,exchange_code,expiry_date,product_type,right,strike_price)
                    except Exception as e:
                        quote = _icici_error(f"Error calling ICICI Breeze API get_quotes({stock_code},{exchange_code},{expiry_date},{product_type},{right},{strike_price}): {e}")
                    if quote['Status'] == 200:
                        i['spot_price'] = quote['Success'][0]['spot_price']
                    else:
                        i['spot_price'] = "Err"

                    if i['product_type'] == cfg.OPTIONS:
                        i['option'] = i['stock_code']+"-"+i['expiry_date']+"-"+i['strike_price']+"-"+i['right']
                        if int(i['quantity']) == 0:
                            i['current_profit'] = 0
                            i['carry_profit'] = 0
                        else:
                            if i['action'] == cfg.SELL:
                                i['current_profit'] = (float(i['average_price']) - float(i['ltp'])) * int(i['quantity'])
                                i['carry_profit'] = float(i['ltp']) * int(i['quantity'])
                                margin_input = [{}]
                                margin_input[0]['strike_price'] = i['strike_price']
                                margin_input[0]['quantity'] = i['quantity']
                                margin_input[0]['right'] = i['right']
                                margin_input[0]['action'] = i['action']
                                margin_input[0]['product'] = i['product_type']
                                margin_input[0]['expiry_date'] = i['expiry_date']
                                margin_input[0]['stock_code'] = i['stock_code']
                                margin_input[0]['cover_order_flow'] = "N"
                                margin_input[0]['fresh_order_type'] = "N"
                                margin_input[0]['cover_limit_rate'] = "0"
                                margin_input[0]['cover_sltp_price'] = "0"
                                margin_input[0]['fresh_limit_rate'] = "0"
                                margin_input[0]['open_quantity'] = "0"
                                try:
                                    margins = breeze.margin_calculator(margin_input, exchange_code=exchange_code)
                                except Exception as e:
                                    margins = _icici_error(f"Error calling ICICI Breeze API margin_calculator: {e}")
                                if margins.get('Status') == 200:
                                    i['span_margin_required'] = float(margins['Success']['span_margin_required'])
                                    i['carry_margin_returns'] = (i['carry_profit']/i['span_margin_required']) * (365/_days_to_expiry(i['expiry_date'])) * 100
                                else:
                                    i['span_margin_required'] = None
                                    i['carry_margin_returns'] = None
                                
                                hedgeable_set = (cfg.HEDGEABLE_UNDERLYINGS or {}).get(i.get("exchange_code") or "", set())
                                if i.get("action") == cfg.SELL and i.get("stock_code") in hedgeable_set:
                                    i["hedgeable"] = True
                                else:
                                    i["hedgeable"] = False

                                # Extreme Loss Margin (ELM) calculations applicable for Index shorts only
                                if (i['stock_index_indicator'] == cfg.INDEX and i['action'] == cfg.SELL):
                                    if i['spot_price'] == "Err":
                                        i['elm_margin_required'] = None
                                    else:
                                        i['elm_margin_required'] = float(i['quantity']) * float(i['spot_price']) * cfg.ELM
                                else:
                                    i['elm_margin_required'] = None
                            else:
                                i['current_profit'] = (float(i['ltp']) - float(i['average_price'])) * int(i['quantity'])
                                i['carry_profit'] = - float(i['ltp']) * int(i['quantity'])
                                i['span_margin_required'] = None
                                i['elm_margin_required'] = None
                                i['carry_margin_returns'] = None
                                i['hedgeable'] = False
            else:
                err = positions.get("Error") or positions.get("error") or "No data"
                _logger.warning("get_positions: API returned status=%s error=%r user_id=%s", status, err, user_id)
        else:
            positions = {"Status": 400, "Error": "ICICI Breeze API get_portfolio_positions() returned NULL", "Success": None}

        return positions

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
        
        # Can't buy as hedge if there are no sellers!
        if int(option['total_sell_qty']) == 0:
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
                # collect option chain between the current spot and strike
                breeze = self.get_session_breeze(user_id)
                product_type = cfg.OPTIONS
                full_chain = breeze.get_option_chain_quotes(stock_code=stock_code,
                                    exchange_code=exchange_code,
                                    product_type=product_type,
                                    expiry_date=expiry_date,
                                    right=right)
                
                hedge_chain = []
                for option in full_chain['Success']:
                    strike_price = int(strike_price)
                    option['strike_price'] = int(option['strike_price'])
                    option['spot_price'] = float(option['spot_price'])

                    if self.is_valid_hedge(strike_price,right,option) == True:
                        option['distance_from_spot'] = abs(option['strike_price'] - option['spot_price'])
                        hedge_chain.append(option)

                # build the ATM premium ratio curve
                hedge_chain = sorted(hedge_chain, key=lambda x: x['distance_from_spot'], reverse=False)
                atm_premium  = hedge_chain[0]['best_offer_price']
                premium_curve = []
                for option in hedge_chain:
                    premium = {}
                    premium['distance_from_spot'] = option['distance_from_spot']
                    premium['premium_ratio'] = option['best_offer_price'] / atm_premium
                    premium_curve.append(premium)

                
                lot_size = self.fetch_lot_size(stock_code=stock_code,expiry_date=expiry_date, exchange_code=exchange_code)

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
                sorted_hedges['Success'] = hedge_chain[:top]
                sorted_hedges['Status'] = 200
                sorted_hedges['Error'] = ""
            except:
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

    def get_quote(self, user_id, stock_code, expiry_date, product_type, right, strike_price, exchange_code: str = cfg.NFO):
        breeze = self.get_session_breeze(user_id)
        try:
            quote = breeze.get_option_chain_quotes(stock_code, exchange_code, expiry_date, product_type, right, strike_price)
        except Exception as e:
            quote = {'Status': 400, 'Error': f"Error calling ICICI Breeze API get_option_chain_quotes: {e}"}
        if not isinstance(quote, dict):
            quote = {'Status': 400, 'Error': "Invalid response from get_option_chain_quotes"}
        if quote.get('Status') != 200:
            if quote.get('Error'):
                _logger.warning("get_quote failed: stock_code=%s error=%s", stock_code, quote['Error'])
            return quote
        try:
            if int(quote['Success'][0]['total_sell_qty']) > 0:
                quote['Success'][0]['buy_sell_ratio'] = int(quote['Success'][0]['total_buy_qty']) / int(quote['Success'][0]['total_sell_qty'])
            else:
                quote['Success'][0]['buy_sell_ratio'] = 0
        except (KeyError, IndexError, TypeError, ZeroDivisionError):
            pass
        return quote

    def get_full_option_chain(self, user_id: str, stock_code: str, exchange_code: str, expiry_date: str):
        """Fetch full CE + PE option chain for order page. Expiry can be YYYY-MM-DD or DD-Mon-YYYY.
        Returns dict with Status, Error, Success: { chain_rows, max_call_oi, max_put_oi, expiry_display, stock_code, exchange_code }.
        chain_rows = list of { strike_price, call?: row, put?: row }; each row has OI, LTP, total_buy_qty, total_sell_qty, buy_sell_ratio, lot_size, best_bid_price, best_offer_price, etc.
        """
        # Normalize expiry: API format for Breeze, display for template/fetch_lot_size
        expiry_display = expiry_date
        if expiry_date and len(expiry_date) == 10 and expiry_date[4] == "-":  # YYYY-MM-DD
            try:
                expiry_display = datetime.datetime.strptime(expiry_date, "%Y-%m-%d").strftime("%d-%b-%Y")
            except ValueError:
                pass
        expiry_api = _expiry_display_to_api(expiry_display)

        breeze = self.get_session_breeze(user_id)
        product_type = cfg.OPTIONS

        def fetch_side(right: str):
            try:
                r = breeze.get_option_chain_quotes(
                    stock_code=stock_code,
                    exchange_code=exchange_code,
                    product_type=product_type,
                    expiry_date=expiry_api,
                    right=right,
                )
            except Exception as e:
                return _icici_error(f"get_option_chain_quotes({right}): {e}")
            if not isinstance(r, dict) or r.get("Status") != 200:
                return r
            rows = []
            for i in (r.get("Success") or []):
                try:
                    total_buy = int(i.get("total_buy_qty") or 0)
                    total_sell = int(i.get("total_sell_qty") or 0)
                    if total_sell > 0:
                        ratio = total_buy / total_sell
                    else:
                        ratio = 0.0 if total_buy == 0 else None  # None = "NA"
                    oi = i.get("open_interest")
                    try:
                        oi_val = int(oi) if oi is not None else 0
                    except (TypeError, ValueError):
                        oi_val = 0
                    row = {
                        "stock_code": stock_code,
                        "strike_price": int(float(i.get("strike_price", 0))),
                        "right": right,
                        "expiry_date": expiry_display,
                        "ltp": i.get("ltp"),
                        "open_interest": oi_val,
                        "total_buy_qty": total_buy,
                        "total_sell_qty": total_sell,
                        "buy_sell_ratio": ratio if ratio is not None else "NA",
                        "best_bid_price": i.get("best_bid_price"),
                        "best_offer_price": i.get("best_offer_price"),
                        "spot_price": i.get("spot_price"),
                    }
                    lot_size = self.fetch_lot_size(stock_code, expiry_display, exchange_code=exchange_code)
                    row["lot_size"] = lot_size if lot_size is not None else 0
                    rows.append(row)
                except (KeyError, TypeError, ValueError):
                    continue
            return {"Status": 200, "Success": rows, "Error": None}

        ce_res = fetch_side(cfg.CALL)
        pe_res = fetch_side(cfg.PUT)
        if ce_res.get("Status") != 200:
            return ce_res
        if pe_res.get("Status") != 200:
            return pe_res

        calls = ce_res.get("Success") or []
        puts = pe_res.get("Success") or []

        strikes = sorted(set(r["strike_price"] for r in calls) | set(r["strike_price"] for r in puts))
        call_by_strike = {r["strike_price"]: r for r in calls}
        put_by_strike = {r["strike_price"]: r for r in puts}

        max_call_oi = max((r["open_interest"] for r in calls), default=0)
        max_put_oi = max((r["open_interest"] for r in puts), default=0)

        chain_rows = []
        for k in strikes:
            chain_rows.append({
                "strike_price": k,
                "call": call_by_strike.get(k),
                "put": put_by_strike.get(k),
            })

        def _is_illiquid(row):
            c, p = row.get("call"), row.get("put")
            c_zero = c is None or (c.get("total_buy_qty", 0) == 0 and c.get("total_sell_qty", 0) == 0)
            p_zero = p is None or (p.get("total_buy_qty", 0) == 0 and p.get("total_sell_qty", 0) == 0)
            return c_zero and p_zero

        chain_rows = [r for r in chain_rows if not _is_illiquid(r)]

        # Spot and ATM from option chain response (no separate get_quote)
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
        if spot_price is not None and strikes:
            atm_strike = min(strikes, key=lambda s: abs(s - spot_price))

        return {
            "Status": 200,
            "Error": None,
            "Success": {
                "chain_rows": chain_rows,
                "max_call_oi": max_call_oi,
                "max_put_oi": max_put_oi,
                "expiry_display": expiry_display,
                "stock_code": stock_code,
                "exchange_code": exchange_code,
                "spot_price": spot_price,
                "atm_strike": atm_strike,
            },
        }

    def place_order(self,user_id,product_type,stock_code,action,strike_price,right,price,expiry_date,quantity, exchange_code: str = cfg.NFO):
        breeze = self.get_session_breeze(user_id)
        expiry_api = _expiry_display_to_api(expiry_date)
        try:
            response = breeze.place_order(
                stock_code=stock_code,
                action=action,
                strike_price=strike_price,
                right=right,
                price=price,
                expiry_date=expiry_api,
                validity="day",
                order_type=cfg.LIMIT,
                quantity=quantity,
                validity_date=str(datetime.date.today())+"T06:00:00.000Z",
                stoploss="",
                disclosed_quantity="0",
                exchange_code=exchange_code,
                product=product_type,
            )
        except Exception as e:
            response = _icici_error(f"Error calling ICICI Breeze API place_order: {e}")

        self._maybe_evict_session(user_id, response)
        return response

    def break_order(self,user_id,stock_code,expiry_date,product_type,right,strike_price,total_qty,price,action, exchange_code: str = cfg.NFO):
        message = {}
        messages = []

        qty_limits = self.fetch_qty_limits(stock_code, exchange_code=exchange_code)
        if qty_limits is None:
            message['type'] = cfg.DANGER
            message['message'] = "No entries for stock code " + stock_code + " in the Options Quantity Limits file"
            messages.append(message.copy())
        else:
            lot_size = self.fetch_lot_size(stock_code, expiry_date, exchange_code=exchange_code)
            qty_per_order = (max(1, int(qty_limits)) // lot_size) * lot_size  # avoid ZeroDivisionError when qty_limits is 1
            iterations = int(int(total_qty) / qty_per_order)
            remainder = int(total_qty) % int(qty_per_order)

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
                )
                iterations -=1
                if response and response.get('Status') == 200:
                    message['type'] = cfg.SUCCESS
                    message['message'] = stock_code + "-" + expiry_date + "-" + str(strike_price) + "-" + right + " | Qty = " + str(qty_per_order) + " | Price = " + str(price) + " >> " + (response.get('Success') or {}).get('message', '') + " : " + (response.get('Success') or {}).get('order_id', '')
                    messages.append(message.copy())
                else:
                    message['type'] = cfg.DANGER
                    err = (response or {}).get('Error') or 'Unknown error'
                    message['message'] = err + stock_code + "-" + expiry_date + "-" + str(strike_price) + "-" + right + " | Qty = " + str(qty_per_order) + " | Price = " + str(price)
                    messages.append(message.copy())

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
                )
                if response and response.get('Status') == 200:
                    message['type'] = cfg.SUCCESS
                    succ = response.get('Success') or {}
                    message['message'] = stock_code + "-" + expiry_date + "-" + str(strike_price) + "-" + right + " | Qty = " + str(remainder) + " | Price = " + str(price) + " >> " + succ.get('message', '') + " : " + succ.get('order_id', '')
                    messages.append(message.copy())
                else:
                    message['type'] = cfg.DANGER
                    err = (response or {}).get('Error') or 'Unknown error'
                    message['message'] = err + stock_code + "-" + expiry_date + "-" + str(strike_price) + "-" + right + " | Qty = " + str(remainder) + " | Price = " + str(price)
                    messages.append(message.copy())

        return messages

    def get_ICICImaster_date(self):
        # Get the creation time of the file
        result = {}
        try:
            creation_timestamp = os.path.getmtime(cfg.DATA_PATH+cfg.SCRIP_DB)
            creation_date = datetime.datetime.fromtimestamp(creation_timestamp)
            creation = {}
            creation['date'] = creation_date.strftime("%d-%b-%Y")
            today = datetime.datetime.today()
            creation['age'] = (today - creation_date).days
            
            result['Status'] = 200
            result['Error'] = None
            result['Success'] = creation
        except:
            result['Status'] = 400
            result['Error'] = "Error getting creating timestamp of master data file at : "+cfg.DATA_PATH+cfg.SCRIP_MASTER
            result['Success'] = None

        return result

    def update_ICICImaster(self):
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

            # Load quantity limits for both exchanges before inserting scrip rows.
            # (Quantity limits files are expected under cfg.DATA_PATH.)
            self.load_qty_limits([
                (os.path.join(cfg.DATA_PATH, cfg.LIMITS_MASTER_NSE), cfg.NFO),
                (os.path.join(cfg.DATA_PATH, cfg.LIMITS_MASTER_BSE), cfg.BFO),
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
        """(Re)load quantity limits into raw_limits_data for both NSE and BSE.
        limits_specs: list of (file_path, segment_code) e.g. (path_to_NSEFreezeLimits.txt, NFO).
        """
        conn = _scrip_master_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("DROP TABLE IF EXISTS raw_limits_data")
            cursor.execute('''
            CREATE TABLE raw_limits_data (
                InstrumentName TEXT,
                ShortName TEXT,
                ExchangeCode TEXT,
                SegmentCode TEXT,
                QtyLimit INTEGER,
                PRIMARY KEY (ShortName, ExchangeCode, SegmentCode)
            )
            ''')

            rows = []
            for limits_path, segment_code in limits_specs:
                if not limits_path or not os.path.exists(limits_path):
                    _logger.warning("Quantity limits file not found: %s", limits_path)
                    continue
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
                raise FileNotFoundError("No quantity limits rows loaded from NSEFreezeLimits/BSEFreezeLimits.")

            cursor.executemany(
                'INSERT OR REPLACE INTO raw_limits_data (InstrumentName, ShortName, ExchangeCode, SegmentCode, QtyLimit) VALUES (?,?,?,?,?)',
                rows,
            )
            conn.commit()
            _logger.info("Loaded quantity limits rows into raw_limits_data (%d rows)", len(rows))
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
        # Get today's date
        today = datetime.datetime.today()
        
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
        period = (datetime.datetime.today() - datetime.datetime.strptime(start,"%Y-%m-%d")).days
        product_type = cfg.OPTIONS

        try:
            all_trades = []
            first_error = None
            for exchange_code in (cfg.NFO, cfg.BFO):
                trades_resp = breeze.get_trade_list(
                    from_date=start_date,
                    to_date=end_date,
                    exchange_code=exchange_code,
                    product_type=product_type,
                    action="",
                    stock_code="",
                )
                self._maybe_evict_session(user_id, trades_resp)

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

            for trade in trades['Success']:
                trade_date = trade['trade_date']
                trade_date = datetime.datetime.strptime(trade_date, "%d-%b-%Y")
                trade_month_name = trade_date.strftime("%b-%y")
                premium = float(trade['quantity']) * float(trade['average_cost'])
                if trade['action'] == cfg.SELL:
                    performance['Success']['premium_earned'] += premium
                    performance['Success']['net_pnl'] += premium
                    per_month[trade_month_name]['pnl'] += premium

                if trade['action'] == cfg.BUY:
                    performance['Success']['premium_paid'] += premium
                    performance['Success']['net_pnl'] -= premium
                    per_month[trade_month_name]['pnl'] -= premium

                performance['Success']['brokerage'] += float(trade['brokerage_amount'])
                performance['Success']['taxes'] += float(trade['total_taxes'])
                performance['Success']['net_pnl'] = performance['Success']['net_pnl'] - float(trade['brokerage_amount']) - float(trade['total_taxes'])
                per_month[trade_month_name]['brokerage'] += float(trade['brokerage_amount'])
                per_month[trade_month_name]['taxes'] += float(trade['total_taxes'])
                per_month[trade_month_name]['pnl'] = per_month[trade_month_name]['pnl'] - float(trade['brokerage_amount']) - float(trade['total_taxes'])
            
            performance['Success']['annualised_roi'] = (performance['Success']['net_pnl'] / margin) * (365 / period)
            monthly = [{"month": month, "pnl": values["pnl"], "brokerage": values["brokerage"], "taxes": values["taxes"]}for month, values in per_month.items()]
            monthly.sort(key=lambda x: datetime.datetime.strptime(x["month"], "%b-%y"))
            performance['Success']['monthly'] = monthly

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

    def fetch_lot_size(self, stock_code, expiry_date, exchange_code: str = cfg.NFO):
        # Fetches the lot size for the provided stock_code from the scrip_master table.
        with _scrip_master_connection() as conn:
            if exchange_code == cfg.NFO:
                cursor = conn.execute(
                    "SELECT LotSize FROM scrip_master WHERE ShortName = ? AND ExpiryDate = ? AND (SegmentCode = ? OR SegmentCode IS NULL) LIMIT 1",
                    (stock_code, expiry_date, exchange_code),
                )
            else:
                cursor = conn.execute(
                    "SELECT LotSize FROM scrip_master WHERE ShortName = ? AND ExpiryDate = ? AND SegmentCode = ? LIMIT 1",
                    (stock_code, expiry_date, exchange_code),
                )
            row = cursor.fetchone()
        return row[0] if row else None

    def fetch_stock_codes(self, exchange_code: str = cfg.NFO):
        # Fetches all unique combinations of ShortName and CompanyName (long_name) where Series is 'OPTION', and returns a list of dictionaries with all expiry dates for each combination.
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
                SegmentCode TEXT
            )
            ''')
            _logger.debug("Created scrip_master table")

            # Backward compatible schema upgrade for existing DBs.
            try:
                cursor.execute("ALTER TABLE scrip_master ADD COLUMN SegmentCode TEXT")
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

            # Insert filtered and joined data into scrip_master (limits from persistent raw_limits_data)
            cursor.execute('''
            INSERT INTO scrip_master (ShortName, ExpiryDate, StrikePrice, OptionType, LotSize, QuantityLimit, CompanyName, ExchangeCode, SegmentCode)
            SELECT 
                scrips.ShortName, 
                scrips.ExpiryDate, 
                scrips.StrikePrice, 
                scrips.OptionType, 
                scrips.LotSize, 
                limits.QtyLimit AS QuantityLimit, 
                scrips.CompanyName, 
                scrips.ExchangeCode,
                limits.SegmentCode
            FROM raw_scrip_data scrips
            JOIN raw_limits_data limits
              ON scrips.ShortName = limits.ShortName
             AND scrips.ExchangeCode = limits.ExchangeCode
             AND limits.SegmentCode = ?
            WHERE scrips.Series = "OPTION"
            ''', (exchange_code,))
            _logger.info("Inserted filtered data into scrip_master")

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
                today = datetime.date.today()
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
            start = kwargs.get('start_date') or (datetime.date.today() - datetime.timedelta(days=90)).strftime("%Y-%m-%d")
            end = kwargs.get('end_date') or datetime.date.today().strftime("%Y-%m-%d")
            return self.get_orders(user_id, start, end)
        return {'Status': 400, 'Error': 'broker_token or user_id required for real-time orders'}

# module-level helpers for ease of use

def get_portfolio_realtime(*args, **kwargs):
    """Module-level proxy to Processor.get_portfolio_realtime."""
    return processor().get_portfolio_realtime(*args, **kwargs)


def get_orders_realtime(*args, **kwargs):
    """Module-level proxy to Processor.get_orders_realtime."""
    return processor().get_orders_realtime(*args, **kwargs)


