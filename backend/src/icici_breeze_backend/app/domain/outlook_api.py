"""API schemas for GenAI market outlook."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


OUTLOOK_DISCLAIMER = (
    "AI-generated outlook. Informational only. Not investment advice. Verify with primary sources."
)


class OutlookSource(BaseModel):
    title: str
    url: str
    publisher: str
    published_at: Optional[str] = None


class OutlookInference(BaseModel):
    volatility_view: str
    movement_scenarios: list[str] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"]
    caveats: list[str] = Field(default_factory=list)


class OutlookStrategyIdea(BaseModel):
    tag: str
    rationale: str
    risk_note: str


class OutlookResponse(BaseModel):
    outlook_type: Literal["market"]
    as_of: str
    english_only: bool = True
    disclaimer: str = OUTLOOK_DISCLAIMER
    summary: list[str] = Field(default_factory=list)
    inference: OutlookInference
    strategy_ideas: list[OutlookStrategyIdea] = Field(default_factory=list)
    sources: list[OutlookSource] = Field(default_factory=list)


class OutlookErrorResponse(BaseModel):
    error_code: str
    message: str
    stale_response_served: bool = False
