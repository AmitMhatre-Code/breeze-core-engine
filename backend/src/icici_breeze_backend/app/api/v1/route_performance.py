import asyncio

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


def _resolve_performance_range(
    years: list,
    fy: str | None,
    start: str | None,
    end: str | None,
) -> tuple[str, str, str]:
    """Returns (start_date, end_date, fy_label)."""
    if not years:
        return "", "", ""
    if start and end:
        for y in years:
            if y.get("start") == start and y.get("end") == end:
                return start, end, str(y.get("year", ""))
        return start, end, ""
    if fy:
        for y in years:
            if y.get("year") == fy:
                return str(y["start"]), str(y["end"]), str(y.get("year", ""))
    y0 = years[0]
    return str(y0["start"]), str(y0["end"]), str(y0.get("year", ""))


@router.get("/data", response_model=PerformanceDataResponse)
async def get_performance_api(
    ctx: RequestContext = Depends(get_request_context),
    fy: str | None = None,
    start: str | None = None,
    end: str | None = None,
):
    user_id = ctx.user_id
    if not ctx.broker_token:
        raise HTTPException(status_code=401, detail="ICICI broker token missing; re-login required")

    # years, margin, and funds are mutually independent ICICI calls; run them
    # concurrently on worker threads instead of blocking the event loop in series.
    years, margin, funds = await asyncio.gather(
        asyncio.to_thread(breeze.get_financial_years),
        asyncio.to_thread(breeze.get_margin_situation, user_id, target_margin_ute=100),
        asyncio.to_thread(breeze.get_funds, user_id),
    )
    years = years or []
    start_d, end_d, fy_label = _resolve_performance_range(years, fy, start, end)
    if not start_d or not end_d:
        raise HTTPException(status_code=503, detail="Could not resolve performance date range")

    if margin.get("Status") == 200 and margin.get("Success"):
        m_success = margin["Success"]
        actual_margin_ute = float(m_success.get("actual_margin_ute", 0) or 0)
        actual_margin_avl = float(m_success.get("actual_margin_avl", 0) or 0)
        cash_limit = float(m_success.get("cash_limit", 0) or 0)
        total_margin = actual_margin_avl + abs(actual_margin_ute)
        if total_margin <= 0:
            total_margin = cash_limit
        performance = await asyncio.to_thread(breeze.get_performance, user_id, total_margin, start_d, end_d)
    else:
        performance = {
            "Status": margin.get("Status", 500),
            "Error": margin.get("Error"),
            "Success": None,
        }

    AuditLogger(None).log_operation(user_id, OperationType.PORTFOLIO_VIEW, "Performance")
    return PerformanceDataResponse(
        performance=performance if isinstance(performance, dict) else {},
        funds=funds if isinstance(funds, dict) else (funds or {}),
        margin=margin if isinstance(margin, dict) else {},
        years=years,
        fy=fy_label,
        start=start_d,
        end=end_d,
    )
