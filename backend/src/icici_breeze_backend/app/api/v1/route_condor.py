"""Dynamic Iron Condors (docs/dynamic-iron-condor-plan.md).

Mounted at `/api/condor`, so nginx's `/api/` prefix already proxies it and `next.config.js`
needs one `/api/condor/:path*` rewrite. Backtests run in the shared backtest job slot;
`/bots/backtest/job` and `/bots/backtest/cancel` report and stop them, as they do the bots'.
Nothing under `/api/condor/backtest` places an order, so read-only licence mode does not
block it.
"""
from __future__ import annotations

import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, ValidationError

from icici_breeze_backend.app.auth.context import RequestContext, get_request_context
from icici_breeze_backend.app.domain.condor import CondorSettings
from icici_breeze_backend.app.services.bots import backtest_jobs as jobs
from icici_breeze_backend.app.services.bots.scalping import backtest_regime as regime
from icici_breeze_backend.app.services.condor import backtest_job

router = APIRouter()

#: Shown in a new form; the ceiling is the user's to set, this is only a starting figure.
DEFAULT_CEILING_INR = 10_00_000


class BacktestStart(BaseModel):
    settings: dict
    from_date: datetime.date
    to_date: datetime.date
    exit_action: Literal["time_roll", "close"] = "time_roll"
    lots_per_tranche: Optional[int] = Field(None, ge=1, le=500)


@router.get("/backtest/defaults")
def defaults(ctx: RequestContext = Depends(get_request_context)):
    """The settings a new backtest form starts from, and the history it may cover."""
    return {
        "settings": CondorSettings(margin_ceiling_inr=DEFAULT_CEILING_INR).model_dump(mode="json"),
        "history_start": regime.HISTORY_START.isoformat(),
        "live": jobs.broker_live(),
    }


@router.post("/backtest/start")
def start(req: BacktestStart, ctx: RequestContext = Depends(get_request_context)):
    try:
        settings = CondorSettings(**req.settings)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail="; ".join(err["msg"] for err in e.errors())) from e
    try:
        return backtest_job.start(
            ctx.user_id, settings, req.from_date, req.to_date,
            exit_action=req.exit_action, lots_per_tranche=req.lots_per_tranche,
        )
    except jobs.Busy as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except jobs.OutOfMemory as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/backtest/runs")
def runs(ctx: RequestContext = Depends(get_request_context)):
    jobs.ensure_store()
    return {"runs": backtest_job.list_runs(ctx.user_id)}


@router.get("/backtest/run")
def run(id: str = Query(...), ctx: RequestContext = Depends(get_request_context)):
    jobs.ensure_store()
    found = backtest_job.get_run(id, ctx.user_id)
    if found is None:
        raise HTTPException(status_code=404, detail="No such condor backtest run.")
    return found


# --------------------------------------------------------------------------------------
# Campaigns (plan sections 2 and 6). Nothing here places an order: creating, adopting,
# assigning and closing only change the ledger, so read-only licence mode does not block them.
# Handlers that read the broker are plain `def`s, run on the threadpool (#61).
# --------------------------------------------------------------------------------------


class CampaignCreate(BaseModel):
    settings: dict
    expiry: Optional[str] = None
    adopt: bool = False


class ContractRef(BaseModel):
    strike: float
    right: Literal["Call", "Put"]


class AssignRequest(ContractRef):
    price: float = Field(..., gt=0)


class CloseRequest(BaseModel):
    reason: str = Field("closed_by_user", max_length=60)


def _proc():
    from icici_breeze_backend.app.services.processor import processor

    return processor()


def _settings(raw: dict) -> CondorSettings:
    try:
        return CondorSettings(**raw)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail="; ".join(err["msg"] for err in e.errors())) from e


def _campaign_or_404(campaign_id: str, user_id: str):
    from icici_breeze_backend.app.repositories import condor as repo

    found = repo.get_campaign(campaign_id, user_id)
    if found is None:
        raise HTTPException(status_code=404, detail="No such campaign.")
    return found


def _campaign_json(c) -> dict:
    import dataclasses

    return {
        "id": c.id,
        "underlying": c.underlying,
        "origin": c.origin,
        "mode": c.mode,
        "status": c.status,
        "settings": c.settings.model_dump(mode="json"),
        "created_at": c.created_at,
        "closed_at": c.closed_at,
        "close_reason": c.close_reason,
        "cycle": dataclasses.asdict(c.cycle) if c.cycle else None,
        "cycles": [dataclasses.asdict(y) for y in c.cycles],
    }


