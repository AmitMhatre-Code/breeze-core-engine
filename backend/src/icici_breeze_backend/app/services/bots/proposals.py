"""Building, re-pricing and approving bot proposals — the propose → approve artefact.

This module exists because approval now has two front doors. The app's review screen was
the only one until Telegram HITL (`approval_mode="telegram"`) let a user approve from a
phone, and the safety rules that make an approval safe — re-price before committing, refuse
material drift, refuse an indicative price — must be identical on both. Leaving them in the
route handler would have meant the Telegram path either re-implemented them or skipped
them, and skipping them is how a tap places orders at prices nobody saw.

So the rules live here and the callers translate: `route_bots` turns `ApprovalRefused` into
the HTTP status it always used, and the Telegram handler turns it into a message.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from icici_breeze_backend.app.db.bots_migrate import (
    BOT_EXPIRY_INDEX_WRITER,
    BOT_HOLDINGS_WRITER,
)
from icici_breeze_backend.app.domain.bots import (
    ApprovalResult,
    ApproveProposalRequest,
    ExpiryIndexWriterConfig,
    HoldingsWriterConfig,
    PlacedLegResult,
    ProposalLeg,
    ReasonCode,
)
from icici_breeze_backend.app.repositories import bots as repo
from icici_breeze_backend.audit.logger import AuditLogger, OperationType

_logger = logging.getLogger(__name__)

# A proposal is a priced snapshot. If the bid has moved more than this by the time the user
# approves, the orders would go out at prices they never agreed to — so re-scan and make
# them look again rather than filling on stale numbers.
MATERIAL_DRIFT_PCT = 10.0


class ApprovalRefused(Exception):
    """An approval that could not be honoured, with the HTTP status the route used to raise.

    Carrying the status here keeps `route_bots`'s existing contract byte-for-byte while
    letting the Telegram handler ignore it and use `message` alone. `reason_code` is what
    the run log and the re-proposal decision key off, so it must stay machine-readable.
    """

    def __init__(self, message: str, *, status_code: int = 400, reason_code: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.reason_code = reason_code or ReasonCode.INTERNAL_ERROR

    @property
    def repriceable(self) -> bool:
        """True when a fresh proposal would plausibly succeed where this one failed.

        Drift and a missing bid are both "the market moved", which is exactly the case the
        re-proposal loop exists for. A missing proposal or an empty selection is not — those
        would re-fail identically, and re-proposing on them would loop.
        """
        return self.reason_code in (ReasonCode.QUOTE_UNAVAILABLE, ReasonCode.RATE_LIMITED)


def cfg_right(right: str) -> str:
    import icici_breeze_backend.app.core.config as cfg

    return cfg.CALL if str(right).lower().startswith("c") else cfg.PUT


def leg_key(leg) -> tuple:
    return (leg.stock_code, leg.right, leg.expiry_display, round(float(leg.strike_price), 4))


# --------------------------------------------------------------------------------------
# Building proposals
# --------------------------------------------------------------------------------------


def plan_to_legs(plan, index_code: str) -> list[ProposalLeg]:
    """Flatten a sized plan into proposal legs.

    Both sides of a strangle share a `group_key`, so the UI can select and deselect them
    together -- half a strangle is a naked short, which is not the shape the user picked.
    """
    from icici_breeze_backend.app.services.bots import expiry_index_writer as bot2

    group_key = f"{index_code}:{plan.strategy}"
    lot_size = plan.quantity // plan.lots if plan.lots else plan.quantity
    out = []
    for leg in plan.legs:
        out.append(
            ProposalLeg(
                stock_code=index_code,
                exchange_code=plan.exchange_code,
                right=leg["right"],
                expiry_display=plan.expiry_display,
                strike_price=leg["strike_price"],
                lots=plan.lots,
                lot_size=lot_size,
                quantity=leg["quantity"],
                premium_per_share=leg["bid"],
                premium_total=leg["premium_total"],
                premium_basis="bid",
                # The whole shape's netted margin is a property of the pair, so it is
                # attributed to the first leg rather than split arbitrarily across both.
                span_margin=plan.margin_total if leg is plan.legs[0] else 0.0,
                elm_margin=0.0,
                strategy=plan.strategy,
                group_key=group_key,
                margin_yield=plan.margin_yield,
                selected=True,
                note=bot2.STRATEGY_LABEL.get(plan.strategy or "", None),
            )
        )
    return out


def index_totals(legs: list[ProposalLeg]) -> dict:
    return {
        "premium_total": round(sum(l.premium_total for l in legs), 2),
        "span_total": round(sum(l.span_margin or 0 for l in legs), 2),
        "elm_total": 0.0,
        "delivery_exposure_total": 0.0,
        "delivery_cash_budget": 0.0,
        "delivery_headroom": 0.0,
        "leg_count": len(legs),
        "selected_count": sum(1 for l in legs if l.selected),
    }


def holdings_totals(legs: list[ProposalLeg], config: HoldingsWriterConfig) -> dict:
    from icici_breeze_backend.app.services.bots import holdings_writer

    return holdings_writer._totals(legs, config)


# --------------------------------------------------------------------------------------
# Re-pricing a user's edits
# --------------------------------------------------------------------------------------


def price_edited_leg(user_id: str, leg, edit, fresh_by_scrip: dict):
    """Price one leg the user changed, enforcing the cap the edit cannot escape.

    Lots are the user's call up to the point where a call stops being covered. Beyond that
    the edit is silently clipped rather than honoured: this bot's whole premise is that its
    calls are backed by stock, and an edit box is not a licence to sell naked ones.
    """
    from icici_breeze_backend.app.services.bots import holdings_writer
    from icici_breeze_backend.app.services.processor import processor

    proc = processor()
    reference = fresh_by_scrip.get((leg.stock_code, leg.right))
    lots = int(edit.lots) if edit.lots is not None else leg.lots
    if leg.right == "call":
        source = reference or leg
        held = int(source.held_quantity or 0)
        lot_size = int(source.lot_size or leg.lot_size or 0)
        if held and lot_size:
            covered = held // lot_size - int(source.existing_short_lots or 0)
            lots = max(1, min(lots, covered)) if covered > 0 else 0
    if lots <= 0:
        return None

    return holdings_writer.price_contract(
        proc,
        user_id,
        stock_code=leg.stock_code,
        right=leg.right,
        expiry_display=leg.expiry_display,
        strike_price=float(
            edit.strike_price if edit.strike_price is not None else leg.strike_price
        ),
        lots=lots,
        lot_size=int(leg.lot_size),
        margin_source=proc.get_strategy_builder_margin_source(user_id),
        held_quantity=leg.held_quantity,
        pledged_quantity=leg.pledged_quantity,
        existing_short_lots=leg.existing_short_lots,
        scrip_priority=leg.scrip_priority,
    )


def reprice_index_leg(proc, user_id: str, leg: ProposalLeg, edit) -> Optional[ProposalLeg]:
    """Bot 2 legs are re-priced by size only.

    The strike is not the user's to move here: it is derived from the safety distance
    against the spot at fire time, and a strike pinned against a stale spot is no longer the
    distance the user configured. Size is genuinely theirs.
    """
    if edit.lots is None:
        return None
    lots = max(1, int(edit.lots))
    quantity = lots * int(leg.lot_size)
    margin = None
    if leg.span_margin:
        from icici_breeze_backend.app.services.bots import expiry_index_writer as bot2

        margin = bot2.margin_for_legs(
            proc,
            user_id,
            exchange_code=leg.exchange_code,
            stock_code=leg.stock_code,
            expiry_display=leg.expiry_display,
            legs=[(cfg_right(leg.right), leg.strike_price, quantity)],
        )
    return leg.model_copy(
        update={
            "lots": lots,
            "quantity": quantity,
            "premium_total": round(leg.premium_per_share * quantity, 2),
            "span_margin": round(margin, 2) if margin is not None else leg.span_margin,
        }
    )


# --------------------------------------------------------------------------------------
# Approval
# --------------------------------------------------------------------------------------


def approve(user_id: str, bot_type: str, payload: ApproveProposalRequest) -> ApprovalResult:
    """Place the approved legs, after confirming their prices still hold.

    Approval names the legs to keep — anything omitted is dropped, which is how the manual
    delivery-cash allocation is expressed.
    """
    if bot_type == BOT_EXPIRY_INDEX_WRITER:
        return _approve_index_plan(user_id, payload)
    return _approve_holdings(user_id, payload)


def _approve_holdings(user_id: str, payload: ApproveProposalRequest) -> ApprovalResult:
    from icici_breeze_backend.app.services.bots import holdings_runner, placement
    from icici_breeze_backend.app.services.processor import processor
    import icici_breeze_backend.app.core.config as cfg

    pending = repo.get_pending_proposal(user_id, BOT_HOLDINGS_WRITER)
    if pending is None:
        raise ApprovalRefused(
            "No proposal awaiting approval — run a scan first.",
            status_code=404,
            reason_code=ReasonCode.NOTHING_ELIGIBLE,
        )
    try:
        chosen = [pending.legs[i] for i in sorted(set(payload.leg_indexes))]
    except IndexError as e:
        raise ApprovalRefused("Unknown leg in approval.", status_code=400) from e
    if not chosen:
        raise ApprovalRefused("No legs selected.", status_code=400)

    # Re-price before committing. A proposal that cannot be re-priced fails closed --
    # a scan that will not run is not permission to place on the old snapshot.
    from icici_breeze_backend.app.services.bots import holdings_writer

    try:
        _, fresh, _, _ = holdings_runner.run_scan(user_id, "manual")
    except holdings_writer.BotScanError as e:
        raise ApprovalRefused(
            str(e), status_code=502, reason_code=ReasonCode.BROKER_ERROR
        ) from e
    fresh_by_key = {leg_key(leg): leg for leg in (fresh.legs if fresh else [])}
    fresh_by_scrip = {(leg.stock_code, leg.right): leg for leg in (fresh.legs if fresh else [])}
    drifted: list[str] = []
    indicative: list[str] = []
    repriced = []
    for order, leg in zip(sorted(set(payload.leg_indexes)), chosen):
        edit = payload.edits.get(order)
        if edit is not None and (edit.lots is not None or edit.strike_price is not None):
            # An edited leg is a contract the scan never priced, so it is priced directly
            # rather than matched against the fresh scan. It still goes through the same
            # bid-not-LTP and margin rules, and it is still capped by coverage below.
            current = price_edited_leg(user_id, leg, edit, fresh_by_scrip)
            if current is None:
                drifted.append(
                    f"{leg.stock_code} {edit.strike_price or leg.strike_price:g} could not "
                    "be priced"
                )
                continue
            if current.premium_basis != "bid":
                indicative.append(f"{leg.stock_code} {current.strike_price:g}")
                continue
            repriced.append(current)
            continue
        current = fresh_by_key.get(leg_key(leg))
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
        raise ApprovalRefused(
            "No live bid for " + ", ".join(indicative) + ". These premiums are "
            "indicative (priced off the last trade because the market is closed), so "
            "nothing was placed. Approve again while the market is open.",
            status_code=409,
            reason_code=ReasonCode.QUOTE_UNAVAILABLE,
        )

    if drifted:
        # The scan above already superseded the old proposal with fresh prices, so the
        # user is re-approving against what the market is actually showing now.
        raise ApprovalRefused(
            "Prices moved before approval, so nothing was placed. "
            "A fresh proposal is ready. " + "; ".join(drifted),
            status_code=409,
            reason_code=ReasonCode.QUOTE_UNAVAILABLE,
        )

    results = placement.place_short_legs(
        processor(),
        user_id,
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
        user_id, OperationType.BOT_ORDERS_PLACED, "BotProposal", pending.id
    )
    run_id = repo.start_run(user_id, BOT_HOLDINGS_WRITER, "manual")
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
        user_id,
        pending.id,
        status="placed",
        note=f"{ok_count} of {len(results)} leg(s) placed.",
    )
    # ...and the freshly-scanned one is retired rather than left pending. The re-price above
    # ran a real scan, which creates a proposal; on the drift path that is deliberate, but
    # here the orders have gone out and a proposal still sitting there would offer the user
    # the same trade a second time.
    repo.supersede_other_pending(user_id, BOT_HOLDINGS_WRITER, pending.id)
    return ApprovalResult(proposal_id=pending.id, placed=placed, all_succeeded=all_ok)


def _approve_index_plan(user_id: str, payload: ApproveProposalRequest) -> ApprovalResult:
    """Execute Bot 2's reviewed plan.

    The plan is re-derived rather than replayed from the stored proposal: index premiums on
    an expiry morning move fast enough that placing on a snapshot even a minute old is not
    the trade the user approved. Selecting legs by index into the fresh plan keeps the
    strangle's two sides together, which is the only shape selection that is meaningful --
    half a strangle is a naked short.
    """
    from icici_breeze_backend.app.services.bots import expiry_index_writer as bot2
    from icici_breeze_backend.app.services.processor import processor

    pending = repo.get_pending_proposal(user_id, BOT_EXPIRY_INDEX_WRITER)
    if pending is None:
        raise ApprovalRefused(
            "No plan awaiting approval — start a run first.",
            status_code=404,
            reason_code=ReasonCode.NOTHING_ELIGIBLE,
        )
    chosen_indexes = sorted(set(payload.leg_indexes))
    if not chosen_indexes:
        raise ApprovalRefused("No legs selected.", status_code=400)
    try:
        chosen = [pending.legs[i] for i in chosen_indexes]
    except IndexError as e:
        raise ApprovalRefused("Unknown leg in approval.", status_code=400) from e

    proc = processor()
    bot = repo.get_or_create_bot(user_id, BOT_EXPIRY_INDEX_WRITER)
    config = ExpiryIndexWriterConfig(**bot.config)
    available = bot2._available_margin(proc, user_id)
    if not available or available <= 0:
        raise ApprovalRefused(
            "Could not read available margin.",
            status_code=502,
            reason_code=ReasonCode.BROKER_ERROR,
        )
    margin_source = proc.get_strategy_builder_margin_source(user_id)

    run_id = repo.start_run(user_id, BOT_EXPIRY_INDEX_WRITER, "manual")
    placed: list[PlacedLegResult] = []
    all_ok = True
    for index_code in sorted({leg.stock_code for leg in chosen}):
        index_legs = [leg for leg in chosen if leg.stock_code == index_code]
        plan = bot2.plan_index(
            proc,
            user_id,
            index_code,
            expiry_display=index_legs[0].expiry_display,
            config=config,
            available_margin=available,
            margin_source=margin_source,
        )
        if plan.error or not plan.legs:
            all_ok = False
            placed.append(
                PlacedLegResult(
                    stock_code=index_code,
                    right=index_legs[0].right,
                    strike_price=index_legs[0].strike_price,
                    expiry_display=index_legs[0].expiry_display,
                    quantity=0,
                    limit_price=0.0,
                    error=plan.error or "Could not re-price the plan.",
                )
            )
            continue

        # The reviewed lot count is the user's; the strike stays whatever the fresh plan
        # says, because a strike chosen against a stale spot is not the safety distance the
        # user configured.
        edited_lots = {
            i: payload.edits[i].lots
            for i in chosen_indexes
            if i in payload.edits and payload.edits[i].lots is not None
        }
        if edited_lots:
            wanted = min(edited_lots.values())
            if wanted and wanted != plan.lots and plan.lots:
                lot_size = plan.quantity // plan.lots
                plan.lots = max(1, min(int(wanted), plan.lots))
                plan.quantity = plan.lots * lot_size
                for leg in plan.legs:
                    leg["quantity"] = plan.quantity
                    leg["premium_total"] = round(leg["bid"] * plan.quantity, 2)
                plan.premium_total = round(
                    sum(leg["premium_total"] for leg in plan.legs), 2
                )

        result = bot2.execute_plan(proc, user_id, plan, config=config)
        if result.error:
            all_ok = False
        for leg in result.legs:
            placed.append(
                PlacedLegResult(
                    stock_code=index_code,
                    right=leg["right"],
                    strike_price=leg["strike_price"],
                    expiry_display=result.expiry_display,
                    quantity=leg["quantity"],
                    limit_price=leg["bid"],
                    order_ids=result.order_ids,
                    error=result.error,
                )
            )

    AuditLogger(None).log_operation(
        user_id, OperationType.BOT_ORDERS_PLACED, "BotProposal", pending.id
    )
    repo.finish_run(
        run_id,
        status="completed" if all_ok else "failed",
        reason_code=ReasonCode.ORDERS_PLACED if all_ok else ReasonCode.ORDER_REJECTED,
        reason_text=f"{sum(1 for p in placed if not p.error)} of {len(placed)} leg(s) placed.",
        detail={"legs": [p.model_dump() for p in placed]},
    )
    repo.resolve_proposal(
        user_id,
        pending.id,
        status="placed",
        note=f"{sum(1 for p in placed if not p.error)} of {len(placed)} leg(s) placed.",
    )
    return ApprovalResult(proposal_id=pending.id, placed=placed, all_succeeded=all_ok)
