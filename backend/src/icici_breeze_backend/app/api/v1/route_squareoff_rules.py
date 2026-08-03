"""Strategy Group profit/loss square-off rules (Portfolio > group > Exit Rule)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from icici_breeze_backend.app.api.deps_license import require_trading_not_revoked
from icici_breeze_backend.app.auth.context import RequestContext, get_request_context
from icici_breeze_backend.app.domain.squareoff_rule import (
    ArmSquareOffRuleRequest,
    SquareOffRuleListResponse,
    SquareOffRuleLiveLeg,
    SquareOffRuleRecord,
)
from icici_breeze_backend.app.repositories import squareoff_rules as repo
from icici_breeze_backend.app.services import portfolio_pnl_engine, strategy_group_lifecycle
from icici_breeze_backend.app.services.icici_call_class import advisory_calls
from icici_breeze_backend.app.services.processor import processor
from icici_breeze_backend.app.services.reference_data.scrip_master_sql import normalize_expiry_display
from icici_breeze_backend.app.services.strategy_group_arm_guard import (
    ArmPreconditionError,
    assert_can_arm,
)
from icici_breeze_backend.audit.logger import AuditLogger, OperationType

_logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("", response_model=SquareOffRuleListResponse)
async def list_rules(ctx: RequestContext = Depends(get_request_context)):
    rules = repo.list_active_rules(ctx.user_id)
    _attach_reset_details(ctx.user_id, rules)
    return SquareOffRuleListResponse(rules=rules)


@router.get("/for-exit-board", response_model=SquareOffRuleListResponse)
async def list_rules_for_exit_board(ctx: RequestContext = Depends(get_request_context)):
    """Orders page > Profit Booking / Stop Loss table — includes completed/reset history
    alongside the live SGs, unlike Portfolio's own badge query above."""
    _reconcile_fired_rules(ctx.user_id)
    rules = repo.list_all_rules_for_exit_board(ctx.user_id)
    _attach_live_legs(ctx.user_id, rules)
    _attach_reset_details(ctx.user_id, rules)
    return SquareOffRuleListResponse(rules=rules)


def _reconcile_fired_rules(user_id: str) -> None:
    """Heal any SG stranded on `fired` by a dropped WS fill event, BEFORE reading the
    rules we are about to return.

    Ordering is the point: this endpoint is what renders the status badge, so
    reconciling here means the badge is post-reconcile by construction. Hanging it off
    the order-book fetch instead (where it used to live) left the flip invisible until an
    unrelated refetch, because the exit board is a separate query that had already
    returned.
    """
    strategy_group_lifecycle.reconcile_fired_rules_for_user(user_id, processor())


def _attach_reset_details(user_id: str, rules: list[SquareOffRuleRecord]) -> None:
    """Populate the derived hazard tier / orphan list on every `reset` SG.

    Computed here rather than in the frontend: the order book and the position registry
    already live on this side, and joining them in the browser would duplicate the work
    and invent a second opinion about the same facts. Best-effort — a broker hiccup must
    not take out the whole rules list, it just leaves the tier unknown for this poll.
    """
    if not any(r.status == "reset" for r in rules):
        return
    try:
        # Advisory: this is the hazard/orphan banner. It is the single largest consumer of
        # the daily broker budget on this deployment, and losing a refresh costs a
        # slightly-stale warning — not a missed exit. It sheds first under pressure so the
        # reserve stays available for placing and cancelling orders.
        with advisory_calls():
            strategy_group_lifecycle.attach_reset_details(user_id, processor(), rules)
    except Exception:  # noqa: BLE001
        _logger.exception("Could not attach reset details for user_id=%s", user_id)


def _attach_live_legs(user_id: str, rules: list[SquareOffRuleRecord]) -> None:
    """Join each rule's currently-open legs from the P&L engine's live
    position registry, for the Orders page's Current MTM column.

    `rule.leg_results` (stored with the rule) only exists once the rule has
    *fired* — for a still-armed rule (the common case: watching, not yet
    triggered) there's no leg data on the rule itself at all, only the
    threshold. The actual open legs live in Portfolio's own position data,
    the same source `portfolio_pnl_engine` tracks."""
    for rule in rules:
        legs = portfolio_pnl_engine.group_legs_for_user(user_id, rule.stock_code, rule.expiry_display)
        if not legs:
            continue
        rule.live_legs = [
            SquareOffRuleLiveLeg(
                scrip_key=leg.scrip_key,
                stock_code=leg.stock_code,
                strike_price=leg.strike,
                right=leg.right,
                quantity=leg.quantity,
                action=leg.action,
                average_price=leg.average_price,
            )
            for leg in legs
        ]


