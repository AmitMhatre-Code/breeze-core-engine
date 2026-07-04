"""Market outlook route -- thin pass-through to the portal's admin-managed global outlook.

Generation now happens centrally on breeze-saas-portal (one admin-configured
API key + prompt, one global result for the whole fleet); this deployment
just polls the portal's cached result (portal_market_outlook.py) and serves
it under the same path/shape the dashboard already expects.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Query

from icici_breeze_backend.app.services import portal_market_outlook

router = APIRouter(prefix="/api/outlook", tags=["outlook"])

_DISCLAIMER = (
    "AI-generated outlook. Informational only. Not investment advice. Verify with primary sources."
)


def _no_cached_outlook_payload(message: str) -> dict:
    """Valid-shaped JSON so the dashboard can load without a prior in-process cache."""
    return {
        "outlook_type": "market",
        "as_of": datetime.now(timezone.utc).isoformat(),
        "english_only": True,
        "disclaimer": _DISCLAIMER,
        "summary": [],
        "inference": {
            "volatility_view": "",
            "movement_scenarios": [],
            "confidence": "low",
            "caveats": [],
        },
        "strategy_ideas": [],
        "sources": [],
        "warning": {
            "error_code": "no_cached_outlook",
            "message": message,
            "stale_response_served": False,
        },
    }


@router.get("/market")
async def market_outlook(force_refresh: bool = Query(default=False)):
    if force_refresh:
        # Re-poll the portal's (Redis-backed, cheap) cache immediately -- this no
        # longer triggers a fresh LLM generation, since generation is centralized.
        await portal_market_outlook.refresh_once()

    cached = portal_market_outlook.get_cached_market_outlook()
    if cached is None:
        await portal_market_outlook.refresh_once()
        cached = portal_market_outlook.get_cached_market_outlook()
    if cached is None:
        return _no_cached_outlook_payload("Market outlook is not yet available. Try again shortly.")

    payload, age_sec = cached
    stale_after = 2 * portal_market_outlook.refresh_interval_sec()
    if age_sec > stale_after:
        return {
            **payload,
            "warning": {
                "error_code": "stale_portal_fetch",
                "message": f"Showing last-known outlook ({int(age_sec)}s old); portal refresh may be failing.",
                "stale_response_served": True,
            },
        }
    return payload
