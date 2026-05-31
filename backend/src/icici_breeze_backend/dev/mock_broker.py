"""Duck-typed stand-in for BreezeConnect when ICICI_BROKER_MODE=mock."""

from __future__ import annotations

import datetime
import re

from icici_breeze_backend.dev.fixtures import responses as fx


def _mock_option_chain_rows(stock_code: str, expiry_date: str, right: str):
    """Several strikes so strategy-builder / full chain UIs have data."""
    spot = "25000"
    strikes = [24600, 24750, 24900, 25000, 25150, 25300]
    rows = []
    for k in strikes:
        base = 45.0 + abs(k - 25000) * 0.12
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
                "spot_price": spot,
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

    def generate_session(self, **kwargs):
        return None

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
            "Success": [dict(r) for r in fx.MOCK_PORTFOLIO_POSITION_ROWS],
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
        rows = [dict(o) for o in fx.MOCK_ORDER_LIST_SAMPLE if (o.get("exchange_code") or "NFO") == (exchange_code or "NFO")]
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
            return {
                "Status": 200,
                "Success": [
                    {
                        "ltp": "15.85",
                        "previous_close": "15.60",
                        "open": "15.72",
                        "high": "16.05",
                        "low": "15.58",
                        "stock_code": sc or "INDVIX",
                    }
                ],
                "Error": None,
            }
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
        expiry_date = str(kwargs.get("expiry_date") or "")
        right = str(kwargs.get("right") or "Call")
        if strike and strike not in ("0", "None"):
            return {
                "Status": 200,
                "Success": [
                    {
                        "strike_price": strike,
                        "ltp": "50",
                        "best_bid_price": "49",
                        "best_offer_price": "51",
                        "total_buy_qty": "100",
                        "total_sell_qty": "100",
                        "spot_price": "25000",
                        "open_interest": "88000",
                        "right": right,
                    }
                ],
                "Error": None,
            }
        rows = _mock_option_chain_rows(stock_code, expiry_date, right)
        return {"Status": 200, "Success": rows, "Error": None}

    def get_historical_data_v2(self, **kwargs):
        from_date = str(kwargs.get("from_date") or "")
        to_date = str(kwargs.get("to_date") or "")
        succ = _mock_hist_v2_rows(from_date, to_date)
        return {"Status": 200, "Success": succ, "Error": None}

    def margin_calculator(self, margin_list, exchange_code: str = "", **kwargs):
        return {
            "Status": 200,
            "Success": {"total_margin": 125000, "span_margin_required": 118500},
            "Error": None,
        }

    def get_order_detail(self, exchange_code: str = "", order_id: str = "", **kwargs):
        rows = [dict(o) for o in fx.MOCK_ORDER_LIST_SAMPLE if str(o.get("order_id")) == str(order_id)]
        if not rows and fx.MOCK_ORDER_LIST_SAMPLE:
            rows = [dict(fx.MOCK_ORDER_LIST_SAMPLE[0])]
        return {"Status": 200, "Success": rows, "Error": None}

    def get_demat_holdings(self, **kwargs):
        return {
            "Status": 200,
            "Success": [
                {
                    "stock_code": "UNITEC",
                    "stock_ISIN": "INE694A01020",
                    "quantity": "1",
                    "demat_avail_quantity": "0",
                }
            ],
            "Error": None,
        }

    def get_portfolio_holdings(self, **kwargs):
        return {"Status": 200, "Success": [], "Error": None}

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
        return {
            "Status": 200,
            "Success": {"gtt_order_id": "MOCK-GTT-1", "message": "Mock GTT placed"},
            "Error": None,
        }

    def gtt_three_leg_modify_order(self, **kwargs):
        return {
            "Status": 200,
            "Success": {"gtt_order_id": kwargs.get("gtt_order_id", "MOCK-GTT-1"), "message": "Mock GTT modified"},
            "Error": None,
        }

    def gtt_three_leg_cancel_order(self, **kwargs):
        return {
            "Status": 200,
            "Success": {"gtt_order_id": kwargs.get("gtt_order_id", "MOCK-GTT-1"), "message": "Mock GTT cancelled"},
            "Error": None,
        }

    def gtt_single_leg_place_order(self, **kwargs):
        return self.gtt_three_leg_place_order(**kwargs)

    def gtt_single_leg_modify_order(self, **kwargs):
        return self.gtt_three_leg_modify_order(**kwargs)

    def gtt_single_leg_cancel_order(self, **kwargs):
        return self.gtt_three_leg_cancel_order(**kwargs)

    def gtt_order_book(self, **kwargs):
        return {"Status": 200, "Success": [], "Error": None}

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
