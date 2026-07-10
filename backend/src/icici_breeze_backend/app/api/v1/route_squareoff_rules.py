"""Portfolio group profit/loss square-off rules (Portfolio > group > Exit Rule)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from icici_breeze_backend.app.api.deps_license import require_trading_not_revoked
from icici_breeze_backend.app.auth.context import RequestContext, get_request_context
from icici_breeze_backend.app.domain.squareoff_rule import (
    ArmSquareOffRuleRequest,
    SquareOffRuleListResponse,
    SquareOffRuleRecord,
)
from icici_breeze_backend.app.repositories import squareoff_rules as repo
from icici_breeze_backend.app.services import portfolio_pnl_engine
from icici_breeze_backend.app.services.reference_data.scrip_master_sql import normalize_expiry_display
from icici_breeze_backend.audit.logger import AuditLogger, OperationType

router = APIRouter()


@router.get("", response_model=SquareOffRuleListResponse)
async def list_rules(ctx: RequestContext = Depends(get_request_context)):
    return SquareOffRuleListResponse(rules=repo.list_active_rules(ctx.user_id))


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

    record = repo.arm_rule(
        ctx.user_id,
        stock_code=stock_code,
        expiry_display=expiry_display,
        exchange_code=body.exchange_code,
        profit_target_pnl=body.profit_target_pnl,
        loss_limit_pnl=body.loss_limit_pnl,
    )
    portfolio_pnl_engine.set_group_rule(
        ctx.user_id,
        record.id,
        stock_code=stock_code,
        expiry_display=expiry_display,
        exchange_code=body.exchange_code,
        target_pnl=body.profit_target_pnl,
        stop_loss_pnl=body.loss_limit_pnl,
    )
    AuditLogger(None).log_operation(
        ctx.user_id,
        OperationType.SQUAREOFF_RULE_ARMED,
        "PortfolioSquareOffRule",
        record.id,
        error_details=f"{stock_code} {expiry_display} target={body.profit_target_pnl} stop={body.loss_limit_pnl}",
    )
    return record


@router.delete("/{rule_id}")
async def disarm_rule(
    rule_id: str,
    ctx: RequestContext = Depends(get_request_context),
    _: None = Depends(require_trading_not_revoked),
):
    ok = repo.disarm_rule(ctx.user_id, rule_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Rule not found or already resolved")
    rule = repo.get_rule(rule_id)
    if rule is not None:
        portfolio_pnl_engine.clear_group_rule(ctx.user_id, rule.stock_code, rule.expiry_display)
    AuditLogger(None).log_operation(
        ctx.user_id, OperationType.SQUAREOFF_RULE_DISARMED, "PortfolioSquareOffRule", rule_id
    )
    return {"ok": True}
