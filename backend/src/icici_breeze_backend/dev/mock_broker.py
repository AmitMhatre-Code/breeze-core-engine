"""Duck-typed stand-in for BreezeConnect when ICICI_BROKER_MODE=mock."""

from __future__ import annotations

import datetime
import logging
import re
import threading

from icici_breeze_backend.dev.fixtures import responses as fx
from icici_breeze_backend.dev.mock_market_data import (
    enrich_contract_fields,
    resolve_underlying_spot,
    seed_running_state,
    step_live_tick_fields,
)

_logger = logging.getLogger(__name__)

_VIX_LIKE_SYMBOLS = {"INDVIX", "INDIA VIX", "VIX"}


def _bhavcopy_option_chain_rows(
    stock_code: str, expiry_date: str, exchange_code: str, right: str
) -> list[dict]:
    """Real strikes/premiums/OI for this contract from the local bhavcopy store, if any.

    Empty list (not an exception) when there's no bhavcopy coverage for this
    stock/expiry -- e.g. an expiry the bhavcopy snapshot doesn't include --
    so callers can fall through to a synthetic response.
    """
    try:
        from icici_breeze_backend.app.services.reference_data.bhavcopy_store import (
            build_chain_from_bhavcopy,
        )
        from icici_breeze_backend.app.services.reference_data.tradable_contracts import (
            list_tradeable_strikes,
        )

        strikes = list_tradeable_strikes(stock_code, expiry_date, exchange_code=exchange_code)
        if not strikes:
            return []
        payload = build_chain_from_bhavcopy(stock_code, expiry_date, exchange_code, strikes=strikes)
        if not payload:
            return []
        side = "put" if (right or "").lower().startswith("p") else "call"
        rows = []
        for row in payload.get("chain_rows") or []:
            cell = row.get(side)
            if not cell:
                continue
            strike_val = row.get("strike_price")
            spot_val = cell.get("spot_price")
            rows.append(
                {
                    "strike_price": str(int(strike_val)) if float(strike_val).is_integer() else str(strike_val),
                    "ltp": f"{float(cell.get('ltp') or 0):.2f}",
                    "best_bid_price": f"{float(cell.get('best_bid_price') or 0):.2f}",
                    "best_offer_price": f"{float(cell.get('best_offer_price') or 0):.2f}",
                    "total_buy_qty": str(cell.get("total_buy_qty") or 0),
                    "total_sell_qty": str(cell.get("total_sell_qty") or 0),
                    "open_interest": str(cell.get("open_interest") or 0),
                    "spot_price": f"{float(spot_val):.2f}" if spot_val else "",
                    "right": right or "Call",
                }
            )
        return rows
    except Exception:
        _logger.debug("bhavcopy option chain lookup failed for %s %s", stock_code, expiry_date, exc_info=True)
        return []


def _mock_option_chain_rows(stock_code: str, expiry_date: str, right: str):
    """Purely synthetic fallback when bhavcopy has no data for this contract."""
    spot = resolve_underlying_spot(stock_code) or 25000.0
    strikes = [round(spot * f / 100) for f in (98, 99, 100, 101, 102, 103)]
    rows = []
    for k in strikes:
        base = 45.0 + abs(k - spot) * 0.12
        if (right or "").lower() == "put":
            base += 8.0
        rows.append(
            {
                "strike_price": str(k),
                "ltp": f"{base:.2f}",
                "best_bid_price": f"{base - 0.5:.2f}",
                "best_offer_price": f"{base + 0.5:.2f}",
                "total_buy_qty": "800",
                "total_sell_qty": "750",
                "open_interest": str(120_000 + k % 10000),
                "spot_price": f"{spot:.2f}",
                "right": right or "Call",
            }
        )
    return rows


def _mock_hist_v2_rows(from_date: str, to_date: str) -> list[dict]:
    """Daily closes between from/to (ISO-ish), for INDVIX-style history."""
    def _parse(d: str) -> datetime.date | None:
        if not d:
            return None
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(d).replace("Z", ""))
        if not m:
            return None
        return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))

    start = _parse(from_date) or (datetime.date.today() - datetime.timedelta(days=30))
    end = _parse(to_date) or datetime.date.today()
    if start > end:
        start, end = end, start
    out = []
    cur = start
    v = 15.2
    while cur <= end:
        v = round(v + (hash(cur.isoformat()) % 100) / 200.0 - 0.2, 2)
        v = max(10.0, min(28.0, v))
        dt = f"{cur.isoformat()}T00:00:00.000Z"
        out.append({"datetime": dt, "close": str(v), "open": str(v - 0.1), "high": str(v + 0.3), "low": str(v - 0.25)})
        cur += datetime.timedelta(days=1)
    return out


