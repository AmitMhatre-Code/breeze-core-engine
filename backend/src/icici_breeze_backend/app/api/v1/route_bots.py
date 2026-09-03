"""Bots section — configuration, run log, and proposals (docs/bots-mvp-plan.md).

Scan and approve endpoints live with their bot's engine; this module owns the surface
that is common to every bot, so adding a third bot needs no new routes here.

Every path here is **static**, with `bot_type` passed as a query parameter rather than as
`/bots/{bot_type}`. That is deliberate. The app proxies by enumerating exact backend paths
(`next.config.js` rewrites, mirrored in the nginx confs), and `/bots` is also a frontend
page. A dynamic `/bots/:botType` rewrite would have to be declared to reach this router,
and it would then swallow every frontend sub-page — `/bots/holdings_writer` would proxy to
the backend instead of rendering the bot's detail screen. Static API paths keep the page
namespace and the API namespace disjoint, which is the same shape `/strategy-builder` and
`/uncovered-shorts` already use.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from icici_breeze_backend.app.api.deps_license import require_trading_not_revoked
from icici_breeze_backend.app.auth.context import RequestContext, get_request_context
from icici_breeze_backend.app.db.bots_migrate import (
    BOT_EXPIRY_INDEX_WRITER,
    BOT_HOLDINGS_WRITER,
    BOT_TYPES,
)
from icici_breeze_backend.app.domain.bots import (
    ApprovalResult,
    ApproveProposalRequest,
    BotRecord,
    BotRunRecord,
    ExpiryIndexWriterConfig,
    HoldingRow,
    HoldingsWriterConfig,
    ProposalLeg,
    RepriceRequest,
    ProposalRecord,
    ReasonCode,
    ScanResponse,
    UpdateBotRequest,
    UpdateScripPrefsRequest,
    ScripPref,
)
from icici_breeze_backend.app.repositories import bots as repo
from icici_breeze_backend.app.services.bots import proposals
from icici_breeze_backend.audit.logger import AuditLogger, OperationType

_logger = logging.getLogger(__name__)

router = APIRouter()


def _validate_bot_type(bot_type: str) -> str:
    if bot_type not in BOT_TYPES:
        raise HTTPException(status_code=404, detail=f"Unknown bot: {bot_type}")
    return bot_type


@router.get("/list", response_model=list[BotRecord])
async def list_bots(ctx: RequestContext = Depends(get_request_context)):
    return repo.list_bots(ctx.user_id)


@router.get("/runs", response_model=list[BotRunRecord])
async def list_runs(
    bot_type: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    ctx: RequestContext = Depends(get_request_context),
):
    """The shared cross-bot run log. Unfiltered by default -- the point of the log is that
    a user can see every bot's activity, including the days nothing happened, in one place."""
    if bot_type is not None:
        _validate_bot_type(bot_type)
    return repo.list_runs(ctx.user_id, bot_type=bot_type, limit=limit)


@router.get("/scrip-prefs", response_model=list[ScripPref])
async def list_scrip_prefs(ctx: RequestContext = Depends(get_request_context)):
    """Only deviations from policy are stored, so an empty list is the normal state and
    means "every holding follows the defaults", not "nothing is configured"."""
    return repo.list_scrip_prefs(ctx.user_id)


@router.put("/scrip-prefs", response_model=list[ScripPref])
async def update_scrip_prefs(
    payload: UpdateScripPrefsRequest,
    ctx: RequestContext = Depends(get_request_context),
):
    return repo.upsert_scrip_prefs(ctx.user_id, payload.prefs)


@router.get("/config", response_model=BotRecord)
async def get_bot(
    bot_type: str = Query(...), ctx: RequestContext = Depends(get_request_context)
):
    return repo.get_or_create_bot(ctx.user_id, _validate_bot_type(bot_type))


