"""The Signals page's API (docs/signals-streamline-plan.md section 4 and 6).

Mounted under `/api/signals` so the `/signals` page itself is never proxied: one `next.config.js`
rewrite covers every route here, and nginx already sends `/api/*` to the backend.

Nothing here places an order, so read-only licence mode does not block it. Backtest fetching is
refused in market hours and off the live broker; see `index_signal/backtest.py`.
"""
from __future__ import annotations

import datetime
import os
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from icici_breeze_backend.app.auth.context import RequestContext, get_request_context
from icici_breeze_backend.app.services.index_signal import backtest as signal_backtest
from icici_breeze_backend.app.services.index_signal import gate, reader
from icici_breeze_backend.app.services.index_signal import settings as signal_settings
from icici_breeze_backend.app.services.index_signal.mechanisms import (
    DURATIONS,
    INDEX_NAMES,
    INDICES,
    MECHANISM_NAMES,
    MECHANISM_SUMMARIES,
    MECHANISMS,
    NAVBAR_DURATION,
    VERSIONS,
    SeriesKey,
)

router = APIRouter()

_READING_FIELDS = (
    "state", "reason", "signal", "call_started_at", "held_until", "computed_at", "evaluated_at",
    "thin_data", "uses_oi",
)
_SUMMARY_FIELDS = (
    "sessions_replayed", "calls", "bullish_calls", "bearish_calls", "right", "wrong", "hit_rate",
    "verdict", "sides", "breakeven_bps", "rough_pnl_one_lot_rupees", "withdrawn_early",
    "directional_share", "up_days", "down_days", "mean_daily_correlation",
)


class NavbarBody(BaseModel):
    mechanism: Literal["expansion", "momentum"]


class BacktestBody(BaseModel):
    period: Literal["last_day", "last_week", "last_month", "custom"]
    from_date: Optional[datetime.date] = None
    to_date: Optional[datetime.date] = None


def _series_summary(summary: dict[str, Any], duration: int) -> dict[str, Any]:
    out = {k: summary.get(k) for k in _SUMMARY_FIELDS}
    out["mean_move_bps"] = summary.get(f"mean_move_{duration}m_bps")
    return out


def _job() -> Optional[dict[str, Any]]:
    from icici_breeze_backend.app.services.bots import backtest_jobs as jobs

    state = jobs.state()
    if state is None:
        return None
    state = dict(state)
    state["log"] = list(state.get("log") or [])[-40:]
    return state


@router.get("")
def signals_overview(ctx: RequestContext = Depends(get_request_context)) -> dict[str, Any]:
    from icici_breeze_backend.app.services.index_signal import publisher

    runs = signal_backtest.completed_runs()
    latest = runs[0] if runs else None
    latest_summary = (latest or {}).get("summary") or {}
    mechanisms = []
    for mechanism in MECHANISMS:
        series = []
        for duration in DURATIONS:
            for index in INDICES:
                key = SeriesKey(mechanism, duration, index)
                reading = reader.get_signal(key)
                s = latest_summary.get(key.id)
                series.append({
                    **key.to_dict(),
                    "index_name": INDEX_NAMES[index],
                    "reading": {f: reading.get(f) for f in _READING_FIELDS},
                    "last_backtest": _series_summary(s, duration) if isinstance(s, dict) else None,
                })
        mechanisms.append({
            "id": mechanism,
            "name": MECHANISM_NAMES[mechanism],
            "summary": MECHANISM_SUMMARIES[mechanism],
            "version": VERSIONS[mechanism],
            "availability": gate.mechanism_availability(mechanism),
            "series": series,
        })
    return {
        "mechanisms": mechanisms,
        "navbar_mechanism": signal_settings.navbar_mechanism(),
        "navbar_duration": NAVBAR_DURATION,
        "gate_days": gate.GATE_DAYS,
        "last_backtest": None if latest is None else {
            "id": latest["id"],
            "from": latest["from_date"],
            "to": latest["to_date"],
            "range_days": latest["range_days"],
            "finished_at": latest.get("finished_at"),
        },
        "live": publisher.status(),
        "job": _job(),
    }


@router.put("/navbar")
def set_navbar(body: NavbarBody, ctx: RequestContext = Depends(get_request_context)) -> dict[str, Any]:
    return {"navbar_mechanism": signal_settings.set_navbar_mechanism(body.mechanism)}


@router.post("/backtest")
def start_backtest(body: BacktestBody, ctx: RequestContext = Depends(get_request_context)) -> dict[str, Any]:
    from icici_breeze_backend.app.services.bots import backtest_jobs as jobs

    try:
        return signal_backtest.start(ctx.user_id, body.period, body.from_date, body.to_date)
    except jobs.Busy as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/backtest/job")
def backtest_job(ctx: RequestContext = Depends(get_request_context)) -> dict[str, Any]:
    return {"job": _job()}


@router.post("/backtest/cancel")
def cancel_backtest(ctx: RequestContext = Depends(get_request_context)) -> dict[str, Any]:
    from icici_breeze_backend.app.services.bots import backtest_jobs as jobs

    return {"stopping": jobs.cancel()}


@router.get("/backtest/runs")
def backtest_runs(ctx: RequestContext = Depends(get_request_context)) -> dict[str, Any]:
    runs = []
    for run in signal_backtest.list_runs(limit=100):
        summary = run.get("summary") or {}
        runs.append({
            "id": run["id"],
            "triggered_at": run["triggered_at"],
            "finished_at": run.get("finished_at"),
            "period": run["period"],
            "from": run["from_date"],
            "to": run["to_date"],
            "range_days": run.get("range_days"),
            "status": run["status"],
            "error": run.get("error"),
            "notes": run.get("notes") or [],
            "calls": run.get("calls") or 0,
            "has_zip": run.get("has_zip", False),
            "versions": run.get("versions") or {},
            "counts_for_gate": (
                run["status"] == "completed" and (run.get("range_days") or 0) >= gate.GATE_DAYS
            ),
            "series": {
                sid: {"calls": s.get("calls"), "hit_rate": s.get("hit_rate"), "verdict": s.get("verdict")}
                for sid, s in summary.items() if isinstance(s, dict)
            },
        })
    return {"runs": runs, "gate_days": gate.GATE_DAYS}


@router.get("/backtest/runs/{run_id}/zip")
def backtest_zip(run_id: str, ctx: RequestContext = Depends(get_request_context)):
    run = signal_backtest.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="No such signal backtest.")
    path = run.get("zip_path")
    if not path or not os.path.exists(str(path)):
        raise HTTPException(status_code=404, detail="This backtest's files are no longer kept.")
    return FileResponse(str(path), media_type="application/zip", filename=os.path.basename(str(path)))
