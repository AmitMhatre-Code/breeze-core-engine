from fastapi import APIRouter
from fastapi import Request, Depends
from fastapi import HTTPException
from typing import Optional
from icici_breeze_backend.app.api.v1.covered_shorts_scan import run_covered_shorts_scan
from icici_breeze_backend.app.auth.context import get_request_context, get_request_context_or_redirect, RequestContext
from icici_breeze_backend.app.domain.responses import (
    UncoveredShortsDataResponse,
    UncoveredShortsScanResponse,
)
from icici_breeze_backend.app.domain.trading import UncoveredShortsFormRequest
from icici_breeze_backend.audit.logger import AuditLogger, OperationType
from icici_breeze_backend.app.services.processor import processor
from icici_breeze_backend.app.api.error_utils import raise_route_errors
from icici_breeze_backend.app.api.frontend_redirect import redirect_to_frontend, json_redirect
import icici_breeze_backend.app.core.config as cfg

router = APIRouter()
breeze = processor()


@router.get("")
async def serve_landing(request: Request):
    q = request.url.query
    return redirect_to_frontend("/uncovered-shorts" + ("?" + q if q else ""))


@router.post("")
async def process_post(
    body: UncoveredShortsFormRequest,
    context: RequestContext = Depends(get_request_context_or_redirect),
):
    errors = breeze.retrieve_errors()

    if body.action == cfg.CLEAR:
        if len(errors) == 0:
            return json_redirect(f"/uncovered-shorts?exchange_code={body.exchange_code or cfg.NFO}")
        raise_route_errors(errors, log_context="route_uncovered_shorts.process_post CLEAR")

    if body.action == cfg.OPTIMIZE:
        return json_redirect(
            f"/uncovered-shorts?exchange_code={body.exchange_code or cfg.NFO}&stock_code={body.stock_code or ''}"
            f"&expiry_date={body.expiry_date or ''}&limits={body.limits or ''}&elm={body.provision_elm or ''}"
            f"&otm_call_distance={body.otm_call_distance or ''}&otm_put_distance={body.otm_put_distance or ''}&top={body.top or ''}"
        )

    if body.action == cfg.SELL:
        if len(errors) == 0:
            return json_redirect(
                f"/order?action={body.action}&product_type={body.product_type or ''}&exchange_code={body.exchange_code or cfg.NFO}"
                f"&stock_code={body.stock_code or ''}&expiry_date={body.expiry_date or ''}&right={body.right or ''}"
                f"&strike_price={body.strike_price or ''}&quantity={body.quantity or ''}"
            )
        raise_route_errors(errors, log_context="route_uncovered_shorts.process_post SELL")

    return json_redirect("/uncovered-shorts")


@router.get("/data", response_model=UncoveredShortsDataResponse)
async def get_uncovered_shorts_data(ctx: RequestContext = Depends(get_request_context)):
    user_id = ctx.user_id
    if not ctx.broker_token:
        raise HTTPException(status_code=401, detail="ICICI broker token missing; re-login required")
    options = breeze.uncovered_shorts(user_id, stock_code=None, expiry_date=None, limits=None)
    AuditLogger(None).log_operation(user_id, OperationType.PORTFOLIO_VIEW, "Uncovered Shorts")
    return UncoveredShortsDataResponse(options=options)


@router.get("/scan", response_model=UncoveredShortsScanResponse)
async def get_uncovered_shorts_scan(
    stock_code: str,
    expiry_date: str,
    limits: int,
    top: int = 10,
    otm_call_distance: int = 10,
    otm_put_distance: int = 10,
    provision_elm: Optional[str] = None,
    exchange_code: str = cfg.NFO,
    ctx: RequestContext = Depends(get_request_context),
):
    """Same inputs as legacy uncovered_shorts Optimize: margin (lacs), OTM %, ELM, top N."""
    if not ctx.broker_token:
        raise HTTPException(status_code=401, detail="ICICI broker token missing; re-login required")
    if limits <= 0:
        raise HTTPException(status_code=400, detail="limits (margin lacs) must be positive")
    if top < 1 or top > 500:
        raise HTTPException(status_code=400, detail="top must be between 1 and 500")
    if otm_call_distance < 1 or otm_call_distance > 50 or otm_put_distance < 1 or otm_put_distance > 50:
        raise HTTPException(status_code=400, detail="OTM distance must be between 1 and 50")
    elm = cfg.CHECKED if provision_elm in (cfg.CHECKED, "on", "true", "1") else None
    raw = breeze.uncovered_shorts(
        ctx.user_id,
        stock_code=stock_code.strip(),
        expiry_date=expiry_date.strip(),
        limits=limits,
        elm=elm,
        otm_call_distance=otm_call_distance,
        otm_put_distance=otm_put_distance,
        top=top,
        exchange_code=exchange_code or cfg.NFO,
    )
    AuditLogger(None).log_operation(ctx.user_id, OperationType.PORTFOLIO_VIEW, "UncoveredShortsScan")
    return UncoveredShortsScanResponse(
        ce_options=raw.get("ce_options") or {},
        pe_options=raw.get("pe_options") or {},
    )


@router.get("/covered-shorts-scan", response_model=UncoveredShortsScanResponse)
async def get_covered_shorts_scan(
    stock_code: str,
    expiry_date: str,
    limits: int,
    top: int,
    otm_call_distance: int = 10,
    otm_put_distance: int = 10,
    provision_elm: Optional[str] = None,
    exchange_code: str = cfg.NFO,
    ctx: RequestContext = Depends(get_request_context),
):
    """Same as Strategy Builder covered scan: uncovered shorts (top 1–5) + best hedge per row."""
    if not ctx.broker_token:
        raise HTTPException(status_code=401, detail="ICICI broker token missing; re-login required")
    out = run_covered_shorts_scan(
        breeze,
        ctx.user_id,
        stock_code,
        expiry_date,
        limits,
        top,
        otm_call_distance=otm_call_distance,
        otm_put_distance=otm_put_distance,
        provision_elm=provision_elm,
        exchange_code=exchange_code,
    )
    AuditLogger(None).log_operation(ctx.user_id, OperationType.PORTFOLIO_VIEW, "CoveredShortsScan")
    return out