@router.patch("/config", response_model=BotRecord)
async def update_bot(
    payload: UpdateBotRequest,
    bot_type: str = Query(...),
    ctx: RequestContext = Depends(get_request_context),
    _: None = Depends(require_trading_not_revoked),
):
    """Guarded by the license check even though it places no orders: enabling a bot is
    arming something that will trade later, so it must be refused in read-only mode rather
    than accepted and then silently skipped every run."""
    _validate_bot_type(bot_type)
    if payload.config is not None:
        # Validate the *incoming* blob strictly. The repository is forgiving when reading
        # stored config (so an old blob still loads); a user submitting a bad value must be
        # told, not silently reset to defaults.
        from icici_breeze_backend.app.repositories.bots import _CONFIG_MODEL

        model = _CONFIG_MODEL[bot_type]
        current = repo.get_or_create_bot(ctx.user_id, bot_type).config
        merged = {**current, **payload.config}
        try:
            model(**merged)
        except Exception as e:  # noqa: BLE001 -- surfaced to the user as a 400
            raise HTTPException(status_code=400, detail=f"Invalid bot configuration: {e}") from e

    before = repo.get_or_create_bot(ctx.user_id, bot_type)
    updated = repo.update_bot(
        ctx.user_id,
        bot_type,
        enabled=payload.enabled,
        priority=payload.priority,
        config=payload.config,
    )
    if payload.enabled is not None and payload.enabled != before.enabled:
        AuditLogger(None).log_operation(
            ctx.user_id,
            OperationType.BOT_ENABLED if updated.enabled else OperationType.BOT_DISABLED,
            "Bot",
            bot_type,
        )
    if payload.config:
        AuditLogger(None).log_operation(
            ctx.user_id, OperationType.BOT_CONFIG_UPDATED, "Bot", bot_type
        )
    return updated


@router.get("/proposal", response_model=Optional[ProposalRecord])
async def get_pending_proposal(
    bot_type: str = Query(...), ctx: RequestContext = Depends(get_request_context)
):
    """Returns null when there is nothing to approve. Expired proposals are retired on
    read, so a stale set of prices can never come back as actionable."""
    return repo.get_pending_proposal(ctx.user_id, _validate_bot_type(bot_type))


@router.post("/proposal/reject", response_model=Optional[ProposalRecord])
async def reject_pending_proposal(
    bot_type: str = Query(...), ctx: RequestContext = Depends(get_request_context)
):
    _validate_bot_type(bot_type)
    pending = repo.get_pending_proposal(ctx.user_id, bot_type)
    if pending is None:
        raise HTTPException(status_code=404, detail="No proposal awaiting approval.")
    AuditLogger(None).log_operation(
        ctx.user_id, OperationType.BOT_PROPOSAL_REJECTED, "BotProposal", pending.id
    )
    return repo.resolve_proposal(
        ctx.user_id, pending.id, status="rejected", note="Dismissed by the user."
    )


# --------------------------------------------------------------------------------------
# Bot 1 — Holdings Option Writer: scan and approve
# --------------------------------------------------------------------------------------

def _run_scan(user_id: str, trigger: str):
    """Thin wrapper over the shared runner, translating its failures into HTTP.

    The orchestration itself lives in `services/bots/holdings_runner` so the unattended
    path cannot drift from this one.
    """
    from icici_breeze_backend.app.services.bots import holdings_runner, holdings_writer

    try:
        return holdings_runner.run_scan(user_id, trigger)
    except holdings_writer.BotScanError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail="The scan failed unexpectedly.") from e


@router.post("/scan", response_model=ScanResponse)
async def scan_bot(
    bot_type: str = Query(...),
    ctx: RequestContext = Depends(get_request_context),
    _: None = Depends(require_trading_not_revoked),
):
    """Run a scan now and produce a proposal. Only Bot 1 scans; Bot 2 fires on a schedule."""
    _validate_bot_type(bot_type)
    if bot_type != BOT_HOLDINGS_WRITER:
        raise HTTPException(status_code=400, detail="This bot does not support manual scans.")
    run_id, proposal, skipped, warnings = _run_scan(ctx.user_id, "manual")
    return ScanResponse(
        run_id=run_id, proposal=proposal, skipped=skipped, warnings=warnings
    )


