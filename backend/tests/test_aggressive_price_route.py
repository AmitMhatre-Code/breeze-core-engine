"""POST /order/aggressive-price: server-side LTP -> tick-rounded aggressive limit price."""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from icici_breeze_backend.app.api.v1 import route_order
from icici_breeze_backend.app.domain.order import (
    AggressivePriceLeg,
    AggressivePriceRequest,
)


def _ctx():
    ctx = MagicMock()
    ctx.user_id = "U1"
    ctx.broker_token = "tok"
    return ctx


def _req(action="Buy", tolerance_pct=5.0):
    return AggressivePriceRequest(
        tolerance_pct=tolerance_pct,
        legs=[
            AggressivePriceLeg(
                ref="0",
                stock_code="NIFTY",
                exchange_code="NFO",
                expiry_date="16-Jun-2026",
                right="Call",
                strike_price="24000",
                action=action,
            )
        ],
    )


def test_disabled_returns_403(monkeypatch):
    monkeypatch.setattr(route_order.cfg, "AGGRESSIVE_LIMIT_ORDER_ENABLED", False)
    with pytest.raises(Exception) as exc:
        asyncio.run(
            route_order.post_aggressive_price(body=_req(), context=_ctx(), _trading_ok=None)
        )
    assert getattr(exc.value, "status_code", None) == 403


def test_buy_prices_above_ltp(monkeypatch):
    monkeypatch.setattr(route_order.cfg, "AGGRESSIVE_LIMIT_ORDER_ENABLED", True)
    with patch.object(
        route_order.breeze,
        "fetch_group_ltps_batch",
        return_value={"0": 100.0},
    ):
        out = asyncio.run(
            route_order.post_aggressive_price(body=_req("Buy", 5.0), context=_ctx(), _trading_ok=None)
        )
    assert out.results[0].price == "105.0"
    assert out.results[0].ltp == 100.0
    assert out.results[0].error is None


def test_sell_prices_below_ltp(monkeypatch):
    monkeypatch.setattr(route_order.cfg, "AGGRESSIVE_LIMIT_ORDER_ENABLED", True)
    with patch.object(
        route_order.breeze, "fetch_group_ltps_batch", return_value={"0": 100.0}
    ):
        out = asyncio.run(
            route_order.post_aggressive_price(body=_req("Sell", 5.0), context=_ctx(), _trading_ok=None)
        )
    assert out.results[0].price == "95.0"


def test_missing_ltp_returns_error_not_price(monkeypatch):
    monkeypatch.setattr(route_order.cfg, "AGGRESSIVE_LIMIT_ORDER_ENABLED", True)
    with patch.object(
        route_order.breeze, "fetch_group_ltps_batch", return_value={"0": None}
    ):
        out = asyncio.run(
            route_order.post_aggressive_price(body=_req(), context=_ctx(), _trading_ok=None)
        )
    assert out.results[0].price is None
    assert out.results[0].error is not None


def test_tolerance_clamped_in_response(monkeypatch):
    monkeypatch.setattr(route_order.cfg, "AGGRESSIVE_LIMIT_ORDER_ENABLED", True)
    monkeypatch.setattr(route_order.cfg, "AGGRESSIVE_LIMIT_MAX_TOLERANCE_PCT", 25.0)
    with patch.object(
        route_order.breeze, "fetch_group_ltps_batch", return_value={"0": 100.0}
    ):
        out = asyncio.run(
            route_order.post_aggressive_price(
                body=_req("Buy", 999.0), context=_ctx(), _trading_ok=None
            )
        )
    assert out.tolerance_pct == 25.0
    assert out.results[0].price == "125.0"
