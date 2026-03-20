from fastapi import APIRouter
from fastapi import Request, Depends
from fastapi import responses
from fastapi import status
from fastapi.templating import Jinja2Templates
from app.auth.context import get_request_context, get_request_context_or_redirect, RequestContext
from app.domain.responses import PerformanceDataResponse
from audit.logger import AuditLogger, OperationType
from app.services.processor import processor
from app.api.error_utils import render_error_page
from app.api.v1.route_admin import get_common_template_vars

templates = Jinja2Templates(directory="templates")
templates.env.filters["inr"] = processor.format_inr

router = APIRouter()
breeze = processor()


@router.get("")
async def serve_landing(
    request: Request,
    context: RequestContext = Depends(get_request_context_or_redirect),
    start: str | None = None,
    end: str | None = None,
):
    user_id = context.user_id
    error = {}

    years = breeze.get_financial_years()
    if start is None or end is None:
        start = years[0]['start']
        end = years[0]['end']

    customer = breeze.get_customer_details(user_id)
    if customer is None:
        error['location'] = "In route_performance.py --> serve_landing() get_customer_details returned None"
        error['contents'] = "get_customer_details() returned None for user_id = " + user_id
        breeze.store_error(error)
    elif customer['Status'] != 200:
        error['location'] = "In route_performance.py --> serve_landing() get_customer_details failed"
        error['contents'] = "customer['status'] = " + str(customer['Status']) + " and customer['error'] = " + customer['Error']
        breeze.store_error(error)

    margin = breeze.get_margin_situation(user_id, target_margin_ute=100)
    performance = None
    if margin['Status'] != 200:
        error['location'] = "In route_performance.py --> serve_landing() get_margin_situation failed"
        error['contents'] = "margin['status'] = " + str(margin['Status']) + " and margin['error'] = " + margin['Error']
        breeze.store_error(error)
    else:
        perf_result = breeze.get_performance(user_id, margin['Success']['cash_limit'], start, end)
        if perf_result['Status'] != 200:
            error['location'] = "In route_performance.py --> serve_landing() get_performance failed"
            error['contents'] = "performance['status'] = " + str(perf_result['Status']) + " and performance['error'] = " + perf_result.get('Error', '')
            breeze.store_error(error)
        else:
            performance = perf_result

    funds = breeze.get_funds(user_id)
    if funds['Status'] != 200:
        error['location'] = "In route_performance.py --> serve_landing() get_funds failed"
        error['contents'] = "funds['status'] = " + str(funds['Status']) + " and funds['error'] = " + funds['Error']
        breeze.store_error(error)

    errors = breeze.retrieve_errors()
    if len(errors) == 0:
        return templates.TemplateResponse("performance.html", {"request": request, "is_logged_in": True, "login_url": None, "active": "performance", "customer": customer, "margin": margin, "funds": funds, "performance": performance, "years": years, **get_common_template_vars(context)})
    else:
        return render_error_page(request, errors, active="performance", log_context="route_performance.serve_landing")


@router.get("/data", response_model=PerformanceDataResponse)
async def get_performance_api(ctx: RequestContext = Depends(get_request_context)):
    user_id = ctx.user_id
    if not ctx.broker_token:
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="ICICI broker token missing; re-login required")
    performance = breeze.get_performance(user_id, 0, None, None)
    funds = breeze.get_funds(user_id)
    AuditLogger(None).log_operation(user_id, OperationType.PORTFOLIO_VIEW, "Performance")
    return PerformanceDataResponse(
        performance=performance or {},
        funds=funds if isinstance(funds, dict) else (funds or {}),
    )
