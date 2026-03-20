"""JSON settings API models."""
from typing import Any, Optional

from pydantic import BaseModel, Field


class CredentialsStateResponse(BaseModel):
    customer: dict[str, Any] = Field(default_factory=dict)
    margin: dict[str, Any] = Field(default_factory=dict)
    user_id: str = ""
    message: Optional[str] = None


class CredentialsUpdateBody(BaseModel):
    user_id: str
    api_key: str
    secret_fragment: str


class QuantityLimitsStateResponse(BaseModel):
    customer: dict[str, Any] = Field(default_factory=dict)
    margin: dict[str, Any] = Field(default_factory=dict)
    limits: list[dict[str, Any]] = Field(default_factory=list)
    message: Optional[str] = None
    user_id: str = ""


class QuantityLimitUpdateItem(BaseModel):
    short_name: str
    exchange_code: str
    segment_code: str = ""
    qty_limit: int


class QuantityLimitsUpdateBody(BaseModel):
    rows: list[QuantityLimitUpdateItem]
