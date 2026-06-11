"""Strategy Builder JSON API: chain, underlyings, multi-leg margin, orchestrated execution."""
import json
import logging
import os
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from icici_breeze_backend.app.api.deps_license import require_trading_not_revoked
from icici_breeze_backend.app.api.frontend_redirect import redirect_to_frontend
from icici_breeze_backend.app.auth.context import get_request_context, RequestContext
from icici_breeze_backend.app.api.v1.covered_shorts_scan import run_covered_shorts_scan
from icici_breeze_backend.app.domain.responses import UncoveredShortsScanResponse
from icici_breeze_backend.app.domain.options_strategy import (
    ProposeTradesRequest,
    ProposeTradesResponse,
)
from icici_breeze_backend.app.domain.strategy_builder import (
    StrategyBuilderChainResponse,
    StrategyBuilderExecuteRequest,
    StrategyBuilderExecuteResponse,
    StrategyBuilderLegResult,
    StrategyBuilderMarginRequest,
    StrategyBuilderMarginResponse,
    StrategyBuilderUnderlyingsResponse,
)
from icici_breeze_backend.app.services.options_strategy_engine import run_propose_trades
from icici_breeze_backend.app.services.processor import processor
from icici_breeze_backend.audit.logger import AuditLogger, OperationType
from icici_breeze_backend.audit.strategy_builder_audit import resolve_audit_file_for_user
from icici_breeze_backend.concurrency.idempotency import idempotency_store
import icici_breeze_backend.app.core.config as cfg

logger = logging.getLogger(__name__)
router = APIRouter()
breeze = processor()


def _serialize_messages(messages: list) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        if isinstance(m, dict):
            out.append({k: (str(v) if not isinstance(v, (str, int, float, bool, type(None))) else v) for k, v in m.items()})
        else:
            out.append({"message": str(m)})
    return out


@router.get("")
async def serve_landing(request: Request):
    q = request.url.query
    return redirect_to_frontend("/strategy-builder" + ("?" + q if q else ""))


@router.get("/underlyings", response_model=StrategyBuilderUnderlyingsResponse)
async def list_underlyings(
    exchange_code: str = cfg.NFO,
    ctx: RequestContext = Depends(get_request_context),
):
    if not ctx.broker_token:
        raise HTTPException(status_code=401, detail="ICICI broker token missing; re-login required")
    raw = breeze.fetch_stock_codes(exchange_code=exchange_code) or []
    AuditLogger(None).log_operation(ctx.user_id, OperationType.PORTFOLIO_VIEW, "StrategyBuilderUnderlyings")
    return StrategyBuilderUnderlyingsResponse(underlyings=raw)


@router.get("/chain", response_model=StrategyBuilderChainResponse)
async def get_chain(
    stock_code: str,
    expiry_date: str,
    exchange_code: str = cfg.NFO,
    ctx: RequestContext = Depends(get_request_context),
):
    if not ctx.broker_token:
        raise HTTPException(status_code=401, detail="ICICI broker token missing; re-login required")
    if not stock_code.strip() or not expiry_date.strip():
        raise HTTPException(status_code=400, detail="stock_code and expiry_date required")
    data = breeze.get_full_option_chain(ctx.user_id, stock_code.strip(), exchange_code, expiry_date.strip())
    AuditLogger(None).log_operation(ctx.user_id, OperationType.PORTFOLIO_VIEW, "StrategyBuilderChain")
    return StrategyBuilderChainResponse(**data)


