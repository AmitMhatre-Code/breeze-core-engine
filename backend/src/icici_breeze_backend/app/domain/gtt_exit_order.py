"""Request/response schemas for per-leg GTT OCO exit orders (Portfolio > individual leg > Exit Rule).

Unlike the group-level square-off rule (`domain/squareoff_rule.py`), these orders are placed and
monitored entirely by ICICI (a GTT OCO bracket) — this app never persists or polls them; the GET
endpoint always re-queries `breeze.gtt_order_book` live.
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class PlaceGttExitOrderRequest(BaseModel):
    stock_code: str
    exchange_code: str = "NFO"
    expiry_date: str = Field(..., description="Leg's own expiry, display or API format")
    strike_price: str
    right: str
    quantity: str
    product_type: str = "options"
    action: str = Field(..., description="Position's own BUY/SELL side; GTT legs close the opposite side")
    target_trigger_price: float = Field(..., gt=0)
    target_limit_price: float = Field(..., gt=0)
    stop_trigger_price: float = Field(..., gt=0)
    stop_limit_price: float = Field(..., gt=0)


class PlaceGttExitOrderResponse(BaseModel):
    gtt_order_id: Optional[str] = None
    message: Optional[str] = None


class GttExitOrderLeg(BaseModel):
    gtt_leg_type: Optional[str] = None
    action: Optional[str] = None
    trigger_price: Optional[float] = None
    limit_price: Optional[float] = None
    status: Optional[str] = None


class GttExitOrderRecord(BaseModel):
    gtt_order_id: str
    gtt_type: Optional[str] = None
    order_datetime: Optional[str] = None
    legs: List[GttExitOrderLeg] = []


class GttExitOrderStatusResponse(BaseModel):
    order: Optional[GttExitOrderRecord] = None


class GttExitOrderRowRecord(BaseModel):
    """One row of the account's full GTT order book (Orders page > Profit Booking / Stop
    Loss), unlike `GttExitOrderRecord` which is narrowed to a single leg's identity."""

    gtt_order_id: str
    stock_code: Optional[str] = None
    exchange_code: Optional[str] = None
    expiry_display: Optional[str] = None
    strike_price: Optional[str] = None
    right: Optional[str] = None
    quantity: Optional[str] = None
    product_type: Optional[str] = None
    gtt_type: Optional[str] = None
    order_datetime: Optional[str] = None
    legs: List[GttExitOrderLeg] = []
    is_cancelled: bool = False


class GttExitOrderListResponse(BaseModel):
    orders: List[GttExitOrderRowRecord] = []


class CancelGttExitOrderResponse(BaseModel):
    ok: bool
    message: Optional[str] = None
