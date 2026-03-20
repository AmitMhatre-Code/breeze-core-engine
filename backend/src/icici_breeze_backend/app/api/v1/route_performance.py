from fastapi import APIRouter, Depends
from fastapi import Request
from fastapi import HTTPException
from icici_breeze_backend.app.auth.context import get_request_context, RequestContext
from icici_breeze_backend.app.domain.responses import PerformanceDataResponse
from icici_breeze_backend.audit.logger import AuditLogger, OperationType
from icici_breeze_backend.app.services.processor import processor
from icici_breeze_backend.app.api.frontend_redirect import redirect_to_frontend

router = APIRouter()
breeze = processor()


@router.get("")
async def serve_landing(request: Request):
    q = request.url.query
    return redirect_to_frontend("/performance" + ("?" + q if q else ""))


@router.get("/data", response_model=PerformanceDataResponse)
async def get_performance_api(ctx: RequestContext = Depends(get_request_context)):
    user_id = ctx.user_id
    if not ctx.broker_token:
        raise HTTPException(status_code=401, detail="ICICI broker token missing; re-login required")
    performance = breeze.get_performance(user_id, 0, None, None)
    funds = breeze.get_funds(user_id)
    AuditLogger(None).log_operation(user_id, OperationType.PORTFOLIO_VIEW, "Performance")
    return PerformanceDataResponse(
        performance=performance or {},
        funds=funds if isinstance(funds, dict) else (funds or {}),
    )