@router.get("/campaigns")
def list_campaigns(include_closed: bool = Query(False), ctx: RequestContext = Depends(get_request_context)):
    from icici_breeze_backend.app.repositories import condor as repo

    return {"campaigns": [_campaign_json(c) for c in repo.list_campaigns(ctx.user_id, include_closed=include_closed)]}


@router.post("/campaigns")
def create_campaign(req: CampaignCreate, ctx: RequestContext = Depends(get_request_context)):
    from icici_breeze_backend.app.services.condor import campaigns

    try:
        c = campaigns.create(_proc(), ctx.user_id, _settings(req.settings), expiry=req.expiry, adopt=req.adopt)
    except campaigns.Refused as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return _campaign_json(c)


@router.get("/campaigns/{campaign_id}")
def get_campaign(campaign_id: str, ctx: RequestContext = Depends(get_request_context)):
    import dataclasses

    from icici_breeze_backend.app.repositories import condor as repo

    c = _campaign_or_404(campaign_id, ctx.user_id)
    return {
        **_campaign_json(c),
        "fills": [{**dataclasses.asdict(f), "cash": round(f.cash, 2)} for f in repo.list_fills(c.id)],
        "decisions": repo.list_decisions(c.id, limit=50),
        "last_scheduled": repo.last_scheduled_decision(c.id),
    }


@router.post("/campaigns/{campaign_id}/evaluate")
def evaluate_campaign(campaign_id: str, ctx: RequestContext = Depends(get_request_context)):
    """Run the engine now, off-schedule. Shown on the card, labelled, never acted on and not
    recorded: the card polls this."""
    from icici_breeze_backend.app.services.condor import campaigns

    c = _campaign_or_404(campaign_id, ctx.user_id)
    return campaigns.evaluate(_proc(), c, "on_demand")


@router.put("/campaigns/{campaign_id}/settings")
def update_settings(campaign_id: str, body: dict, ctx: RequestContext = Depends(get_request_context)):
    from icici_breeze_backend.app.repositories import condor as repo

    c = _campaign_or_404(campaign_id, ctx.user_id)
    settings = _settings(body)
    if c.origin == "bot" and settings != c.settings:
        # The bot runs only on its own settings (its evidence is matched on them, #66), so a
        # campaign given other settings is no longer the bot's: it comes back to the user, as
        # it does when the bot's own settings change (#68).
        from icici_breeze_backend.app.services.condor import bot as condor_bot

        condor_bot.release(c, "settings_changed")
    repo.update_settings(c.id, ctx.user_id, settings)
    return _campaign_json(_campaign_or_404(campaign_id, ctx.user_id))


@router.post("/campaigns/{campaign_id}/assign")
def assign(campaign_id: str, req: AssignRequest, ctx: RequestContext = Depends(get_request_context)):
    from icici_breeze_backend.app.services.condor import campaigns

    c = _campaign_or_404(campaign_id, ctx.user_id)
    try:
        campaigns.assign(_proc(), c, req.strike, req.right, req.price)
    except campaigns.Refused as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return {"assigned": True}


@router.post("/campaigns/{campaign_id}/leave-out")
def leave_out(campaign_id: str, req: ContractRef, ctx: RequestContext = Depends(get_request_context)):
    from icici_breeze_backend.app.services.condor import campaigns

    c = _campaign_or_404(campaign_id, ctx.user_id)
    try:
        campaigns.leave_out(_proc(), c, req.strike, req.right)
    except campaigns.Refused as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return {"left_out": True}


@router.post("/campaigns/{campaign_id}/close")
def close_campaign(campaign_id: str, req: CloseRequest, ctx: RequestContext = Depends(get_request_context)):
    """Stop managing: the campaign ends, its ledger is kept, nothing is traded. Closing the
    legs themselves is a ticket (step 4), not this."""
    from icici_breeze_backend.app.repositories import condor as repo

    c = _campaign_or_404(campaign_id, ctx.user_id)
    if c.origin == "bot":
        from icici_breeze_backend.app.services.condor import bot as condor_bot

        condor_bot.pause(ctx.user_id, "you stopped managing its campaign.")
    repo.close_campaign(c.id, ctx.user_id, req.reason)
    return _campaign_json(_campaign_or_404(campaign_id, ctx.user_id))