@router.get("/covered-shorts-scan", response_model=UncoveredShortsScanResponse)
async def get_covered_shorts_scan(
    stock_code: str,
    expiry_date: str,
    limits: int,
    top: int,
    otm_call_min: int = 10,
    otm_call_max: int = 10,
    otm_put_min: int = 10,
    otm_put_max: int = 10,
    provision_elm: Optional[str] = None,
    exchange_code: str = cfg.NFO,
    ctx: RequestContext = Depends(get_request_context),
):
    """Uncovered short scan (top 1–5) plus best hedge per candidate via processor.hedge."""
    if not ctx.broker_token:
        raise HTTPException(status_code=401, detail="ICICI broker token missing; re-login required")
    out = run_covered_shorts_scan(
        breeze,
        ctx.user_id,
        stock_code,
        expiry_date,
        limits,
        top,
        otm_call_min=otm_call_min,
        otm_call_max=otm_call_max,
        otm_put_min=otm_put_min,
        otm_put_max=otm_put_max,
        provision_elm=provision_elm,
        exchange_code=exchange_code,
        margin_source=breeze.get_strategy_builder_margin_source(ctx.user_id),
    )
    AuditLogger(None).log_operation(ctx.user_id, OperationType.PORTFOLIO_VIEW, "StrategyBuilderCoveredShortsScan")
    return out


@router.get("/audit/{session_id}")
async def get_strategy_builder_audit(
    session_id: str,
    ctx: RequestContext = Depends(get_request_context),
):
    """Download the JSON audit log for a Strategy Builder (New) session owned by the caller."""
    if not ctx.broker_token:
        raise HTTPException(status_code=401, detail="ICICI broker token missing; re-login required")
    path = resolve_audit_file_for_user(session_id.strip(), ctx.user_id)
    if not path:
        raise HTTPException(status_code=404, detail="Audit log not found")
    fname = os.path.basename(path)
    return FileResponse(
        path,
        media_type="application/json",
        filename=fname,
        headers={"Cache-Control": "no-store"},
    )


@router.post("/propose-trades", response_model=ProposeTradesResponse)
async def post_propose_trades(
    body: ProposeTradesRequest,
    ctx: RequestContext = Depends(get_request_context),
):
    if not ctx.broker_token:
        raise HTTPException(status_code=401, detail="ICICI broker token missing; re-login required")
    data = run_propose_trades(
        breeze,
        ctx.user_id,
        exchange_code=body.exchange_code.strip() or cfg.NFO,
        stock_code=body.stock_code.strip(),
        expiry_date=body.expiry_date.strip(),
        margin_lacs=body.margin_lacs,
        max_loss_lacs=body.max_loss_lacs,
        min_pop_pct=body.min_pop_pct,
        provision_elm=body.provision_elm,
        request_id=ctx.request_id,
    )
    AuditLogger(None).log_operation(ctx.user_id, OperationType.PORTFOLIO_VIEW, "StrategyBuilderProposeTrades")
    return ProposeTradesResponse(**data)


@router.post("/margin", response_model=StrategyBuilderMarginResponse)
async def post_margin(
    body: StrategyBuilderMarginRequest,
    ctx: RequestContext = Depends(get_request_context),
):
    if not ctx.broker_token:
        raise HTTPException(status_code=401, detail="ICICI broker token missing; re-login required")
    legs = [leg.model_dump() for leg in body.legs]
    ex0 = legs[0].get("exchange_code") or cfg.NFO
    for leg in legs:
        if leg.get("exchange_code") != ex0:
            raise HTTPException(status_code=400, detail="All legs must use the same exchange_code")
    data = breeze.strategy_builder_margin(ctx.user_id, ex0, legs)
    if data.get("Status") == 200 and "Success" in data and isinstance(data.get("Success"), dict):
        data["Success"]["margin_source"] = breeze.get_strategy_builder_margin_source(ctx.user_id)
    elif data.get("Status") != 200:
        data["margin_source"] = breeze.get_strategy_builder_margin_source(ctx.user_id)
    AuditLogger(None).log_operation(ctx.user_id, OperationType.PORTFOLIO_VIEW, "StrategyBuilderMargin")
    return StrategyBuilderMarginResponse(**data)


