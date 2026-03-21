"""Order domain schemas."""
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from icici_breeze_backend.app.core.config import BUY, SELL


class Order(BaseModel):
    """Single order."""
    order_id: Optional[str] = None
    status: Optional[str] = None
    stock_code: Optional[str] = None
    product_type: Optional[str] = None
    action: Optional[str] = None
    quantity: Optional[str] = None
    price: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class PlaceOrderRequest(BaseModel):
    """Request to place an order (BUY/SELL)."""
    product_type: str
    stock_code: str
    action: Literal["Buy", "Sell"]
    strike_price: str
    right: str
    price: str = "0"
    expiry_date: str
    quantity: str

    @field_validator("quantity")
    @classmethod
    def quantity_positive(cls, v: str) -> str:
        if v and int(v) <= 0:
            raise ValueError("quantity must be positive")
        return v


class BreakOrderRequest(BaseModel):
    """Request to break order into smaller lots."""
    stock_code: str
    expiry_date: str
    product_type: str
    right: str
    strike_price: str
    total_qty: str
    price: str = "0"
    action: Literal["Buy", "Sell"]


class CancelOrderDetail(BaseModel):
    """Per-order context from the book UI when cancelling (for consolidated messaging)."""

    option: str = ""
    open_quantity: int = 0


class BookActionRequest(BaseModel):
    """Request for order book actions (cancel, view)."""
    order_ids: List[str] = Field(default_factory=list)
    start: Optional[str] = None
    end: Optional[str] = None
    action: str
    cancel_details: Optional[List[CancelOrderDetail]] = None

    @field_validator("order_ids", mode="before")
    @classmethod
    def coerce_order_ids(cls, v):
        if v is None:
            return []
        if isinstance(v, str):
            return [v] if v else []
        return list(v) if v else []


class OrderFormRequest(BaseModel):
    """Full order form request (all actions: BUY, SELL, QUOTE, CLEAR)."""
    product_type: Optional[str] = None
    stock_code: Optional[str] = None
    exchange_code: Optional[str] = None
    expiry_date: Optional[str] = None
    right: Optional[str] = None
    strike_price: Optional[str] = None
    quantity: Optional[str] = None
    price: Optional[str] = None
    action: Literal["Buy", "Sell", "Quote", "Clear"]
    buy_button_state: Optional[str] = None
    sell_button_state: Optional[str] = None

    @model_validator(mode="after")
    def require_fields_for_buy_sell(self):
        if self.action in (BUY, SELL) and not all(
            [self.product_type, self.stock_code, self.expiry_date, self.right, self.strike_price, self.quantity]
        ):
            raise ValueError("product_type, stock_code, expiry_date, right, strike_price, quantity required for Buy/Sell")
        return self

    def to_place_request(self) -> PlaceOrderRequest:
        """Extract PlaceOrderRequest when action is BUY or SELL."""
        if self.action not in (BUY, SELL):
            raise ValueError(f"action must be {BUY} or {SELL}")
        return PlaceOrderRequest(
            product_type=self.product_type or "",
            stock_code=self.stock_code or "",
            action=self.action,
            strike_price=self.strike_price or "",
            right=self.right or "",
            price=self.price or "0",
            expiry_date=self.expiry_date or "",
            quantity=self.quantity or "0",
        )
