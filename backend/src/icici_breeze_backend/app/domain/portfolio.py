"""Portfolio domain schemas."""
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

class PortfolioActionRequest(BaseModel):
    """Request for portfolio form actions (SQUAREOFF)."""
    product_type: str
    stock_code: str
    exchange_code: str = ""
    position_action: str
    expiry_date: str
    right: str
    strike_price: str
    quantity: str
    action: Literal["SquareOff"]


class Position(BaseModel):
    """Single portfolio position."""
    stock_code: str = ""
    exchange_code: str = ""
    expiry_date: str = ""
    product_type: str = ""
    right: str = ""
    strike_price: str = ""
    quantity: str = "0"
    average_price: str = "0"
    ltp: str = "0"
    spot_price: Optional[str] = None
    action: str = ""
    option: Optional[str] = None
    current_profit: Optional[float] = None
    carry_profit: Optional[float] = None
    span_margin_required: Optional[float] = None

    model_config = ConfigDict(extra="allow")


class MarginInfo(BaseModel):
    """Margin information."""
    actual_margin_ute: float = 0
    cash_limit: float = 0
    actual_margin_avl: float = 0
    target_margin_free: float = 0
    limits: float = 0
    last_refresh: str = ""

    model_config = ConfigDict(extra="allow")


class CustomerDetails(BaseModel):
    """Customer/account details from broker."""
    idirect_user_name: Optional[str] = None

    model_config = ConfigDict(extra="allow")
