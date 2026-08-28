"""Dashboard API: VIX widget."""
from fastapi import APIRouter, Depends, HTTPException

from icici_breeze_backend.app.auth.context import RequestContext, get_request_context
from icici_breeze_backend.app.services.processor import processor
from icici_breeze_backend.app.services.dashboard_bootstrap import build_dashboard_bootstrap
from icici_breeze_backend.app.services.dashboard_day_pnl import build_dashboard_day_pnl
from icici_breeze_backend.app.services.dashboard_vix import (
    fetch_vix_core,
    fetch_vix_history,
    fetch_vix_options,
    fetch_vix_options_atm_skew,
)
from icici_breeze_backend.app.services.system_chain_health import get_system_health_status
from icici_breeze_backend.app.services.index_spot_feed import get_index_quotes_status

router = APIRouter()
breeze = processor()


@router.get("/ws-health")
@router.get("/ws-health/")
async def get_dashboard_ws_health(ctx: RequestContext = Depends(get_request_context)):
    """Combined NIFTY+SENSEX system-chain WS health for the navbar status dot.

    No `ctx.broker_token` check (unlike the /vix* routes below) -- this reads
    process-wide state, not a per-user broker session, so it must resolve
    correctly even for a logged-in user whose own session hasn't touched the
    broker yet.
    """
    return get_system_health_status()


@router.get("/index-quotes")
@router.get("/index-quotes/")
async def get_dashboard_index_quotes(ctx: RequestContext = Depends(get_request_context)):
    """Live NIFTY/SENSEX spot + day's change for the navbar ticker.

    Same process-wide-state rationale as /ws-health above -- no broker_token check
    (the REST post-close fallback inside `get_index_quotes_status` resolves its own
    session and degrades to null quotes if one isn't available).
    """
    return get_index_quotes_status(breeze, ctx.user_id)


@router.get("/live")
@router.get("/live/")
async def get_dashboard_live(ctx: RequestContext = Depends(get_request_context)):
    """WS-fed live values for the Dashboard tiles: Open P&L (from the portfolio
    P&L engine's ~2s repricing) and Day's P&L (from `dashboard_day_pnl_live`).

    Pure process-state read -- no broker_token gate, no ICICI call. Returns nulls
    for anything not warm yet; the frontend falls back to its REST snapshot then.
    """
    from icici_breeze_backend.app.services import dashboard_day_pnl_live
    from icici_breeze_backend.app.services.portfolio_pnl_engine import (
        is_tick_stream_stale,
        latest_snapshot,
    )

    user_id = ctx.user_id
    snap = latest_snapshot(user_id)
    open_pnl = None
    if snap and isinstance(snap.get("total_pnl"), (int, float)):
        open_pnl = {
            "total_pnl": snap["total_pnl"],
            "leg_count": len(snap.get("legs") or []),
            "stream_stale": bool(snap.get("stream_stale")),
            "computed_at": snap.get("computed_at"),
        }

    return {
        "open_pnl": open_pnl,
        "day_pnl": dashboard_day_pnl_live.latest(user_id),
        "tick_stale": is_tick_stream_stale(),
    }


@router.get("/bootstrap")
@router.get("/bootstrap/")
async def get_dashboard_bootstrap(ctx: RequestContext = Depends(get_request_context)):
    """Orchestrated home + portfolio + vix headline + options (no VIX history)."""
    if not ctx.broker_token:
        raise HTTPException(status_code=401, detail="ICICI broker token missing; re-login required")
    return build_dashboard_bootstrap(ctx.user_id, breeze, broker_token=ctx.broker_token or "")


@router.get("/day-pnl")
@router.get("/day-pnl/")
async def get_dashboard_day_pnl(ctx: RequestContext = Depends(get_request_context)):
    """Lazy: mark-to-market Day's P&L (realized + unrealized) from positions + today's trades.

    Loaded after first paint so /bootstrap stays fast; the tile renders a loading state
    until this resolves. Gross of brokerage/taxes.
    """
    if not ctx.broker_token:
        raise HTTPException(status_code=401, detail="ICICI broker token missing; re-login required")
    return build_dashboard_day_pnl(ctx.user_id, breeze, broker_token=ctx.broker_token or "")


@router.get("/vix")
@router.get("/vix/")
async def get_dashboard_vix(ctx: RequestContext = Depends(get_request_context)):
    """Fast: current VIX, NIFTY spot, ~3m INDVIX history. Use /vix/options for ATM IV, expected range."""
    if not ctx.broker_token:
        raise HTTPException(status_code=401, detail="ICICI broker token missing; re-login required")
    return fetch_vix_core(ctx.user_id, breeze)


@router.get("/vix/history")
@router.get("/vix/history/")
async def get_dashboard_vix_history(ctx: RequestContext = Depends(get_request_context)):
    """~3 calendar months of INDVIX daily closes (lazy-loaded chart data)."""
    if not ctx.broker_token:
        raise HTTPException(status_code=401, detail="ICICI broker token missing; re-login required")
    return {"vix_30d": fetch_vix_history(ctx.user_id, breeze)}


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
