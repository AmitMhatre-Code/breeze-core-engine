from fastapi import APIRouter
from fastapi import Request, Depends
from fastapi import responses
from fastapi import status
from fastapi import Form
from fastapi.templating import Jinja2Templates
from app.auth.context import get_request_context, get_request_context_or_redirect, RequestContext
from app.domain.responses import StockCodesResponse
from app.domain.trading import VerticalSpreadFormRequest
from audit.logger import AuditLogger, OperationType
from app.services.processor import processor
from app.api.error_utils import render_error_page
from app.api.v1.route_admin import get_common_template_vars
import app.core.config as cfg
import json


def get_vertical_spread_form_request(
    product_type: str | None = Form(None),
    stock_code: str | None = Form(None),
    exchange_code: str | None = Form(None),
    right: str | None = Form(None),
    strike_price: str | None = Form(None),
    quantity: str | None = Form(None),
    expiry_date: str | None = Form(None),
    limits: int | None = Form(None),
    provision_elm: str | None = Form(None),
    range_lower: int | None = Form(None),
    range_upper: int | None = Form(None),
    top: int | None = Form(None),
    action: str | None = Form(None),
) -> VerticalSpreadFormRequest:
    return VerticalSpreadFormRequest(
        product_type=product_type,
        stock_code=stock_code,
        exchange_code=exchange_code,
        right=right,
        strike_price=strike_price,
        quantity=quantity,
        expiry_date=expiry_date,
        limits=limits,
        provision_elm=provision_elm,
        range_lower=range_lower,
        range_upper=range_upper,
        top=top,
        action=action,
    )

templates = Jinja2Templates(directory="templates")
router = APIRouter()
breeze = processor()


@router.get("")
async def serve_landing(
    request: Request,
    context: RequestContext = Depends(get_request_context_or_redirect),
    stock_code: str | None = None,
    exchange_code: str | None = None,
    expiry_date: str | None = None,
    limits: int | None = None,
    elm: str | None = None,
    range_lower: int | None = None,
    range_upper: int | None = None,
    top: int | None = None,
):
    user_id = context.user_id
    error = {}
    selected_exchange_code = exchange_code or cfg.NFO

    customer = breeze.get_customer_details(user_id)
    if customer is None:
        error['location'] = "In route_vertical_spread.py --> serve_landing() get_customer_details returned None"
        error['contents'] = "get_customer_details() returned None for user_id = " + user_id
        breeze.store_error(error)
    elif customer['Status'] != 200:
        error['location'] = "In route_vertical_spread.py --> serve_landing() get_customer_details failed"
        error['contents'] = "customer['status'] = " + str(customer['Status']) + " and customer['error'] = " + customer['Error']
        breeze.store_error(error)

    margin = breeze.get_margin_situation(user_id, target_margin_ute=100)
    if margin['Status'] != 200:
        error['location'] = "In route_vertical_spread.py --> serve_landing() get_margin_situation failed"
        error['contents'] = "margin['status'] = " + str(margin['Status']) + " and margin['error'] = " + margin['Error']
        breeze.store_error(error)

    stock_codes = breeze.fetch_stock_codes(selected_exchange_code)
    if len(stock_codes) == 0:
        error['location'] = "In route_vertical_spread.py --> serve_landing() fetch_stock_codes failed"
        error['contents'] = "stock_codes = " + json.dumps(stock_codes)
        breeze.store_error(error)

    errors = breeze.retrieve_errors()
    if len(errors) == 0:
        if stock_code and expiry_date and limits and range_lower is not None and range_upper is not None and top is not None:
            parameters = {"stock_code": stock_code, "expiry_date": expiry_date, "limits": limits, "elm": elm, "range_lower": range_lower, "range_upper": range_upper, "top": top}
            trades = breeze.strat_bull_spread(
                user_id,
                stock_code=stock_code,
                expiry_date=expiry_date,
                limits=limits,
                elm=elm,
                range_lower=range_lower,
                range_upper=range_upper,
                top=top,
                exchange_code=selected_exchange_code,
            )
        else:
            parameters = None
            trades = None
        return templates.TemplateResponse(
            "vertical_spread.html",
            {
                "request": request,
                "is_logged_in": True,
                "login_url": None,
                "active": "vertical_spread",
                "customer": customer,
                "margin": margin,
                "trades": trades,
                "parameters": parameters,
                "stock_codes": stock_codes,
                "exchange_code": selected_exchange_code,
                **get_common_template_vars(context),
            },
        )
    else:
        return render_error_page(request, errors, active="vertical_spread", log_context="route_vertical_spread.serve_landing")


@router.post("")
async def process_post(
    request: Request,
    context: RequestContext = Depends(get_request_context_or_redirect),
    form: VerticalSpreadFormRequest = Depends(get_vertical_spread_form_request),
):
    user_id = context.user_id
    errors = breeze.retrieve_errors()

    if form.action == cfg.CLEAR:
        if len(errors) == 0:
            return responses.RedirectResponse(
                f"/vertical-spread?exchange_code={form.exchange_code or cfg.NFO}",
                status_code=status.HTTP_302_FOUND,
            )
        return render_error_page(request, errors, active="vertical_spread", log_context="route_vertical_spread.process_post CLEAR")

    if form.action == cfg.OPTIMIZE:
        return responses.RedirectResponse(
            url=f"/vertical-spread?exchange_code={form.exchange_code or cfg.NFO}&stock_code={form.stock_code or ''}&expiry_date={form.expiry_date or ''}&limits={form.limits or ''}&elm={form.provision_elm or ''}&range_lower={form.range_lower or ''}&range_upper={form.range_upper or ''}&top={form.top or ''}",
            status_code=status.HTTP_302_FOUND,
        )

    if form.action == cfg.SELL:
        if len(errors) == 0:
            return responses.RedirectResponse(
                url=f"/order?action={form.action}&product_type={form.product_type or ''}&exchange_code={form.exchange_code or cfg.NFO}&stock_code={form.stock_code or ''}&expiry_date={form.expiry_date or ''}&right={form.right or ''}&strike_price={form.strike_price or ''}&quantity={form.quantity or ''}",
                status_code=status.HTTP_302_FOUND,
            )
        return render_error_page(request, errors, active="vertical_spread", log_context="route_vertical_spread.process_post SELL")

    return responses.RedirectResponse("/vertical-spread", status_code=status.HTTP_302_FOUND)


@router.get("/data", response_model=StockCodesResponse)
async def get_vertical_spread_api(ctx: RequestContext = Depends(get_request_context)):
    user_id = ctx.user_id
    if not ctx.broker_token:
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="ICICI broker token missing; re-login required")
    stock_codes = breeze.fetch_stock_codes()
    AuditLogger(None).log_operation(user_id, OperationType.PORTFOLIO_VIEW, "VerticalSpread")
    return StockCodesResponse(stock_codes=stock_codes or [])