@router.post("/execute", response_model=StrategyBuilderExecuteResponse)
async def post_execute(
    body: StrategyBuilderExecuteRequest,
    ctx: RequestContext = Depends(get_request_context),
    _trading_ok: None = Depends(require_trading_not_revoked),
):
    if not ctx.broker_token:
        raise HTTPException(status_code=401, detail="ICICI broker token missing; re-login required")

    customer = breeze.get_customer_details(ctx.user_id)
    if customer is None or customer.get("Status") != 200:
        raise HTTPException(status_code=503, detail="Unable to verify session with broker")

    margin = breeze.get_margin_situation(ctx.user_id, target_margin_ute=100)
    if margin.get("Status") != 200:
        raise HTTPException(status_code=503, detail="Unable to load margin; try again")

    stock_list = breeze.fetch_stock_codes()
    if len(stock_list) == 0:
        raise HTTPException(status_code=503, detail="Underlying master not loaded")

    results: list[StrategyBuilderLegResult] = []
    placed = 0
    failed = 0

    for idx, leg in enumerate(body.legs):
        idem = (leg.idempotency_key or "").strip() or None
        if idem:
            stored = idempotency_store.retrieve_result(idem, ctx.user_id)
            if stored and stored.response_body:
                try:
                    cached = json.loads(stored.response_body.decode())
                    ok = bool(cached.get("success"))
                    results.append(
                        StrategyBuilderLegResult(
                            index=idx,
                            success=ok,
                            idempotency_key=idem,
                            messages=cached.get("messages") or [],
                            error=cached.get("error"),
                        )
                    )
                    if ok:
                        placed += 1
                    else:
                        failed += 1
                    continue
                except (json.JSONDecodeError, TypeError):
                    logger.warning("Stale idempotency payload for key=%s", idem)

        ex = leg.exchange_code or cfg.NFO
        try:
            qn = int(str(leg.quantity).strip(), 10)
        except ValueError:
            results.append(
                StrategyBuilderLegResult(
                    index=idx,
                    success=False,
                    idempotency_key=idem,
                    error="Invalid quantity",
                )
            )
            failed += 1
            continue
        if qn <= 0:
            results.append(
                StrategyBuilderLegResult(
                    index=idx,
                    success=False,
                    idempotency_key=idem,
                    error="quantity must be positive",
                )
            )
            failed += 1
            continue

        messages = breeze.break_order(
            ctx.user_id,
            leg.stock_code,
            leg.expiry_date,
            leg.product_type or cfg.OPTIONS,
            leg.right,
            leg.strike_price,
            str(qn),
            leg.price or "0",
            leg.action,
            exchange_code=ex,
        )
        breeze.store_messages(ctx.user_id, messages)
        serialized = _serialize_messages(messages)
        success = bool(messages) and not any(m.get("type") == cfg.DANGER for m in messages)
        err = None
        if not success and messages:
            for m in messages:
                if m.get("type") == cfg.DANGER:
                    err = str(m.get("message") or "Order failed")
                    break
            if not err:
                err = "Order failed"

        results.append(
            StrategyBuilderLegResult(
                index=idx,
                success=success,
                idempotency_key=idem,
                messages=serialized,
                error=err,
            )
        )
        if success:
            placed += 1
            AuditLogger(None).log_operation(
                ctx.user_id,
                OperationType.ORDER_PLACE,
                "StrategyBuilderLeg",
                action_status="success",
            )
        else:
            failed += 1
            AuditLogger(None).log_operation(
                ctx.user_id,
                OperationType.ORDER_PLACE,
                "StrategyBuilderLeg",
                action_status="failure",
                error_details=err,
            )

        if idem:
            payload = json.dumps(
                {"success": success, "messages": serialized, "error": err},
            ).encode()
            idempotency_store.store_result(idem, ctx.user_id, "strategy_builder_leg", payload, 200)

    return StrategyBuilderExecuteResponse(legs=results, placed_count=placed, failed_count=failed)
