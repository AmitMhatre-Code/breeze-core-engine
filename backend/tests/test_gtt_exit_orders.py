"""Tests for the Portfolio > individual leg > Exit Rule feature: the
`processor.place_gtt_oco_exit_order` / `get_gtt_order_book` / `cancel_gtt_order`
broker wrappers, and the `route_gtt_exit_orders` route layer (action inversion,
leg matching/filtering). Unlike the group square-off rule, there's no local
persistence to test — ICICI's GTT order book is the only source of truth.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from icici_breeze_backend.app.api.v1 import route_gtt_exit_orders as route
from icici_breeze_backend.app.domain.gtt_exit_order import PlaceGttExitOrderRequest


def _ctx(user_id="u1"):
    from icici_breeze_backend.app.auth.context import RequestContext

    return RequestContext(user_id=user_id, username=user_id, roles=["trader"], is_authenticated=True, broker_token="tok")


def _place_body(**overrides):
    base = dict(
        stock_code="NIFTY",
        exchange_code="NFO",
        expiry_date="30-Jun-2026",
        strike_price="25000",
        right="call",
        quantity="75",
        product_type="options",
        action="Buy",
        target_trigger_price=14.5,
        target_limit_price=15,
        stop_trigger_price=7.5,
        stop_limit_price=7,
    )
    base.update(overrides)
    return PlaceGttExitOrderRequest(**base)


class _FakeProcessor:
    def __init__(self, place_response=None, book_response=None, cancel_response=None):
        self.place_response = place_response or {
            "Status": 200,
            "Success": {"gtt_order_id": "MOCK-GTT-1", "message": "placed"},
            "Error": None,
        }
        self.book_response = book_response or {"Status": 200, "Success": [], "Error": None}
        self.cancel_response = cancel_response or {
            "Status": 200,
            "Success": {"gtt_order_id": "MOCK-GTT-1", "message": "cancelled"},
            "Error": None,
        }
        self.place_calls: list[dict] = []

    def place_gtt_oco_exit_order(self, user_id, **kwargs):
        self.place_calls.append(kwargs)
        return self.place_response

    def get_gtt_order_book(self, user_id, exchange_code, from_date, to_date):
        return self.book_response

    def cancel_gtt_order(self, user_id, exchange_code, gtt_order_id):
        return self.cancel_response


class TestPlaceRoute:
    def test_long_position_close_action_is_sell(self, monkeypatch):
        fake = _FakeProcessor()
        monkeypatch.setattr(route, "breeze", fake)
        asyncio.run(route.place_gtt_exit_order(_place_body(action="Buy"), _ctx()))
        assert fake.place_calls[0]["close_action"] == "Sell"

    def test_short_position_close_action_is_buy(self, monkeypatch):
        fake = _FakeProcessor()
        monkeypatch.setattr(route, "breeze", fake)
        asyncio.run(route.place_gtt_exit_order(_place_body(action="Sell"), _ctx()))
        assert fake.place_calls[0]["close_action"] == "Buy"

    def test_successful_place_returns_gtt_order_id(self, monkeypatch):
        fake = _FakeProcessor()
        monkeypatch.setattr(route, "breeze", fake)
        resp = asyncio.run(route.place_gtt_exit_order(_place_body(), _ctx()))
        assert resp.gtt_order_id == "MOCK-GTT-1"

    def test_broker_error_raises_400(self, monkeypatch):
        fake = _FakeProcessor(place_response={"Status": 400, "Success": None, "Error": "RMS:Margin Exceeds"})
        monkeypatch.setattr(route, "breeze", fake)
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(route.place_gtt_exit_order(_place_body(), _ctx()))
        assert exc_info.value.status_code == 400
        assert "Margin" in exc_info.value.detail

    def test_rejects_non_positive_prices(self):
        with pytest.raises(ValueError):
            _place_body(target_trigger_price=0)


def _gtt_book_row(**overrides):
    base = dict(
        stock_code="NIFTY",
        expiry_date="30-Jun-2026",
        strike_price=25000.0,
        right="Call",
        gtt_type="Cover OCO",
        order_datetime="05-FEB-2025 11:14:38",
        order_details=[
            {
                "gtt_leg_type": "Target",
                "action": "Sell",
                "trigger_price": 14.5,
                "limit_price": 15.0,
                "status": "Pending",
                "gtt_order_id": "2025020500001234",
            },
            {
                "gtt_leg_type": "Stoploss",
                "action": "Sell",
                "trigger_price": 7.5,
                "limit_price": 7.0,
                "status": "Pending",
                "gtt_order_id": "2025020500001234",
            },
        ],
    )
    base.update(overrides)
    return base


class TestStatusRoute:
    def test_returns_none_when_no_match(self, monkeypatch):
        fake = _FakeProcessor(book_response={"Status": 200, "Success": [_gtt_book_row(stock_code="BANKNIFTY")], "Error": None})
        monkeypatch.setattr(route, "breeze", fake)
        resp = asyncio.run(
            route.get_gtt_exit_order_status(
                stock_code="NIFTY", expiry_date="30-Jun-2026", strike_price="25000", right="call", ctx=_ctx()
            )
        )
        assert resp.order is None

    def test_matches_leg_by_stock_expiry_strike_right(self, monkeypatch):
        fake = _FakeProcessor(book_response={"Status": 200, "Success": [_gtt_book_row()], "Error": None})
        monkeypatch.setattr(route, "breeze", fake)
        resp = asyncio.run(
            route.get_gtt_exit_order_status(
                stock_code="NIFTY", expiry_date="30-Jun-2026", strike_price="25000", right="call", ctx=_ctx()
            )
        )
        assert resp.order is not None
        assert resp.order.gtt_order_id == "2025020500001234"
        assert len(resp.order.legs) == 2

    def test_picks_most_recent_when_multiple_matches(self, monkeypatch):
        older = _gtt_book_row(order_datetime="01-JAN-2025 10:00:00", order_details=[
            {"gtt_leg_type": "Target", "action": "Sell", "trigger_price": 10, "limit_price": 11, "status": "Cancelled", "gtt_order_id": "OLD-1"},
        ])
        newer = _gtt_book_row(order_datetime="05-FEB-2025 11:14:38", order_details=[
            {"gtt_leg_type": "Target", "action": "Sell", "trigger_price": 14.5, "limit_price": 15, "status": "Pending", "gtt_order_id": "NEW-1"},
        ])
        fake = _FakeProcessor(book_response={"Status": 200, "Success": [older, newer], "Error": None})
        monkeypatch.setattr(route, "breeze", fake)
        resp = asyncio.run(
            route.get_gtt_exit_order_status(
                stock_code="NIFTY", expiry_date="30-Jun-2026", strike_price="25000", right="call", ctx=_ctx()
            )
        )
        assert resp.order.gtt_order_id == "NEW-1"

    def test_does_not_match_different_strike(self, monkeypatch):
        fake = _FakeProcessor(book_response={"Status": 200, "Success": [_gtt_book_row(strike_price=24500.0)], "Error": None})
        monkeypatch.setattr(route, "breeze", fake)
        resp = asyncio.run(
            route.get_gtt_exit_order_status(
                stock_code="NIFTY", expiry_date="30-Jun-2026", strike_price="25000", right="call", ctx=_ctx()
            )
        )
        assert resp.order is None

    def test_does_not_match_different_right(self, monkeypatch):
        fake = _FakeProcessor(book_response={"Status": 200, "Success": [_gtt_book_row(right="Put")], "Error": None})
        monkeypatch.setattr(route, "breeze", fake)
        resp = asyncio.run(
            route.get_gtt_exit_order_status(
                stock_code="NIFTY", expiry_date="30-Jun-2026", strike_price="25000", right="call", ctx=_ctx()
            )
        )
        assert resp.order is None

    def test_fully_cancelled_order_reads_as_no_active_order(self, monkeypatch):
        cancelled = _gtt_book_row(order_details=[
            {"gtt_leg_type": "Target", "action": "Sell", "trigger_price": 14.5, "limit_price": 15, "status": "Cancelled", "gtt_order_id": "X-1"},
            {"gtt_leg_type": "Stoploss", "action": "Sell", "trigger_price": 7.5, "limit_price": 7, "status": "Cancelled", "gtt_order_id": "X-1"},
        ])
        fake = _FakeProcessor(book_response={"Status": 200, "Success": [cancelled], "Error": None})
        monkeypatch.setattr(route, "breeze", fake)
        resp = asyncio.run(
            route.get_gtt_exit_order_status(
                stock_code="NIFTY", expiry_date="30-Jun-2026", strike_price="25000", right="call", ctx=_ctx()
            )
        )
        assert resp.order is None

    def test_partially_cancelled_order_still_reads_as_active(self, monkeypatch):
        partial = _gtt_book_row(order_details=[
            {"gtt_leg_type": "Target", "action": "Sell", "trigger_price": 14.5, "limit_price": 15, "status": "Cancelled", "gtt_order_id": "X-1"},
            {"gtt_leg_type": "Stoploss", "action": "Sell", "trigger_price": 7.5, "limit_price": 7, "status": "Pending", "gtt_order_id": "X-1"},
        ])
        fake = _FakeProcessor(book_response={"Status": 200, "Success": [partial], "Error": None})
        monkeypatch.setattr(route, "breeze", fake)
        resp = asyncio.run(
            route.get_gtt_exit_order_status(
                stock_code="NIFTY", expiry_date="30-Jun-2026", strike_price="25000", right="call", ctx=_ctx()
            )
        )
        assert resp.order is not None
        assert resp.order.gtt_order_id == "X-1"

    def test_skips_cancelled_even_when_it_is_the_most_recent(self, monkeypatch):
        older_active = _gtt_book_row(order_datetime="01-JAN-2025 10:00:00", order_details=[
            {"gtt_leg_type": "Target", "action": "Sell", "trigger_price": 10, "limit_price": 11, "status": "Pending", "gtt_order_id": "OLD-ACTIVE"},
        ])
        newer_cancelled = _gtt_book_row(order_datetime="05-FEB-2025 11:14:38", order_details=[
            {"gtt_leg_type": "Target", "action": "Sell", "trigger_price": 14.5, "limit_price": 15, "status": "Cancelled", "gtt_order_id": "NEW-CANCELLED"},
        ])
        fake = _FakeProcessor(book_response={"Status": 200, "Success": [older_active, newer_cancelled], "Error": None})
        monkeypatch.setattr(route, "breeze", fake)
        resp = asyncio.run(
            route.get_gtt_exit_order_status(
                stock_code="NIFTY", expiry_date="30-Jun-2026", strike_price="25000", right="call", ctx=_ctx()
            )
        )
        assert resp.order.gtt_order_id == "OLD-ACTIVE"


class TestCancelRoute:
    def test_cancel_success(self, monkeypatch):
        fake = _FakeProcessor()
        monkeypatch.setattr(route, "breeze", fake)
        resp = asyncio.run(route.cancel_gtt_exit_order("MOCK-GTT-1", ctx=_ctx()))
        assert resp.ok is True

    def test_cancel_broker_error_raises_400(self, monkeypatch):
        fake = _FakeProcessor(cancel_response={"Status": 400, "Success": None, "Error": "Order already triggered"})
        monkeypatch.setattr(route, "breeze", fake)
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(route.cancel_gtt_exit_order("MOCK-GTT-1", ctx=_ctx()))
        assert exc_info.value.status_code == 400


class TestProcessorGttMethods:
    def test_place_gtt_oco_exit_order_builds_expected_sdk_call(self, monkeypatch):
        from icici_breeze_backend.app.services.processor import processor as processor_factory

        captured = {}

        class _FakeSdk:
            def gtt_three_leg_place_order(self, **kwargs):
                captured.update(kwargs)
                return {"Status": 200, "Success": {"gtt_order_id": "X-1"}, "Error": None}

        p = processor_factory()
        monkeypatch.setattr(p, "get_session_breeze", lambda user_id: _FakeSdk())

        resp = p.place_gtt_oco_exit_order(
            "u1",
            product_type="options",
            stock_code="NIFTY",
            exchange_code="NFO",
            strike_price="25000",
            right="call",
            quantity="75",
            expiry_date="30-Jun-2026",
            close_action="Sell",
            target_trigger_price=14.5,
            target_limit_price=15,
            stop_trigger_price=7.5,
            stop_limit_price=7,
        )

        assert resp["Success"]["gtt_order_id"] == "X-1"
        assert captured["gtt_type"] == "oco"
        assert captured["index_or_stock"] == "index"
        assert captured["order_details"][0]["gtt_leg_type"] == "target"
        assert captured["order_details"][0]["action"] == "sell"
        assert captured["order_details"][1]["gtt_leg_type"] == "stoploss"

    def test_place_gtt_oco_exit_order_stock_underlying_is_not_index(self, monkeypatch):
        from icici_breeze_backend.app.services.processor import processor as processor_factory

        captured = {}

        class _FakeSdk:
            def gtt_three_leg_place_order(self, **kwargs):
                captured.update(kwargs)
                return {"Status": 200, "Success": {"gtt_order_id": "X-1"}, "Error": None}

        p = processor_factory()
        monkeypatch.setattr(p, "get_session_breeze", lambda user_id: _FakeSdk())
        p.place_gtt_oco_exit_order(
            "u1", product_type="options", stock_code="RELIANCE", exchange_code="NFO",
            strike_price="2500", right="call", quantity="500", expiry_date="30-Jun-2026",
            close_action="Sell", target_trigger_price=14.5, target_limit_price=15,
            stop_trigger_price=7.5, stop_limit_price=7,
        )
        assert captured["index_or_stock"] == "stock"

    def test_place_gtt_oco_exit_order_no_session_returns_error(self, monkeypatch):
        from icici_breeze_backend.app.services.processor import processor as processor_factory

        p = processor_factory()
        monkeypatch.setattr(p, "get_session_breeze", lambda user_id: None)
        resp = p.place_gtt_oco_exit_order(
            "u1", product_type="options", stock_code="NIFTY", exchange_code="NFO",
            strike_price="25000", right="call", quantity="75", expiry_date="30-Jun-2026",
            close_action="Sell", target_trigger_price=14.5, target_limit_price=15,
            stop_trigger_price=7.5, stop_limit_price=7,
        )
        assert resp["Status"] == 400