@router.post("/proposal/approve", response_model=ApprovalResult)
async def approve_proposal(
    payload: ApproveProposalRequest,
    bot_type: str = Query(...),
    ctx: RequestContext = Depends(get_request_context),
    _: None = Depends(require_trading_not_revoked),
):
    """Place the approved legs, after confirming their prices still hold.

    The rules themselves live in `services/bots/proposals` because the Telegram approval
    path shares them; this handler only turns a refusal back into the HTTP status it has
    always returned.
    """
    _validate_bot_type(bot_type)
    try:
        return proposals.approve(ctx.user_id, bot_type, payload)
    except proposals.ApprovalRefused as e:
        raise HTTPException(status_code=e.status_code, detail=e.message) from e


# --------------------------------------------------------------------------------------
# The settings drawer's scrip list
# --------------------------------------------------------------------------------------


@router.get("/holdings", response_model=list[HoldingRow])
async def list_holdings(ctx: RequestContext = Depends(get_request_context)):
    """Live holdings, F&O eligibility resolved, for Bot 1's per-scrip settings.

    Read on every open rather than stored: holdings change without the bot being told, and
    configuring lots against a scrip the user sold last week is worse than showing nothing.
    """
    from icici_breeze_backend.app.services.bots import holdings_writer
    from icici_breeze_backend.app.services.processor import processor

    bot = repo.get_or_create_bot(ctx.user_id, BOT_HOLDINGS_WRITER)
    config = HoldingsWriterConfig(**bot.config)
    try:
        rows = holdings_writer.list_holdings(processor(), ctx.user_id, config)
    except holdings_writer.BotScanError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return [HoldingRow(**row) for row in rows]


# --------------------------------------------------------------------------------------
# Bot 2 — manual run
# --------------------------------------------------------------------------------------


def _index_expiring_today(proc, index_code: str) -> Optional[str]:
    from icici_breeze_backend.app.services.bots import scheduler

    return scheduler._expiring_today(proc).get(index_code)


@router.post("/plan", response_model=ScanResponse)
async def plan_bot(
    bot_type: str = Query(...),
    ctx: RequestContext = Depends(get_request_context),
    _: None = Depends(require_trading_not_revoked),
):
    """Size Bot 2's trade for today without placing it — the manual run's first step.

    Deliberately allowed on any day. Off an expiry day it reports that nothing expires,
    rather than the button being greyed out with no explanation: "is it broken?" is the
    single most common question a disabled control produces.
    """
    _validate_bot_type(bot_type)
    if bot_type != BOT_EXPIRY_INDEX_WRITER:
        raise HTTPException(
            status_code=400, detail="This bot plans through a scan, not a plan."
        )

    from icici_breeze_backend.app.services.bots import expiry_index_writer as bot2
    from icici_breeze_backend.app.services.processor import processor

    proc = processor()
    bot = repo.get_or_create_bot(ctx.user_id, BOT_EXPIRY_INDEX_WRITER)
    config = ExpiryIndexWriterConfig(**bot.config)
    run_id = repo.start_run(ctx.user_id, BOT_EXPIRY_INDEX_WRITER, "manual")

    enabled = [code for code, leg in config.indices.items() if leg.enabled]
    if not enabled:
        repo.finish_run(
            run_id,
            status="skipped",
            reason_code=ReasonCode.NOTHING_ELIGIBLE,
            reason_text="No index is enabled on this bot.",
        )
        return ScanResponse(run_id=run_id, proposal=None, skipped=[], warnings=[])

    expiring = {}
    for code in enabled:
        display = _index_expiring_today(proc, code)
        if display:
            expiring[code] = display
    if not expiring:
        repo.finish_run(
            run_id,
            status="skipped",
            reason_code=ReasonCode.NOT_AN_EXPIRY_DAY,
            reason_text="Neither NIFTY nor SENSEX expires today.",
        )
        return ScanResponse(
            run_id=run_id,
            proposal=None,
            skipped=[
                {
                    "stock_code": bot2.INDEX_LABEL.get(c, c),
                    "reason_code": ReasonCode.NOT_AN_EXPIRY_DAY,
                    "reason": "Does not expire today.",
                }
                for c in enabled
            ],
            warnings=[],
        )

    available = bot2._available_margin(proc, ctx.user_id)
    if not available or available <= 0:
        repo.finish_run(
            run_id,
            status="failed",
            reason_code=ReasonCode.BROKER_ERROR,
            reason_text="Could not read available margin from the broker.",
        )
        raise HTTPException(status_code=502, detail="Could not read available margin.")

    margin_source = proc.get_strategy_builder_margin_source(ctx.user_id)
    # Priority order, same as the unattended sweep, so the manual review shows what the bot
    # would actually have done rather than a differently-ordered version of it.
    ordered = sorted(expiring, key=lambda c: config.indices[c].priority)
    legs: list[ProposalLeg] = []
    skipped: list[dict] = []
    for code in ordered:
        plan = bot2.plan_index(
            proc,
            ctx.user_id,
            code,
            expiry_display=expiring[code],
            config=config,
            available_margin=available,
            margin_source=margin_source,
        )
        if plan.error or not plan.legs:
            skipped.append(
                {
                    "stock_code": bot2.INDEX_LABEL.get(code, code),
                    "reason_code": plan.reason_code or ReasonCode.NOTHING_ELIGIBLE,
                    "reason": plan.error or "Nothing to trade.",
                }
            )
            continue
        legs.extend(proposals.plan_to_legs(plan, code))

    if not legs:
        repo.finish_run(
            run_id,
            status="skipped",
            reason_code=ReasonCode.NOTHING_ELIGIBLE,
            reason_text="Nothing could be sized today.",
            detail={"skipped": skipped},
        )
        return ScanResponse(run_id=run_id, proposal=None, skipped=skipped, warnings=[])

    proposal = repo.create_proposal(
        run_id=run_id,
        user_id=ctx.user_id,
        bot_type=BOT_EXPIRY_INDEX_WRITER,
        legs=legs,
        totals=proposals.index_totals(legs),
        ttl_minutes=5,
    )
    repo.finish_run(
        run_id,
        status="proposed",
        reason_code=ReasonCode.PROPOSAL_READY,
        reason_text=f"{len(legs)} leg(s) proposed for review.",
        detail={"skipped": skipped, "proposal_id": proposal.id},
    )
    return ScanResponse(run_id=run_id, proposal=proposal, skipped=skipped, warnings=[])