# --------------------------------------------------------------------------------------
# Tickets and execution (plan sections 4 and 6a)
# --------------------------------------------------------------------------------------


class TicketTemplate(BaseModel):
    kind: str
    params: dict = Field(default_factory=dict)


class TicketBody(BaseModel):
    kind: str = "blank"
    orders: list[dict]
    note: Optional[str] = Field(None, max_length=500)


def _suggested_kind(kind: str, orders: list) -> str:
    """A suggestion is booked by what it does, so the ledger reads entry / roll / exit."""
    if kind != "suggested":
        return kind
    if all(not o.opening for o in orders):
        return "suggested_close"
    if all(o.opening for o in orders):
        return "suggested_entry"
    return "suggested_roll"


@router.post("/campaigns/{campaign_id}/ticket/template")
def ticket_template(campaign_id: str, req: TicketTemplate, ctx: RequestContext = Depends(get_request_context)):
    from icici_breeze_backend.app.services.condor import tickets

    c = _campaign_or_404(campaign_id, ctx.user_id)
    try:
        return tickets.template(_proc(), c, req.kind, req.params)
    except tickets.Refused as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.post("/campaigns/{campaign_id}/ticket/preview")
def ticket_preview(campaign_id: str, req: TicketBody, ctx: RequestContext = Depends(get_request_context)):
    from icici_breeze_backend.app.services.condor import tickets

    c = _campaign_or_404(campaign_id, ctx.user_id)
    try:
        return tickets.preview(_proc(), c, req.orders, req.kind)
    except tickets.Refused as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.post("/campaigns/{campaign_id}/ticket/execute")
def ticket_execute(campaign_id: str, req: TicketBody, ctx: RequestContext = Depends(get_request_context)):
    """Places the ticket, one order at a time, in the background; poll the execution.

    Read-only licence mode blocks a ticket that opens anything and lets one that only closes
    through (#57). The warnings the user overrode are re-derived on fresh prices and kept on
    the execution row."""
    from icici_breeze_backend.app.api.deps_license import require_trading_not_revoked
    from icici_breeze_backend.app.services.condor import executor, tickets

    c = _campaign_or_404(campaign_id, ctx.user_id)
    proc = _proc()
    try:
        sequenced, _ = executor.plan(proc, c, req.orders)
        if c.origin == "bot":
            # The user always wins: the bot stops deciding until resumed, so it cannot undo
            # this change at its next check (plan section 6a).
            from icici_breeze_backend.app.services.condor import bot as condor_bot

            condor_bot.pause(ctx.user_id, "you executed a manual ticket on its campaign.")
        if executor.opens_anything(sequenced):
            require_trading_not_revoked()
        warnings = tickets.preview(proc, c, req.orders, req.kind, with_margin=False)["warnings"]
        return executor.start(
            proc, c, req.orders, kind=_suggested_kind(req.kind, sequenced), note=req.note, warnings=warnings,
        )
    except (executor.Refused, tickets.Refused) as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.get("/campaigns/{campaign_id}/executions")
def executions(campaign_id: str, ctx: RequestContext = Depends(get_request_context)):
    from icici_breeze_backend.app.repositories import condor as repo

    c = _campaign_or_404(campaign_id, ctx.user_id)
    return {"executions": repo.list_executions(c.id)}


@router.get("/campaigns/{campaign_id}/executions/{execution_id}")
def execution(campaign_id: str, execution_id: str, ctx: RequestContext = Depends(get_request_context)):
    from icici_breeze_backend.app.repositories import condor as repo

    c = _campaign_or_404(campaign_id, ctx.user_id)
    found = repo.get_execution(execution_id, c.id)
    if found is None:
        raise HTTPException(status_code=404, detail="No such execution.")
    return found



# --------------------------------------------------------------------------------------
# The bot (plan section 7)
# --------------------------------------------------------------------------------------


