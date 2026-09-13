"""Bots -> Backtest (docs/bots-scalping-plan.md section 8.11).

Static paths under `/bots/backtest/...`, each enumerated in `next.config.js` and both nginx
confs, for the reason `route_bots` gives: the page itself is `/bots/backtest`, which no
rewrite matches, so the page and API namespaces stay disjoint.

Nothing here places an order, so read-only licence mode does not block it. Fetching is
refused in market hours and off the live broker; see `services/bots/backtest_jobs.py`.
"""
from __future__ import annotations

import datetime
import json
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.auth.context import RequestContext, get_request_context
from icici_breeze_backend.app.core.timezone import now_ist
from icici_breeze_backend.app.services.bots import backtest_jobs as jobs
from icici_breeze_backend.app.services.bots import backtest_service as service
from icici_breeze_backend.app.services.bots.scalping import backtest_regime as regime
from icici_breeze_backend.app.services.bots.scalping import backtest_store as store

router = APIRouter()

BotKey = Literal["momentum", "fly", "expiry"]


class RangeRequest(BaseModel):
    bot: BotKey
    from_date: Optional[datetime.date] = None
    to_date: Optional[datetime.date] = None


class ReplayRequest(RangeRequest):
    model: bool = False


def _range(req: RangeRequest) -> tuple[datetime.date, datetime.date]:
    try:
        return service.clip_range(req.from_date, req.to_date, now_ist().date())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


def _start(fn):
    try:
        return fn()
    except jobs.Busy as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/backtest/overview")
def overview(ctx: RequestContext = Depends(get_request_context)):
    """Everything the page renders, polled while a job runs."""
    jobs.ensure_store()
    probe = store.get_meta("probe_report")
    return {
        "broker_mode": cfg.ICICI_BROKER_MODE,
        "live": jobs.broker_live(),
        "market_hours_block": jobs.market_hours_reason(),
        "history_start": regime.HISTORY_START.isoformat(),
        "default_to": (now_ist().date() - datetime.timedelta(days=1)).isoformat(),
        "job": jobs.state(),
        "probe": json.loads(probe) if probe else None,
        "probed_at": store.get_meta("probed_at"),
        "coverage": service.coverage_summary(),
        "saved": service.saved_summary(ctx.user_id),
        "runs": store.list_runs(ctx.user_id),
    }


@router.post("/backtest/probe")
def probe(ctx: RequestContext = Depends(get_request_context)):
    return _start(lambda: jobs.start_probe(ctx.user_id))


@router.post("/backtest/fetch")
def fetch(req: RangeRequest, ctx: RequestContext = Depends(get_request_context)):
    start, end = _range(req)
    return _start(lambda: jobs.start_fetch(ctx.user_id, req.bot, start, end))


@router.post("/backtest/replay")
def replay(req: ReplayRequest, ctx: RequestContext = Depends(get_request_context)):
    start, end = _range(req)
    return _start(lambda: jobs.start_replay(ctx.user_id, req.bot, start, end, model=req.model))


@router.post("/backtest/cancel")
def cancel(ctx: RequestContext = Depends(get_request_context)):
    return {"cancelled": jobs.cancel()}


@router.get("/backtest/run")
def get_run(id: str = Query(...), ctx: RequestContext = Depends(get_request_context)):
    jobs.ensure_store()
    run = store.get_run(id, ctx.user_id)
    if run is None:
        raise HTTPException(status_code=404, detail="No such backtest run.")
    return run


@router.delete("/backtest/run")
def delete_run(id: str = Query(...), ctx: RequestContext = Depends(get_request_context)):
    jobs.ensure_store()
    if not store.delete_run(id, ctx.user_id):
        raise HTTPException(status_code=404, detail="No such finished backtest run.")
    return {"deleted": True}


@router.get("/backtest/run/csv")
def run_csv(id: str = Query(...), ctx: RequestContext = Depends(get_request_context)):
    jobs.ensure_store()
    run = store.get_run(id, ctx.user_id)
    if run is None:
        raise HTTPException(status_code=404, detail="No such backtest run.")
    name = f"backtest-{run['bot']}-{run['params'].get('from')}-{run['params'].get('to')}.csv"
    return Response(
        content=service.trades_csv(run.get("trades") or []),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.get("/backtest/compare")
def compare(
    bot: Literal["momentum", "fly"] = Query(...),
    date: datetime.date = Query(...),
    model: bool = Query(False),
    ctx: RequestContext = Depends(get_request_context),
):
    """Simulation cycles against a backtest of the same day. Synchronous: one day is fast."""
    jobs.ensure_store()
    try:
        return service.compare(bot, date, ctx.user_id, model=model)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except (service.AwaitingData, service.NoCachedData) as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
