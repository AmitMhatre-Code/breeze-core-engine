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
    aggressive_limit: bool = False

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
    aggressive_limit: bool = False


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


class BreakOrderChunkRequest(BaseModel):
    """Place one slice of a quantity-split order (see processor.break_order_place_chunk)."""

    product_type: str
    stock_code: str
    exchange_code: str = "NFO"
    expiry_date: str
    right: str
    strike_price: str
    total_qty: str
    price: str = "0"
    action: Literal["Buy", "Sell"]
    chunk_index: int = Field(ge=0)
    chunk_qty: Optional[str] = Field(
        default=None,
        description="Optional max units per child order; lot-rounded, capped at exchange freeze.",
    )
    aggressive_limit: bool = False
    from_parked_execution: bool = Field(
        default=False,
        description="True when executing an existing parked row; avoids duplicate auto-park.",
    )
    batch_group_id: Optional[str] = Field(
        default=None,
        description="Correlate multi-leg orders parked together after hours.",
    )


class BreakChunkDefaultsRequest(BaseModel):
    """Resolve default chunk size (freeze-aligned) and lot size for confirm UIs."""

    stock_code: str
    exchange_code: str = "NFO"
    expiry_date: str


class BreakOrderFinalizeRequest(BaseModel):
    """Build the same user messages as break_order after chunked placement on the client."""

    stock_code: str
    expiry_date: str
    right: str
    strike_price: str
    product_type: str = "Options"
    exchange_code: str = "NFO"
    price: str = "0"
    action: Literal["Buy", "Sell"]
    success_quantities: list[int] = Field(default_factory=list)
    danger_lines: list[str] = Field(default_factory=list)
    aggressive_limit: bool = False
    parked_only: bool = False
    market_closed_reason: Optional[str] = None


class BookCancelOneRequest(BaseModel):
    """Single order book line: order_id or \"order_id|EXCHANGE\"."""

    order_id: str


class BookCancelResultItem(BaseModel):
    order_ref: str
    success: bool
    error: Optional[str] = None


class BookCancelCommitRequest(BaseModel):
    """Persist cancel outcome messages after per-order cancel API calls from the client."""

    results: list[BookCancelResultItem]
    cancel_details: Optional[List[CancelOrderDetail]] = None


class LegModifyOrderRef(BaseModel):
    """One constituent order in a leg, as currently displayed in Order Book."""

    order_id: str
    exchange_code: str = "NFO"
    quantity: int
    pending_quantity: int = 0
    status: str
    price: Optional[str] = None


class LegModifyRequest(BaseModel):
    """Change the total quantity/price of a leg (1..N underlying chunk orders)."""

    stock_code: str
    expiry_date: str
    strike_price: str
    right: str
    product_type: str = "Options"
    exchange_code: str = "NFO"
    action: Literal["Buy", "Sell"]
    orders: List[LegModifyOrderRef]
    new_quantity: str
    new_price: Optional[str] = None
    rule_id: Optional[str] = None
    scrip_key: Optional[str] = None

    @model_validator(mode="after")
    def require_orders_and_positive_quantity(self):
        if not self.orders:
            raise ValueError("orders must not be empty")
        if int(self.new_quantity) <= 0:
            raise ValueError("new_quantity must be positive")
        return self


class LegModifyOrderOutcome(BaseModel):
    order_id: str
    quantity: int
    price: Optional[str] = None


class LegModifyFailure(BaseModel):
    ref: str
    error: str


class LegModifyResponse(BaseModel):
    success: bool
    cancelled_order_ids: List[str] = Field(default_factory=list)
    modified: List[LegModifyOrderOutcome] = Field(default_factory=list)
    placed: List[LegModifyOrderOutcome] = Field(default_factory=list)
    failures: List[LegModifyFailure] = Field(default_factory=list)
    rate_limited: bool = False
    rate_limit_pause_seconds: Optional[int] = None


class BookGroupLtpItem(BaseModel):
    """One order-book group row for batched LTP lookup."""

    group: str
    stock_code: str
    expiry_date: str
    strike_price: str | float | int
    right: str
    exchange_code: str = "NFO"


class BookGroupLtpRequest(BaseModel):
    groups: List[BookGroupLtpItem] = Field(default_factory=list)


class BookGroupLtpResponse(BaseModel):
    ltps: dict[str, float | None] = Field(default_factory=dict)


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
    aggressive_limit: bool = False
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
            aggressive_limit=self.aggressive_limit,
        )


class ParkedOrderItem(BaseModel):
    """One leg stored as a parked order (create / replace)."""

    product_type: str = "Options"
    stock_code: str
    exchange_code: str = "NFO"
    expiry_date: str
    right: str
    strike_price: str
    quantity: str
    price: str = "0"
    action: Literal["Buy", "Sell"]
    aggressive_limit: bool = False
    chunk_qty: Optional[str] = Field(
        default=None,
        description="Optional max units per child order when executing later.",
    )
    batch_group_id: Optional[str] = Field(
        default=None,
        description="Correlate multiple legs parked together (e.g. strategy preview).",
    )

    @field_validator("quantity")
    @classmethod
    def quantity_positive(cls, v: str) -> str:
        if v and int(v) <= 0:
            raise ValueError("quantity must be positive")
        return v


class ParkedOrderCreateRequest(BaseModel):
    """Create one or more parked rows; optionally remove existing rows first (re-park)."""

    items: List[ParkedOrderItem]
    replace_ids: Optional[List[str]] = None

    @model_validator(mode="after")
    def require_items(self):
        if not self.items:
            raise ValueError("items must not be empty")
        return self


class ParkedOrderPatchRequest(BaseModel):
    """Partial update for qty / price / chunk on Order Book."""

    quantity: Optional[str] = None
    price: Optional[str] = None
    chunk_qty: Optional[str] = None

    @model_validator(mode="after")
    def require_some_field(self):
        if not self.model_fields_set:
            raise ValueError("at least one of quantity, price, chunk_qty is required")
        return self

    @field_validator("quantity")
    @classmethod
    def quantity_if_set(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return v
        if int(v) <= 0:
            raise ValueError("quantity must be positive")
        return v


class ParkedOrderIdsRequest(BaseModel):
    """Bulk delete parked ids (same user scope enforced in route)."""

    ids: List[str] = Field(default_factory=list)

    @field_validator("ids", mode="before")
    @classmethod
    def coerce_ids(cls, v):
        if v is None:
            return []
        if isinstance(v, str):
            return [v] if v else []
        return list(v) if v else []


class ParkedOrderListItem(BaseModel):
    """Parked order as returned to clients (no user_id)."""

    id: str
    product_type: str
    stock_code: str
    exchange_code: str
    expiry_date: str
    right: str
    strike_price: str
    quantity: str
    price: str
    action: Literal["Buy", "Sell"]
    chunk_qty: Optional[str] = None
    batch_group_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ParkedOrderListResponse(BaseModel):
    orders: List[ParkedOrderListItem]
