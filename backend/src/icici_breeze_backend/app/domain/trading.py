"""Trading form schemas (uncovered shorts, vertical spread)."""
from typing import Optional

from pydantic import BaseModel


class UncoveredShortsFormRequest(BaseModel):
    """Uncovered Shorts form request."""
    product_type: Optional[str] = None
    stock_code: Optional[str] = None
    exchange_code: Optional[str] = None
    right: Optional[str] = None
    strike_price: Optional[str] = None
    quantity: Optional[str] = None
    expiry_date: Optional[str] = None
    limits: Optional[int] = None
    provision_elm: Optional[str] = None
    otm_call_distance: Optional[int] = None
    otm_put_distance: Optional[int] = None
    top: Optional[int] = None
    action: Optional[str] = None  # CLEAR | OPTIMIZE | SELL


class VerticalSpreadFormRequest(BaseModel):
    """Vertical Spread form request."""
    product_type: Optional[str] = None
    stock_code: Optional[str] = None
    exchange_code: Optional[str] = None
    right: Optional[str] = None
    strike_price: Optional[str] = None
    quantity: Optional[str] = None
    expiry_date: Optional[str] = None
    limits: Optional[int] = None
    provision_elm: Optional[str] = None
    range_lower: Optional[int] = None
    range_upper: Optional[int] = None
    top: Optional[int] = None
    action: Optional[str] = None  # CLEAR | OPTIMIZE | SELL