@router.get("/bot")
def bot_overview(ctx: RequestContext = Depends(get_request_context)):
    """What the bot card shows: which modes the evidence unlocks, and the bot's campaign."""
    from icici_breeze_backend.app.db.bots_migrate import BOT_DYNAMIC_CONDOR
    from icici_breeze_backend.app.repositories import bots as bots_repo
    from icici_breeze_backend.app.repositories import condor as repo
    from icici_breeze_backend.app.services.condor import bot as condor_bot

    record = bots_repo.get_or_create_bot(ctx.user_id, BOT_DYNAMIC_CONDOR)
    cfg = condor_bot.config_of(record.config)
    camp = repo.bot_campaign(ctx.user_id)
    waiting = None
    if record.enabled and not cfg.paused and camp is None:
        from icici_breeze_backend.app.services.condor import live

        _, waiting = condor_bot.waiting_reason(_proc(), ctx.user_id, cfg, datetime.datetime.now(live.IST).date())
    return {
        "eligibility": condor_bot.eligibility(ctx.user_id, cfg).as_dict(),
        "campaign": _campaign_json(camp) if camp else None,
        "waiting": waiting,
    }


def _bot_config(user_id: str):
    from icici_breeze_backend.app.db.bots_migrate import BOT_DYNAMIC_CONDOR
    from icici_breeze_backend.app.repositories import bots as bots_repo
    from icici_breeze_backend.app.services.condor import bot as condor_bot

    return condor_bot.config_of(bots_repo.get_or_create_bot(user_id, BOT_DYNAMIC_CONDOR).config)


@router.get("/bot/entry")
def bot_entry(ctx: RequestContext = Depends(get_request_context)):
    """The card's play button: a first tranche at the bot's saved settings, for Basket Orders to
    pre-fill (#67). Lots are the saved lots per tranche, else today's margin, else one lot with a
    note saying why; the user edits all of it before anything is placed."""
    from icici_breeze_backend.app.services.condor import campaigns
    from icici_breeze_backend.app.services.condor.backtest_job import SizingError, size_from_margin

    cfg = _bot_config(ctx.user_id)
    try:
        out = campaigns.entry_proposal(_proc(), ctx.user_id, cfg.campaign)
    except campaigns.Refused as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    lots, sizing = cfg.lots_per_tranche, "lots per tranche from the bot's settings"
    if not lots:
        try:
            lots, sizing = size_from_margin(cfg.campaign, ctx.user_id)["lots"], "sized from today's margin"
        except SizingError as e:
            lots, sizing = 1, f"one lot: {e}"
        except Exception:  # noqa: BLE001 -- sizing is a convenience; the basket still opens
            lots, sizing = 1, "one lot: today's margin could not size a tranche"
    return {**out, "lots": lots, "sizing": sizing}


class CampaignOpen(BaseModel):
    expiry: str
    orders: list[dict]
    note: Optional[str] = Field(None, max_length=500)


@router.post("/campaigns/open")
def open_campaign(req: CampaignOpen, ctx: RequestContext = Depends(get_request_context)):
    """Basket Orders' "Manage as a Dynamic Iron Condor campaign" (#67): a new manual campaign on
    the bot's saved settings, whose first tranche is the basket, placed by the campaign executor
    one order at a time, wings first. Poll the execution as the Adjust ticket does."""
    from icici_breeze_backend.app.api.deps_license import require_trading_not_revoked
    from icici_breeze_backend.app.services.condor import executor, tickets

    require_trading_not_revoked()
    try:
        campaign, execution = executor.open_campaign(
            _proc(), ctx.user_id, _bot_config(ctx.user_id).campaign, req.expiry, req.orders, note=req.note,
        )
    except (executor.Refused, tickets.Refused) as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return {"campaign": _campaign_json(campaign), "execution": execution}


@router.get("/campaigns/{campaign_id}/handover")
def handover_preview(campaign_id: str, ctx: RequestContext = Depends(get_request_context)):
    """What handing this campaign to the bot would change, and what still stops it (#68)."""
    from icici_breeze_backend.app.services.condor import bot as condor_bot

    return condor_bot.handover_preview(_proc(), _campaign_or_404(campaign_id, ctx.user_id))


@router.post("/campaigns/{campaign_id}/handover")
def handover(campaign_id: str, ctx: RequestContext = Depends(get_request_context)):
    """Hand a manual campaign to the bot. Nothing is traded: the bot acts from its next check, on
    its own settings, in its current mode, and a manual ticket or switching it off hands it back."""
    from icici_breeze_backend.app.services.condor import bot as condor_bot

    try:
        c = condor_bot.hand_over(_proc(), _campaign_or_404(campaign_id, ctx.user_id))
    except condor_bot.Refused as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return _campaign_json(c)
