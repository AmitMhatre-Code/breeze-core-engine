"""Tests for Breeze API usage SDK method name resolution."""
import sqlite3
import tempfile
from unittest.mock import patch

from icici_breeze_backend.app.services import api_usage
from icici_breeze_backend.app.services.api_usage import (
    _normalize_api_name,
    _resolve_sdk_method_name,
    get_daily_usage_by_api,
    record_breeze_call,
)

_ORDER_URL = "https://api.icicidirect.com/breezeapi/api/v1/order"
_FUNDS_URL = "https://api.icicidirect.com/breezeapi/api/v1/funds"
_TRADES_URL = "https://api.icicidirect.com/breezeapi/api/v1/trades"
_GTT_URL = "https://api.icicidirect.com/breezeapi/api/v1/gttorder"
_HIST_V1 = "https://api.icicidirect.com/breezeapi/api/v1/historicalcharts"
_HIST_V2 = "https://breezeapi.icicidirect.com/api/v2/historicalcharts"


class TestOrderEndpoint:
    def test_place_order(self):
        body = '{"stock_code":"ITC","exchange_code":"NSE","action":"buy","order_type":"limit","quantity":"1","price":"420"}'
        assert _resolve_sdk_method_name(_ORDER_URL, "POST", body) == "place_order"

    def test_modify_order(self):
        body = '{"order_id":"123","exchange_code":"NSE","price":"421","quantity":"1"}'
        assert _resolve_sdk_method_name(_ORDER_URL, "PUT", body) == "modify_order"

    def test_cancel_order(self):
        body = '{"exchange_code":"NSE","order_id":"123"}'
        assert _resolve_sdk_method_name(_ORDER_URL, "DELETE", body) == "cancel_order"

    def test_get_order_detail(self):
        body = '{"exchange_code":"NSE","order_id":"123"}'
        assert _resolve_sdk_method_name(_ORDER_URL, "GET", body) == "get_order_detail"

    def test_get_order_list(self):
        body = '{"exchange_code":"NSE","from_date":"2025-02-05T10:00:00.000Z","to_date":"2025-02-05T10:00:00.000Z"}'
        assert _resolve_sdk_method_name(_ORDER_URL, "GET", body) == "get_order_list"

    def test_order_without_verb_falls_back_to_segment(self):
        assert _resolve_sdk_method_name(_ORDER_URL) == "order"


class TestFundsEndpoint:
    def test_get_funds(self):
        assert _resolve_sdk_method_name(_FUNDS_URL, "GET", "{}") == "get_funds"

    def test_set_funds(self):
        body = '{"transaction_type":"debit","amount":"200","segment":"Equity"}'
        assert _resolve_sdk_method_name(_FUNDS_URL, "POST", body) == "set_funds"


class TestTradesEndpoint:
    def test_get_trade_list(self):
        body = '{"exchange_code":"NSE","from_date":"2025-02-05T06:00:00.000Z","to_date":"2025-02-05T06:00:00.000Z"}'
        assert _resolve_sdk_method_name(_TRADES_URL, "GET", body) == "get_trade_list"

    def test_get_trade_detail(self):
        body = '{"exchange_code":"NSE","order_id":"123"}'
        assert _resolve_sdk_method_name(_TRADES_URL, "GET", body) == "get_trade_detail"


class TestGttEndpoint:
    def test_gtt_order_book(self):
        body = '{"exchange_code":"NFO","from_date":"2025-02-05T06:00:00.00Z","to_date":"2025-02-05T06:00:00.00Z"}'
        assert _resolve_sdk_method_name(_GTT_URL, "GET", body) == "gtt_order_book"

    def test_gtt_three_leg_place(self):
        body = '{"exchange_code":"NFO","gtt_type":"cover_oco","fresh_order_action":"buy","order_details":[]}'
        assert _resolve_sdk_method_name(_GTT_URL, "POST", body) == "gtt_three_leg_place_order"

    def test_gtt_single_leg_place(self):
        body = '{"exchange_code":"NFO","gtt_type":"single","order_details":[]}'
        assert _resolve_sdk_method_name(_GTT_URL, "POST", body) == "gtt_single_leg_place_order"

    def test_gtt_three_leg_modify(self):
        body = '{"exchange_code":"NFO","gtt_order_id":"1","gtt_type":"oco","order_details":[]}'
        assert _resolve_sdk_method_name(_GTT_URL, "PUT", body) == "gtt_three_leg_modify_order"

    def test_gtt_single_leg_modify(self):
        body = '{"exchange_code":"NFO","gtt_order_id":"1","gtt_type":"single","order_details":[]}'
        assert _resolve_sdk_method_name(_GTT_URL, "PUT", body) == "gtt_single_leg_modify_order"

    def test_gtt_cancel_fallback(self):
        body = '{"exchange_code":"NFO","gtt_order_id":"1"}'
        assert _resolve_sdk_method_name(_GTT_URL, "DELETE", body) == "gtt_three_leg_cancel_order"


class TestSimpleEndpoints:
    def test_customer_details(self):
        url = "https://api.icicidirect.com/breezeapi/api/v1/customerdetails"
        assert _resolve_sdk_method_name(url, "GET", "{}") == "get_customer_details"

    def test_preview_order(self):
        url = "https://api.icicidirect.com/breezeapi/api/v1/preview_order"
        assert _resolve_sdk_method_name(url, "GET", "{}") == "preview_order"

    def test_historical_v1(self):
        assert _resolve_sdk_method_name(_HIST_V1, "GET", "{}") == "get_historical_data"

    def test_historical_v2(self):
        assert _resolve_sdk_method_name(_HIST_V2, "GET", "{}") == "get_historical_data_v2"


class TestLegacyNormalize:
    def test_legacy_order_unchanged(self):
        assert _normalize_api_name("order") == "order"

    def test_legacy_customerdetails(self):
        assert _normalize_api_name("customerdetails") == "get_customer_details"

    def test_legacy_funds(self):
        assert _normalize_api_name("funds") == "get_funds"


class TestRecordBreezeCall:
    def test_records_place_order_not_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = f"{tmp}/users.db"
            with patch.object(api_usage, "_DB_PATH", db_path):
                body = '{"stock_code":"ITC","exchange_code":"NSE","action":"buy","order_type":"limit","quantity":"1","price":"420"}'
                record_breeze_call(
                    "user1",
                    "https://api.icicidirect.com/breezeapi/api/v1/order",
                    route_id="test",
                    http_method="POST",
                    request_body=body,
                )
                rows = get_daily_usage_by_api("user1", days=30)
                assert len(rows) == 1
                assert rows[0]["api_name"] == "place_order"
                assert rows[0]["call_count"] == 1

                with sqlite3.connect(db_path) as conn:
                    raw = conn.execute(
                        "SELECT api_name FROM api_usage_daily_by_api WHERE user_id = ?",
                        ("user1",),
                    ).fetchone()
                    assert raw[0] == "place_order"