@router.post("/proposal/reprice", response_model=ProposalRecord)
async def reprice_proposal(
    payload: RepriceRequest,
    bot_type: str = Query(...),
    ctx: RequestContext = Depends(get_request_context),
):
    """Re-price the pending proposal with the user's edits applied. Places nothing.

    The manual review shows margin and premium *before* the user commits, and a margin
    number is not linear in lot count -- it comes from the broker, not from multiplication.
    So an edited size has to go back to the source rather than being scaled in the browser,
    or the figure the user decides on is one nobody ever quoted.
    """
    _validate_bot_type(bot_type)
    pending = repo.get_pending_proposal(ctx.user_id, bot_type)
    if pending is None:
        raise HTTPException(status_code=404, detail="No proposal to re-price.")

    from icici_breeze_backend.app.services.processor import processor

    proc = processor()
    if bot_type == BOT_EXPIRY_INDEX_WRITER:
        # A distance edit re-picks the strike, and a strangle's two sides net against each
        # other, so Bot 2 re-prices the whole proposal at once rather than leg by leg.
        legs = proposals.reprice_index_legs(proc, ctx.user_id, pending, payload.edits)
    else:
        legs = []
        for index, leg in enumerate(pending.legs):
            edit = payload.edits.get(index)
            if edit is None or (
                edit.lots is None
                and edit.strike_price is None
                and edit.distance_pct is None
            ):
                legs.append(leg)
                continue
            priced = proposals.price_edited_leg(ctx.user_id, leg, edit, {})
            legs.append(priced or leg)

    if payload.leg_indexes:
        wanted = set(payload.leg_indexes)
        for index, leg in enumerate(legs):
            leg.selected = index in wanted

    bot = repo.get_or_create_bot(ctx.user_id, bot_type)
    totals = (
        proposals.index_totals(legs)
        if bot_type == BOT_EXPIRY_INDEX_WRITER
        else proposals.holdings_totals(legs, HoldingsWriterConfig(**bot.config))
    )
    return pending.model_copy(update={"legs": legs, "totals": totals})