@router.post("", response_model=SquareOffRuleRecord)
async def arm_rule(
    body: ArmSquareOffRuleRequest,
    ctx: RequestContext = Depends(get_request_context),
    _: None = Depends(require_trading_not_revoked),
):
    stock_code = body.stock_code.strip().upper()
    expiry_display = normalize_expiry_display(body.expiry_date.strip())
    if not stock_code or not expiry_display:
        raise HTTPException(status_code=400, detail="stock_code and expiry_date required")

    # Spec section 4, and the duplicate-fire guard: a previous rule's exit orders may still
    # be resting at the exchange, and arming over them would stack a second exit on each.
    try:
        assert_can_arm(processor(), ctx.user_id, stock_code, expiry_display)
    except ArmPreconditionError as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc

    # Baseline for composition-drift detection (spec section 9), captured fresh on every
    # arm so it always matches the thresholds the user just confirmed.
    legs = portfolio_pnl_engine.group_legs_for_user(ctx.user_id, stock_code, expiry_display)
    if not legs:
        raise HTTPException(
            status_code=409,
            detail=(
                f"No open positions found for {stock_code} {expiry_display}. Profit Booking "
                f"/ Stop Loss protects an existing position — open one first."
            ),
        )

    record = repo.arm_rule(
        ctx.user_id,
        stock_code=stock_code,
        expiry_display=expiry_display,
        exchange_code=body.exchange_code,
        profit_target_pnl=body.profit_target_pnl,
        loss_limit_pnl=body.loss_limit_pnl,
        target_premium_pct=body.target_premium_pct,
        stop_loss_premium_pct=body.stop_loss_premium_pct,
        legs_snapshot=strategy_group_lifecycle.snapshot_from_legs(legs),
    )
    # Hold the chain subscription for as long as this SG is armed, independent of any
    # browser: otherwise closing the tab lets the chain go cold, quotes go stale, and the
    # rule silently stops protecting.
    strategy_group_lifecycle.pin_subscription(ctx.user_id, record)
    portfolio_pnl_engine.set_group_rule(
        ctx.user_id,
        record.id,
        stock_code=stock_code,
        expiry_display=expiry_display,
        exchange_code=body.exchange_code,
        target_pnl=body.profit_target_pnl,
        stop_loss_pnl=body.loss_limit_pnl,
        target_premium_pct=body.target_premium_pct,
        stop_loss_premium_pct=body.stop_loss_premium_pct,
    )
    AuditLogger(None).log_operation(
        ctx.user_id,
        OperationType.SQUAREOFF_RULE_ARMED,
        "PortfolioSquareOffRule",
        record.id,
        error_details=(
            f"{stock_code} {expiry_display} target={body.profit_target_pnl} "
            f"stop={body.loss_limit_pnl} target_pct={body.target_premium_pct} "
            f"stop_pct={body.stop_loss_premium_pct}"
        ),
    )
    return record


@router.post("/{rule_id}/cancel-orphan-orders")
async def cancel_orphan_orders(
    rule_id: str,
    ctx: RequestContext = Depends(get_request_context),
    _: None = Depends(require_trading_not_revoked),
):
    """Cancel this Reset SG's still-live exit orders.

    The escape hatch, and deliberately **user-initiated only**. Reset itself never cancels
    them: the SG may have fired on a stop loss, and auto-cancelling would kill the exits
    that are still working — leaving the user unprotected in a moving market — just because
    some unrelated leg was rejected. Those orders are the user's own configured intent
    already in flight, so only they get to retract them.
    """
    rule = repo.get_rule(rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    _attach_reset_details(ctx.user_id, [rule])
    orphans = rule.orphan_orders or []
    if not orphans:
        return {"ok": True, "cancelled": [], "failed": []}

    breeze = processor()
    cancelled: list[str] = []
    failed: list[dict[str, str]] = []
    for o in orphans:
        try:
            result = breeze.cancel_order_single(ctx.user_id, o.order_id)
        except Exception as exc:  # noqa: BLE001 — one failure must not strand the rest
            _logger.exception("Cancelling orphan order %s failed", o.order_id)
            failed.append({"order_id": o.order_id, "error": str(exc)})
            continue
        if result.get("success"):
            cancelled.append(o.order_id)
        else:
            failed.append(
                {"order_id": o.order_id, "error": str(result.get("error") or "Unknown error")}
            )
    if cancelled:
        # The cached order book still shows these as live, and `rearm_blocked` is derived
        # from exactly that. Without this the user cancels their orphans and is still told
        # they cannot re-arm, for up to the cache TTL — friction on a safety action, and
        # indistinguishable from the cancel having failed.
        from icici_breeze_backend.app.services import order_book_cache

        order_book_cache.invalidate_user(ctx.user_id)
    AuditLogger(None).log_operation(
        ctx.user_id,
        OperationType.SQUAREOFF_RULE_DISARMED,
        "PortfolioSquareOffRule",
        rule_id,
        action_status="success" if not failed else "failure",
        error_details=f"cancelled {len(cancelled)}/{len(orphans)} orphaned exit order(s)",
    )
    return {"ok": not failed, "cancelled": cancelled, "failed": failed}


@router.delete("/{rule_id}")
async def disarm_rule(
    rule_id: str,
    ctx: RequestContext = Depends(get_request_context),
    _: None = Depends(require_trading_not_revoked),
):
    existing = repo.get_rule(rule_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Rule not found or already resolved")

    # A Reset SG can't be dismissed while its exit orders are still live: dismissing would
    # erase the hazard from the UI while the orders keep working, and one of them may open
    # a contra position. The UI must not be able to lie about live risk.
    if existing.status == "reset":
        _attach_reset_details(ctx.user_id, [existing])
        if existing.rearm_blocked:
            n = len(existing.orphan_orders or [])
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{n} exit order(s) from this rule are still live and may still "
                    f"execute. Cancel them first, or wait for them to fill or expire."
                ),
            )

    if not repo.disarm_rule(ctx.user_id, rule_id):
        raise HTTPException(status_code=404, detail="Rule not found or already resolved")
    portfolio_pnl_engine.clear_group_rule(
        ctx.user_id, existing.stock_code, existing.expiry_display
    )
    strategy_group_lifecycle.release_subscription(rule_id)
    AuditLogger(None).log_operation(
        ctx.user_id, OperationType.SQUAREOFF_RULE_DISARMED, "PortfolioSquareOffRule", rule_id
    )
    return {"ok": True}
