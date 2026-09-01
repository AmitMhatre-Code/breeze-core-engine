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
from icici_breeze_backend.app.db.bots_migrate import BOT_HOLDINGS_WRITER, BOT_TYPES
from icici_breeze_backend.app.domain.bots import (
    ApprovalResult,
    ApproveProposalRequest,
    BotRecord,
    BotRunRecord,
    HoldingsWriterConfig,
    PlacedLegResult,
    ProposalRecord,
    ReasonCode,
    ScanResponse,
    UpdateBotRequest,
    UpdateScripPrefsRequest,
    ScripPref,
)
from icici_breeze_backend.app.repositories import bots as repo
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
        ctx.user_id, bot_type, enabled=payload.enabled, config=payload.config
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

# A proposal is a priced snapshot. If the bid has moved more than this by the time the user
# approves, the orders would go out at prices they never agreed to — so re-scan and make
# them look again rather than filling on stale numbers.
MATERIAL_DRIFT_PCT = 10.0


def _leg_key(leg) -> tuple:
    return (leg.stock_code, leg.right, leg.expiry_display, round(float(leg.strike_price), 4))


def _run_scan(user_id: str, trigger: str):
    """Scan, and record the outcome in the run log whatever happens."""
    from icici_breeze_backend.app.services.bots import holdings_writer
    from icici_breeze_backend.app.services.processor import processor

    proc = processor()
    bot = repo.get_or_create_bot(user_id, BOT_HOLDINGS_WRITER)
    config = HoldingsWriterConfig(**bot.config)
    prefs = {p.stock_code: p for p in repo.list_scrip_prefs(user_id)}
    run_id = repo.start_run(user_id, BOT_HOLDINGS_WRITER, trigger)

    try:
        result = holdings_writer.scan(
            proc,
            user_id,
            config=config,
            prefs=prefs,
            margin_source=proc.get_strategy_builder_margin_source(user_id),
        )
    except holdings_writer.BotScanError as e:
        repo.finish_run(
            run_id, status="failed", reason_code=ReasonCode.BROKER_ERROR, reason_text=str(e)
        )
        raise HTTPException(status_code=502, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        _logger.exception("holdings-writer scan failed for user=%s", user_id)
        repo.finish_run(
            run_id,
            status="failed",
            reason_code=ReasonCode.INTERNAL_ERROR,
            reason_text="The scan failed unexpectedly.",
        )
        raise HTTPException(status_code=500, detail="The scan failed unexpectedly.") from e

    skipped = [
        {"stock_code": s.stock_code, "reason_code": s.reason_code, "reason": s.reason}
        for s in result.skipped
    ]
    if not result.legs:
        repo.finish_run(
            run_id,
            status="skipped",
            reason_code=ReasonCode.NOTHING_ELIGIBLE,
            reason_text=(
                f"No writable contracts found across {len(skipped)} holding(s)."
                if skipped
                else "No F&O-eligible holdings."
            ),
            detail={"skipped": skipped},
        )
        return run_id, None, skipped, result.warnings

    proposal = repo.create_proposal(
        run_id=run_id,
        user_id=user_id,
        bot_type=BOT_HOLDINGS_WRITER,
        legs=result.legs,
        totals=result.totals,
        ttl_minutes=config.proposal_ttl_minutes,
    )
    repo.finish_run(
        run_id,
        status="proposed",
        reason_code=ReasonCode.PROPOSAL_READY,
        reason_text=(
            f"{len(result.legs)} contract(s) proposed; "
            f"{len(skipped)} holding(s) produced nothing."
        ),
        detail={"skipped": skipped, "proposal_id": proposal.id},
    )
    return run_id, proposal, skipped, result.warnings


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

    Approval names the legs to keep — anything omitted is dropped, which is how the manual
    delivery-cash allocation is expressed.
    """
    _validate_bot_type(bot_type)
    if bot_type != BOT_HOLDINGS_WRITER:
        raise HTTPException(status_code=400, detail="This bot does not use proposals.")

    pending = repo.get_pending_proposal(ctx.user_id, bot_type)
    if pending is None:
        raise HTTPException(
            status_code=404, detail="No proposal awaiting approval — run a scan first."
        )
    try:
        chosen = [pending.legs[i] for i in sorted(set(payload.leg_indexes))]
    except IndexError as e:
        raise HTTPException(status_code=400, detail="Unknown leg in approval.") from e
    if not chosen:
        raise HTTPException(status_code=400, detail="No legs selected.")

    # Re-price before committing. A proposal that cannot be re-priced fails closed.
    _, fresh, _, _ = _run_scan(ctx.user_id, "manual")
    fresh_by_key = {_leg_key(leg): leg for leg in (fresh.legs if fresh else [])}
    drifted: list[str] = []
    indicative: list[str] = []
    repriced = []
    for leg in chosen:
        current = fresh_by_key.get(_leg_key(leg))
        if current is None:
            drifted.append(f"{leg.stock_code} {leg.strike_price:g} is no longer available")
            continue
        if current.premium_basis != "bid":
            # An indicative price is planning information, not something to sell into. There
            # is no order book outside market hours, so this is the honest stopping point.
            indicative.append(f"{leg.stock_code} {leg.strike_price:g}")
            continue
        if leg.premium_per_share > 0:
            move = (current.premium_per_share - leg.premium_per_share) / leg.premium_per_share
            if move * 100 <= -MATERIAL_DRIFT_PCT:
                drifted.append(
                    f"{leg.stock_code} {leg.strike_price:g} bid fell "
                    f"{abs(move) * 100:.1f}% to {current.premium_per_share:g}"
                )
                continue
        repriced.append(current)

    if indicative:
        raise HTTPException(
            status_code=409,
            detail=(
                "No live bid for " + ", ".join(indicative) + ". These premiums are "
                "indicative (priced off the last trade because the market is closed), so "
                "nothing was placed. Approve again while the market is open."
            ),
        )

    if drifted:
        # `_run_scan` above already superseded the old proposal with fresh prices, so the
        # user is re-approving against what the market is actually showing now.
        raise HTTPException(
            status_code=409,
            detail=(
                "Prices moved before approval, so nothing was placed. "
                "A fresh proposal is ready. " + "; ".join(drifted)
            ),
        )

    from icici_breeze_backend.app.services.bots import placement
    from icici_breeze_backend.app.services.processor import processor
    import icici_breeze_backend.app.core.config as cfg

    results = placement.place_short_legs(
        processor(),
        ctx.user_id,
        [leg.model_dump() for leg in repriced],
        tolerance_pct=float(cfg.AGGRESSIVE_LIMIT_DEFAULT_TOLERANCE_PCT),
    )
    placed = [
        PlacedLegResult(
            stock_code=r.stock_code,
            right=r.right,
            strike_price=r.strike_price,
            expiry_display=r.expiry_display,
            quantity=r.quantity,
            limit_price=r.limit_price,
            order_ids=r.order_ids,
            error=r.error,
        )
        for r in results
    ]
    ok_count = sum(1 for r in results if r.ok)
    all_ok = ok_count == len(results)

    AuditLogger(None).log_operation(
        ctx.user_id, OperationType.BOT_ORDERS_PLACED, "BotProposal", pending.id
    )
    run_id = repo.start_run(ctx.user_id, BOT_HOLDINGS_WRITER, "manual")
    repo.finish_run(
        run_id,
        status="completed" if all_ok else "failed",
        reason_code=ReasonCode.ORDERS_PLACED if all_ok else ReasonCode.ORDER_REJECTED,
        reason_text=f"{ok_count} of {len(results)} leg(s) placed.",
        detail={"legs": [p.model_dump() for p in placed]},
    )
    # The *approved* proposal is resolved, not the freshly-scanned one, so the run log shows
    # which snapshot the user actually acted on.
    repo.resolve_proposal(
        ctx.user_id,
        pending.id,
        status="placed",
        note=f"{ok_count} of {len(results)} leg(s) placed.",
    )
    return ApprovalResult(proposal_id=pending.id, placed=placed, all_succeeded=all_ok)