class MockBreezeSdk:
    """Implements SDK methods invoked by processor paths used in typical UI flows."""

    user_id: str | None = None

    def __init__(self) -> None:
        self.on_ticks = None
        self.on_ticks2 = None  # matches real SDK's attribute name; unused here
        self._ws_tokens: set[str] = set()
        self._ws_stop: threading.Event | None = None
        self._ws_thread: threading.Thread | None = None
        self._gtt_orders: dict[str, dict] = {}
        self._gtt_seq = 0

    def generate_session(self, **kwargs):
        return None

    def ws_connect(self, **kwargs):
        """Start a background thread streaming random-walk ticks for subscribed tokens.

        Mirrors the real SDK's `on_ticks` callback contract exactly (see
        `breeze_websocket_manager._attach_sdk_ticks_handler`), so the rest of the
        chain-quote pipeline (ws_tick_pipeline, the chain_builder worker,
        canonical chain assembly, chain_readiness) runs completely unmodified
        against this mock data -- only the tick *source* is fake.

        Ticks carry only `symbol` + price/quote fields, not contract identity
        (stock_code/expiry_date/strike/right): identity resolution goes through
        `ws_token_index.lookup_contract_by_ws_symbol()` against the real local
        scrip-master DB, which already works for real tokens regardless of
        broker mode.
        """
        if self._ws_thread is not None and self._ws_thread.is_alive():
            return
        self._ws_stop = threading.Event()
        self._ws_thread = threading.Thread(target=self._ws_tick_loop, daemon=True)
        self._ws_thread.start()

    def ws_disconnect(self, **kwargs):
        if self._ws_stop is not None:
            self._ws_stop.set()
        self._ws_tokens.clear()
        return [{"message": "socket server for rate refresh  has been disconnected."}]

    @staticmethod
    def _normalize_tokens(stock_token) -> list[str]:
        if isinstance(stock_token, list):
            return [str(t) for t in stock_token if t]
        return [str(stock_token)] if stock_token else []

    def subscribe_feeds(self, stock_token: str | list[str] = "", **kwargs):
        if kwargs.get("get_order_notification"):
            # Real ICICI opens a separate socket.io channel that still dispatches through
            # on_ticks. Nothing to poll here -- events are injected via
            # emit_order_notification() below.
            self._order_notifications_on = True
            return {"message": "Order notification subscribed successfully"}
        self._ws_tokens.update(self._normalize_tokens(stock_token))
        return {"message": f"Stock {stock_token} subscribed successfully"}

    def emit_order_notification(self, **overrides) -> dict:
        """Push a synthetic order-notification tick through the real `on_ticks` path.

        Exists because live-mode broker calls only work from the production static IP, so
        the Strategy Group lifecycle (Fired -> Completed/Reset, manual-intervention
        detection) cannot otherwise be exercised locally at all. The payload mirrors a real
        capture (docs/Temp/order_placed), including the quirks the parser has to survive:
        prices scaled x100, and `averageExecutedRate` carrying junk while nothing has
        filled.
        """
        payload = {
            "userId": getattr(self, "_user_id", "MOCKUSER"),
            "messageType": "6",
            "messageSequence": str(int(time.time() * 1000)),
            "orderExchangeCode": "NFO",
            "stockCode": "NIFTY",
            "productType": "Options",
            "optionType": "Call",
            "strikePrice": "2600000",  # x100 -> 26000
            "expiryDate": "21-Jul-2026",
            "orderFlow": "Sell",
            "limitMarketFlag": "Limit",
            "orderType": "Day",
            "limitRate": "300",  # x100 -> Rs 3.00
            "orderStatus": "Ordered",
            "orderReference": "MOCK-ORDER-1",
            "orderTotalQuantity": "130",
            "executedQuantity": "0",
            "cancelledQuantity": "0",
            "expiredQuantity": "0",
            "averageExecutedRate": "1550900.000000",  # garbage, as in the real capture
            "channel": "WEB",
            **overrides,
        }
        if self.on_ticks:
            self.on_ticks(payload)
        return payload

    def unsubscribe_feeds(self, stock_token: str | list[str] = "", **kwargs):
        for token in self._normalize_tokens(stock_token):
            self._ws_tokens.discard(token)
        return {"message": f"Stock {stock_token} unsubscribed successfully"}

    def _ws_tick_loop(self) -> None:
        running_state: dict[str, dict] = {}
        stop = self._ws_stop
        while stop is not None and not stop.is_set():
            for token in list(self._ws_tokens):
                state = running_state.setdefault(token, seed_running_state(token))
                tick = step_live_tick_fields(token, state)
                tick.update(enrich_contract_fields(token))
                if self.on_ticks is not None:
                    try:
                        self.on_ticks(tick)
                    except Exception:
                        _logger.exception("mock_ws_on_ticks_handler_failed token=%s", token)
            stop.wait(0.5)

    def get_customer_details(self, *args, **kwargs):
        return {
            "Status": 200,
            "Success": {
                "id": "MOCKUSER",
                "exg_trade_date": "04-Apr-2026",
                "session_token": fx.MOCK_CUSTOMER_DETAILS_SESSION_TOKEN,
                "email": "mock@example.com",
            },
            "Error": None,
        }

    def get_portfolio_positions(self, **kwargs):
        return {
            "Status": 200,
            "Success": fx.mock_portfolio_position_rows(),
            "Error": None,
        }

    def get_margin(self, exchange_code: str | None = None, **kwargs):
        return dict(fx.MARGIN_DIRECT_RESPONSE)

    def get_funds(self, **kwargs):
        return {
            "Status": 200,
            "Success": {
                "bank_account": "****1234",
                "total_bank_balance": 2_500_000.0,
                "allocated_equity": 1_500_000.0,
                "unallocated_balance": 875_432.50,
            },
            "Error": None,
        }

    def get_order_list(
        self,
        exchange_code: str = "",
        from_date: str = "",
        to_date: str = "",
        **kwargs,
    ):
        rows = [dict(o) for o in fx.mock_order_list_rows() if (o.get("exchange_code") or "NFO") == (exchange_code or "NFO")]
        return {"Status": 200, "Success": rows, "Error": None}

    def get_trade_list(self, **kwargs):
        ex = (kwargs.get("exchange_code") or "").strip()
        rows = [dict(t) for t in fx.MOCK_TRADE_LIST_SAMPLE if not ex or t.get("exchange_code") == ex]
        return {"Status": 200, "Success": rows, "Error": None}

    def cancel_order(self, exchange_code: str = "", order_id: str = "", **kwargs):
        return {"Status": 200, "Success": True, "Error": None}

    def place_order(self, **kwargs):
        return {
            "Status": 200,
            "Success": {
                "order_id": "MOCK-ORDER-1",
                "message": "Mock broker: order not sent to ICICI.",
            },
            "Error": None,
        }

    def get_quotes(
        self,
        stock_code: str = "",
        exchange_code: str = "",
        expiry_date: str = "",
        product_type: str = "",
        right: str = "",
        strike_price: str = "",
        **kwargs,
    ):
        sc = (kwargs.get("stock_code") or stock_code or "").strip()
        ex = (kwargs.get("exchange_code") or exchange_code or "").strip()
        pt = (kwargs.get("product_type") or product_type or "").strip()
        if ex.upper() == "NSE" and pt.lower() == "cash":
            spot = resolve_underlying_spot(sc) if sc else None
            if spot is None:
                # No F&O bhavcopy row exists for this symbol at all (e.g. INDVIX
                # isn't itself F&O-traded): a stable, plausible synthetic value
                # instead of one canned constant shared by every stock_code.
                spot = 15.85 if (not sc or sc.upper() in _VIX_LIKE_SYMBOLS) else seed_running_state(sc)["last"]
            jitter = max(0.05, spot * 0.003)
            return {
                "Status": 200,
                "Success": [
                    {
                        "ltp": f"{spot:.2f}",
                        "previous_close": f"{max(0.01, spot - jitter):.2f}",
                        "open": f"{max(0.01, spot - jitter * 0.6):.2f}",
                        "high": f"{spot + jitter:.2f}",
                        "low": f"{max(0.01, spot - jitter):.2f}",
                        "stock_code": sc or "INDVIX",
                    }
                ],
                "Error": None,
            }
        rows = _bhavcopy_option_chain_rows(sc, expiry_date, ex or "NFO", right)
        row = next((r for r in rows if not strike_price or r["strike_price"] == str(strike_price)), None)
        if row:
            return {"Status": 200, "Success": [row], "Error": None}
        return {
            "Status": 200,
            "Success": [
                {
                    "ltp": "118.25",
                    "best_bid_price": "117.50",
                    "best_offer_price": "119.00",
                    "total_buy_qty": "1000",
                    "total_sell_qty": "1000",
                    "spot_price": "25000",
                }
            ],
            "Error": None,
        }

    def get_option_chain_quotes(self, **kwargs):
        strike = str(kwargs.get("strike_price") or "")
        stock_code = str(kwargs.get("stock_code") or "NIFTY")
        exchange_code = str(kwargs.get("exchange_code") or "NFO")
        expiry_date = str(kwargs.get("expiry_date") or "")
        right = str(kwargs.get("right") or "Call")

        rows = _bhavcopy_option_chain_rows(stock_code, expiry_date, exchange_code, right)
        if not rows:
            rows = _mock_option_chain_rows(stock_code, expiry_date, right)
        if strike and strike not in ("0", "None"):
            match = next((r for r in rows if r["strike_price"] == strike), None)
            rows = [match] if match else rows[:1]
        return {"Status": 200, "Success": rows, "Error": None}

    def get_historical_data_v2(self, **kwargs):
        from_date = str(kwargs.get("from_date") or "")
        to_date = str(kwargs.get("to_date") or "")
        succ = _mock_hist_v2_rows(from_date, to_date)
        return {"Status": 200, "Success": succ, "Error": None}

    def margin_calculator(self, margin_list, exchange_code: str = "", **kwargs):
        # Deterministic stand-in that actually reacts to the leg set, so netting is
        # observable in mock mode: a multi-leg call returns LESS than the sum of its
        # legs priced alone (opposing CE/PE offset; long legs hedge). Not a real SPAN
        # model -- just enough structure to exercise the group/portfolio netting path.
        legs = margin_list or []

        def _qty(leg):
            try:
                return abs(float(leg.get("quantity", 0) or 0))
            except (TypeError, ValueError):
                return 0.0

        def _is(leg, prefix):
            return str(leg.get("action", "")).strip().lower().startswith(prefix)

        def _notional_per_share(leg):
            """SPAN scales with the underlying's value, so a flat per-share figure cannot
            serve both a 75-share NIFTY lot and a 14,100-share SAIL lot -- the latter came
            out at tens of lakhs per lot. Price off real spot where the bhavcopy has it,
            else the strike, which is close enough for the OTM strikes bots actually write."""
            spot = resolve_underlying_spot(str(leg.get("stock_code") or ""))
            if spot and spot > 0:
                return float(spot)
            try:
                return abs(float(leg.get("strike_price", 0) or 0))
            except (TypeError, ValueError):
                return 0.0

        # ~8% of notional: lands near real SPAN for both an index lot and a stock lot.
        span_rate = 0.08
        sells = [l for l in legs if _is(l, "s")]
        buys = [l for l in legs if _is(l, "b")]
        naked = sum(_qty(l) * _notional_per_share(l) * span_rate for l in sells)
        rights = {str(l.get("right", "")).strip().lower()[:1] for l in sells}
        net_factor = 0.65 if {"c", "p"} <= rights else 1.0  # strangle/condor offset
        hedge = sum(_qty(l) * _notional_per_share(l) * span_rate * 0.6 for l in buys)
        span = max(0.0, naked * net_factor - hedge)
        return {
            "Status": 200,
            "Success": {
                "total_margin": round(span * 1.07, 2),
                "span_margin_required": round(span, 2),
            },
            "Error": None,
        }

    def get_order_detail(self, exchange_code: str = "", order_id: str = "", **kwargs):
        order_rows = fx.mock_order_list_rows()
        rows = [dict(o) for o in order_rows if str(o.get("order_id")) == str(order_id)]
        if not rows and order_rows:
            rows = [dict(order_rows[0])]
        return {"Status": 200, "Success": rows, "Error": None}

    def get_demat_holdings(self, **kwargs):
        """Faithful to ICICI: pledged quantities come back as 0. Not a holdings source --
        see `fx.mock_demat_holding_rows` and use `get_portfolio_holdings` instead."""
        return {"Status": 200, "Success": fx.mock_demat_holding_rows(), "Error": None}

    def get_portfolio_holdings(self, **kwargs):
        return {
            "Status": 200,
            "Success": fx.mock_portfolio_holding_rows(
                exchange_code=kwargs.get("exchange_code") or "",
                stock_code=kwargs.get("stock_code") or "",
            ),
            "Error": None,
        }

    def modify_order(self, **kwargs):
        return {
            "Status": 200,
            "Success": {"message": "Mock: order modified", "order_id": kwargs.get("order_id", "MOCK")},
            "Error": None,
        }

    def square_off(self, **kwargs):
        return {
            "Status": 200,
            "Success": {"order_id": "MOCK-SQ-1", "message": "Mock broker: square off not sent to ICICI."},
            "Error": None,
        }

    def set_funds(self, **kwargs):
        return {"Status": 200, "Success": {"status": "Success"}, "Error": None}

    def get_historical_data(self, **kwargs):
        return self.get_historical_data_v2(**kwargs)

    def get_names(self, exchange_code: str = "", stock_code: str = "", **kwargs):
        return {
            "exchange_code": exchange_code or "NSE",
            "exchange_stock_code": stock_code or "NIFTY",
            "isec_stock_code": "MOCK",
            "isec_token": "3499",
            "company name": "Mock Company",
            "isec_token_level1": "4.1!3499",
        }

    def preview_order(self, **kwargs):
        return {
            "Status": 200,
            "Success": {"total_brokerage": 0.89, "brokerage": 0.31},
            "Error": None,
        }

    def limit_calculator(self, **kwargs):
        return {
            "Status": 200,
            "Success": {"limit_rate": "16", "order_margin": "0"},
            "Error": None,
        }

    def gtt_three_leg_place_order(self, **kwargs):
        """Tracks placed GTT orders in-memory (keyed on the mock session) so `gtt_order_book`
        reflects what was actually placed/cancelled during a mock-mode dev session, letting the
        per-leg Exit Rule modal's "existing GTT status" display be exercised locally."""
        self._gtt_seq += 1
        gtt_order_id = f"MOCK-GTT-{self._gtt_seq}"
        order_details = [
            {**leg, "gtt_order_id": gtt_order_id, "status": "Pending"}
            for leg in kwargs.get("order_details") or []
        ]
        self._gtt_orders[gtt_order_id] = {
            "exchange_code": kwargs.get("exchange_code"),
            "product_type": kwargs.get("product"),
            "stock_code": kwargs.get("stock_code"),
            "expiry_date": kwargs.get("expiry_date"),
            "strike_price": kwargs.get("strike_price"),
            "right": kwargs.get("right"),
            "quantity": kwargs.get("quantity"),
            "index_or_stock": kwargs.get("index_or_stock"),
            "gtt_type": kwargs.get("gtt_type"),
            "fresh_order_id": None,
            "order_datetime": datetime.datetime.now().strftime("%d-%b-%Y %H:%M:%S"),
            "order_details": order_details,
        }
        return {
            "Status": 200,
            "Success": {"gtt_order_id": gtt_order_id, "message": "Mock GTT Order Request Placed Successfully"},
            "Error": None,
        }

    def gtt_three_leg_modify_order(self, **kwargs):
        gtt_order_id = kwargs.get("gtt_order_id", "MOCK-GTT-1")
        record = self._gtt_orders.get(gtt_order_id)
        if record is not None:
            record["order_details"] = [
                {**leg, "gtt_order_id": gtt_order_id, "status": "Pending"}
                for leg in kwargs.get("order_details") or []
            ]
        return {
            "Status": 200,
            "Success": {"gtt_order_id": gtt_order_id, "message": "Mock GTT modified"},
            "Error": None,
        }

    def gtt_three_leg_cancel_order(self, **kwargs):
        gtt_order_id = kwargs.get("gtt_order_id", "MOCK-GTT-1")
        record = self._gtt_orders.get(gtt_order_id)
        if record is not None:
            for leg in record["order_details"]:
                leg["status"] = "Cancelled"
        return {
            "Status": 200,
            "Success": {"gtt_order_id": gtt_order_id, "message": "Your request for order cancellation successfully submitted !"},
            "Error": None,
        }

    def gtt_single_leg_place_order(self, **kwargs):
        return self.gtt_three_leg_place_order(**kwargs)

    def gtt_single_leg_modify_order(self, **kwargs):
        return self.gtt_three_leg_modify_order(**kwargs)

    def gtt_single_leg_cancel_order(self, **kwargs):
        return self.gtt_three_leg_cancel_order(**kwargs)

    def gtt_order_book(self, **kwargs):
        return {"Status": 200, "Success": list(self._gtt_orders.values()), "Error": None}

    def get_trade_detail(self, exchange_code: str = "", order_id: str = "", **kwargs):
        return {"Status": 200, "Success": [], "Error": None}

    def __getattr__(self, name: str):
        """Safe stub for Breeze API Playground methods not explicitly mocked."""

        def _stub(*args, **kwargs):
            return {
                "Status": 200,
                "Success": {"message": f"Mock: {name} not fully implemented"},
                "Error": None,
            }

        return _stub
