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

    years = breeze.get_financial_years() or []
    start_d, end_d, fy_label = _resolve_performance_range(years, fy, start, end)
    if not start_d or not end_d:
        raise HTTPException(status_code=503, detail="Could not resolve performance date range")

    margin = breeze.get_margin_situation(user_id, target_margin_ute=100)
    if margin.get("Status") == 200 and margin.get("Success"):
        cash_limit = margin["Success"].get("cash_limit", 0) or 0
        performance = breeze.get_performance(user_id, cash_limit, start_d, end_d)
    else:
        performance = {
            "Status": margin.get("Status", 500),
            "Error": margin.get("Error"),
            "Success": None,
        }

    funds = breeze.get_funds(user_id)
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
