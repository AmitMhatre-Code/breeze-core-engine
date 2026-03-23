"""Dashboard API: VIX widget."""
from fastapi import APIRouter, Depends, HTTPException

from icici_breeze_backend.app.auth.context import RequestContext, get_request_context
from icici_breeze_backend.app.services.processor import processor
from icici_breeze_backend.app.services.dashboard_vix import fetch_vix_core, fetch_vix_options, fetch_vix_options_atm_skew

router = APIRouter()
breeze = processor()


@router.get("/vix")
@router.get("/vix/")
async def get_dashboard_vix(ctx: RequestContext = Depends(get_request_context)):
    """Fast: current VIX, NIFTY spot, ~3m INDVIX history. Use /vix/options for ATM IV, expected range."""
    if not ctx.broker_token:
        raise HTTPException(status_code=401, detail="ICICI broker token missing; re-login required")
    return fetch_vix_core(ctx.user_id, breeze)


@router.get("/vix/options")
@router.get("/vix/options/")
async def get_dashboard_vix_options(ctx: RequestContext = Depends(get_request_context)):
    """NIFTY spot, next expiry, ATM IV, expected range (1σ), put:call OI ratio."""
    if not ctx.broker_token:
        raise HTTPException(status_code=401, detail="ICICI broker token missing; re-login required")
    return fetch_vix_options(ctx.user_id, breeze)


@router.get("/vix/options/atm")
@router.get("/vix/options/atm/")
async def get_dashboard_vix_options_atm(ctx: RequestContext = Depends(get_request_context)):
    """First expiry only: NIFTY, ATM IV, expected range."""
    if not ctx.broker_token:
        raise HTTPException(status_code=401, detail="ICICI broker token missing; re-login required")
    return fetch_vix_options_atm_skew(ctx.user_id, breeze)
