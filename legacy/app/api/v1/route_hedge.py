from fastapi import APIRouter
from fastapi import Request, Depends
from fastapi import responses
from fastapi import status
from fastapi import Form
from fastapi.templating import Jinja2Templates
from app.auth.context import get_request_context, get_request_context_or_redirect, RequestContext
from app.domain.responses import StockCodesResponse
from app.domain.trading import HedgeFormRequest
from audit.logger import AuditLogger, OperationType
from app.services.processor import processor
from app.api.error_utils import render_error_page
from app.api.v1.route_admin import get_common_template_vars
import app.core.config as cfg
import json


def get_hedge_form_request(
    product_type: str | None = Form(None),
    position_action: str | None = Form(None),
    stock_code: str | None = Form(None),
    exchange_code: str | None = Form(None),
    right: str | None = Form(None),
    strike_price: str | None = Form(None),
    quantity: str | None = Form(None),
    expiry_date: str | None = Form(None),
    top: int | None = Form(None),
    action: str | None = Form(None),
) -> HedgeFormRequest:
    return HedgeFormRequest(
        product_type=product_type,
        position_action=position_action,
        stock_code=stock_code,
        exchange_code=exchange_code,
        right=right,
        strike_price=strike_price,
        quantity=quantity,
        expiry_date=expiry_date,
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
    right: str | None = None,
    strike_price: str | None = None,
    quantity: str | None = None,
    action: str | None = None,
    top: int | None = None,
):
    user_id = context.user_id
    error = {}
    selected_exchange_code = exchange_code or cfg.NFO

    customer = breeze.get_customer_details(user_id)
    if customer is None:
        error['location'] = "In route_hedge.py --> serve_landing() get_customer_details returned None"
        error['contents'] = "get_customer_details() returned None for user_id = " + user_id
        breeze.store_error(error)
    elif customer['Status'] != 200:
        error['location'] = "In route_hedge.py --> serve_landing() get_customer_details failed"
        error['contents'] = "customer['status'] = " + str(customer['Status']) + " and customer['error'] = " + customer['Error']
        breeze.store_error(error)

    margin = breeze.get_margin_situation(user_id, target_margin_ute=100)
    if margin['Status'] != 200:
        error['location'] = "In route_hedge.py --> serve_landing() get_margin_situation failed"
        error['contents'] = "margin['status'] = " + str(margin['Status']) + " and margin['error'] = " + margin['Error']
        breeze.store_error(error)

    stock_codes = breeze.fetch_stock_codes(selected_exchange_code)
    if len(stock_codes) == 0:
        error['location'] = "In route_hedge.py --> serve_landing() fetch_stock_codes failed"
        error['contents'] = "stock_codes = " + json.dumps(stock_codes)
        breeze.store_error(error)

    errors = breeze.retrieve_errors()
    if len(errors) == 0:
        if right and action and stock_code and quantity and expiry_date and strike_price and top:
            position = {"right": right, "action": action, "stock_code": stock_code, "quantity": quantity, "expiry_date": expiry_date, "strike_price": strike_price, "top": top}
            hedges = breeze.hedge(
                user_id=user_id,
                right=right,
                action=action,
                stock_code=stock_code,
                quantity=quantity,
                expiry_date=expiry_date,
                strike_price=strike_price,
                top=top,
                exchange_code=selected_exchange_code,
            )
        else:
            position = None
            hedges = {}
        return templates.TemplateResponse(
            "hedge.html",
            {
                "request": request,
                "is_logged_in": True,
                "login_url": None,
                "active": "hedge",
                "customer": customer,
                "margin": margin,
                "stock_codes": stock_codes,
                "position": position,
                "hedges": hedges,
                "exchange_code": selected_exchange_code,
                **get_common_template_vars(context),
            },
        )
    else:
        return render_error_page(request, errors, active="hedge", log_context="route_hedge.serve_landing")


@router.post("")
async def process_post(
    request: Request,
    context: RequestContext = Depends(get_request_context_or_redirect),
    form: HedgeFormRequest = Depends(get_hedge_form_request),
):
    user_id = context.user_id

    if form.action == cfg.CLEAR:
        return responses.RedirectResponse(
            f"/hedge?exchange_code={form.exchange_code or ''}",
            status_code=status.HTTP_302_FOUND,
        )
    if form.action == cfg.HEDGE:
        return responses.RedirectResponse(
            url=f"/hedge?right={form.right or ''}&action={form.position_action or ''}&stock_code={form.stock_code or ''}&exchange_code={form.exchange_code or ''}&expiry_date={form.expiry_date or ''}&quantity={form.quantity or ''}&strike_price={form.strike_price or ''}&top={3}",
            status_code=status.HTTP_302_FOUND,
        )
    if form.action == cfg.BUY:
        return responses.RedirectResponse(
            url=f"/order?action={form.action}&product_type={form.product_type or ''}&stock_code={form.stock_code or ''}&exchange_code={form.exchange_code or ''}&expiry_date={form.expiry_date or ''}&right={form.right or ''}&strike_price={form.strike_price or ''}&quantity={form.quantity or ''}",
            status_code=status.HTTP_302_FOUND,
        )

    return responses.RedirectResponse("/hedge", status_code=status.HTTP_302_FOUND)


@router.get("/data", response_model=StockCodesResponse)
async def get_hedge_api(ctx: RequestContext = Depends(get_request_context)):
    user_id = ctx.user_id
    if not ctx.broker_token:
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="ICICI broker token missing; re-login required")
    stock_codes = breeze.fetch_stock_codes()
    AuditLogger(None).log_operation(user_id, OperationType.PORTFOLIO_VIEW, "Hedge")
    return StockCodesResponse(stock_codes=stock_codes or [])
