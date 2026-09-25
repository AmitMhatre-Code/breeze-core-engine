"""Settings -> Storage: how full the data volume is, what fills it, and deleting by date (#44).

Mounted at `/api/settings/storage`, so the existing `/api/settings/:path*` rewrite and nginx's
`/api/` block already reach it -- no proxy entries to add.

`/status` is what the app-wide banner polls: one `statvfs` and one settings read, cheap enough for
every open tab. `/` is the screen's full inventory and can take seconds on a large backtest cache
the first time (it scans the option candles once, then memoises until the file changes).
"""
from __future__ import annotations

import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from icici_breeze_backend.app.api.deps import get_current_user
from icici_breeze_backend.app.auth.context import RequestContext
from icici_breeze_backend.app.services.storage import cleanup, elements, usage

router = APIRouter()


class ThresholdBody(BaseModel):
    threshold_pct: int


class DeleteBody(BaseModel):
    element: str
    from_date: datetime.date
    to_date: datetime.date


def _bounds() -> dict[str, int]:
    return {
        "min": usage.MIN_THRESHOLD_PCT,
        "max": usage.MAX_THRESHOLD_PCT,
        "default": usage.DEFAULT_THRESHOLD_PCT,
    }


@router.get("/status")
def storage_status(_: RequestContext = Depends(get_current_user)) -> dict[str, Any]:
    return usage.status()


@router.get("")
def storage_inventory(_: RequestContext = Depends(get_current_user)) -> dict[str, Any]:
    return {**elements.inventory(), "threshold_bounds": _bounds(), "job": cleanup.state()}


@router.put("/threshold")
def storage_threshold(body: ThresholdBody, _: RequestContext = Depends(get_current_user)) -> dict[str, Any]:
    try:
        usage.set_threshold_pct(body.threshold_pct)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return usage.status()


@router.post("/delete")
def storage_delete(body: DeleteBody, _: RequestContext = Depends(get_current_user)) -> dict[str, Any]:
    try:
        rng = elements.DateRange(body.from_date, body.to_date)
        return {"job": cleanup.start(body.element, rng)}
    except cleanup.Busy as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/job")
def storage_job(_: RequestContext = Depends(get_current_user)) -> dict[str, Any]:
    return {"job": cleanup.state()}
