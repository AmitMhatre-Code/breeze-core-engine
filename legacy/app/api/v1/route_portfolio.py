from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi import responses
from fastapi import status
from fastapi import Form
from fastapi.templating import Jinja2Templates
import datetime
from app.services.processor import processor
import app.core.config as cfg
from app.auth.context import get_request_context, get_request_context_or_redirect, RequestContext
from app.domain.portfolio import PortfolioActionRequest
from app.domain.responses import IciciApiResponse
from audit.logger import AuditLogger
from app.api.v1.route_admin import get_common_template_vars


def get_portfolio_form_request(
    product_type: str = Form(...),
    stock_code: str = Form(...),
    exchange_code: str = Form(""),
    position_action: str = Form(...),
    expiry_date: str = Form(...),
    right: str = Form(...),
    strike_price: str = Form(...),
    quantity: str = Form(...),
    action: str = Form(...),
) -> PortfolioActionRequest:
    return PortfolioActionRequest(
        product_type=product_type,
        stock_code=stock_code,
        exchange_code=exchange_code,
        position_action=position_action,
        expiry_date=expiry_date,
        right=right,
        strike_price=strike_price,
        quantity=quantity,
        action=action,
    )

templates = Jinja2Templates(directory="templates")
router = APIRouter()
breeze = processor()

@router.get("")
async def serve_landing(request: Request, context: RequestContext = Depends(get_request_context_or_redirect)):
    """Serve portfolio page for authenticated user with JWT context.
    
    FR-001: User data must be isolated by user_id from JWT token.
    FR-002: RequestContext extracted from JWT, not session.
    FR-005: Routes uses context to fetch user-specific data.
    FR-006: Audit log portfolio access.
    """
    user_id = context.user_id
    if not context.broker_token:
        raise HTTPException(status_code=401, detail="ICICI broker token missing; re-login required")
    warnings = []

    customer = breeze.get_customer_details(user_id)
    if customer is None:
        breeze.store_error({"location": "route_portfolio.serve_landing get_customer_details", "contents": f"get_customer_details() returned None for user_id = {user_id}"})
        warnings.append("Customer details could not be loaded.")
        customer = {"Status": 400, "Error": "Not available", "Success": {"idirect_user_name": "—"}}
    elif customer.get("Status") != 200:
        breeze.store_error({"location": "route_portfolio.serve_landing get_customer_details", "contents": f"customer Status={customer.get('Status')} Error={customer.get('Error', '')}"})
        warnings.append("Customer details could not be loaded.")

    margin = breeze.get_margin_situation(user_id, target_margin_ute=100)
    if margin.get("Status") != 200:
        breeze.store_error({"location": "route_portfolio.serve_landing get_margin_situation", "contents": f"margin Status={margin.get('Status')} Error={margin.get('Error', '')}"})
        warnings.append("Margin could not be loaded.")
        margin = {"Status": 400, "Error": margin.get("Error", ""), "Success": {"last_refresh": "—", "actual_margin_ute": 0, "cash_limit": 0, "actual_margin_avl": 0, "target_margin_free": 0, "limits": 0}}

    positions = breeze.get_positions(user_id)
    AuditLogger(None).log_portfolio_access(user_id)
    if positions.get("Status") != 200:
        breeze.store_error({"location": "route_portfolio.serve_landing get_positions", "contents": f"positions Status={positions.get('Status')} Error={positions.get('Error', '')}"})
        warnings.append("Positions could not be loaded.")
        positions = {"Status": 400, "Error": positions.get("Error", ""), "Success": []}

    return templates.TemplateResponse("portfolio.html", {
        "request": request,
        "is_logged_in": True,
        "login_url": None,
        "active": "portfolio",
        "customer": customer,
        "margin": margin,
        "positions": positions,
        "user_id": user_id,
        "warnings": warnings,
        **get_common_template_vars(context),
    })

@router.post("")
async def process_post(
    request: Request,
    context: RequestContext = Depends(get_request_context_or_redirect),
    form: PortfolioActionRequest = Depends(get_portfolio_form_request),
):
    """Process portfolio POST request with authenticated user context.
    
    FR-001: User isolation enforced via context.user_id.
    FR-002: RequestContext from JWT, not session.
    FR-006: Audit log state changes.
    """
    user_id = context.user_id
    if not context.broker_token:
        raise HTTPException(status_code=401, detail="ICICI broker token missing; re-login required")

    if form.action == cfg.SQUAREOFF:
        return responses.RedirectResponse(
            url=f"/order?action={form.action}&position={form.position_action}&product_type={form.product_type}&stock_code={form.stock_code}&exchange_code={form.exchange_code}&expiry_date={form.expiry_date}&right={form.right}&strike_price={form.strike_price}&quantity={form.quantity}",
            status_code=status.HTTP_302_FOUND
        )
    
    if form.action == cfg.HEDGE:
        return responses.RedirectResponse(
            url=f"/hedge?action={form.position_action}&product_type={form.product_type}&stock_code={form.stock_code}&exchange_code={form.exchange_code}&expiry_date={form.expiry_date}&right={form.right}&strike_price={form.strike_price}&quantity={form.quantity}&top={3}",
            status_code=status.HTTP_302_FOUND
        )
# JWT-protected API endpoint returning JSON portfolio data (T080 - real-time from ICICI API)
@router.get("/data", response_model=IciciApiResponse)
async def get_portfolio_api(ctx: RequestContext = Depends(get_request_context)):
    """Return portfolio positions for authenticated user (real-time from ICICI API).
    
    Fetches real-time portfolio data directly from ICICI Breeze API.
    No database caching - always fresh data (FR-010).
    ICICI API call counts and latencies are tracked via /metrics endpoint.
    """
    user_id = ctx.user_id
    if not ctx.broker_token:
        raise HTTPException(status_code=401, detail="ICICI broker token missing; re-login required")
    token_str = ctx.broker_token

    from app.services.processor import get_portfolio_realtime
    
    data = get_portfolio_realtime(broker_token=token_str, user_id=user_id)
    AuditLogger(None).log_portfolio_access(user_id)
    return IciciApiResponse.model_validate(data)
