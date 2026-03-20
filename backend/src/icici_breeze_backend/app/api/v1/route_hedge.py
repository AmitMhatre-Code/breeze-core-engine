from fastapi import APIRouter
from fastapi import Request, Depends
from fastapi import HTTPException
from icici_breeze_backend.app.auth.context import get_request_context, get_request_context_or_redirect, RequestContext
from icici_breeze_backend.app.domain.responses import StockCodesResponse
from icici_breeze_backend.app.domain.trading import HedgeFormRequest
from icici_breeze_backend.audit.logger import AuditLogger, OperationType
from icici_breeze_backend.app.services.processor import processor
from icici_breeze_backend.app.api.frontend_redirect import redirect_to_frontend, json_redirect
import icici_breeze_backend.app.core.config as cfg

router = APIRouter()
breeze = processor()


@router.get("")
async def serve_landing(request: Request):
    q = request.url.query
    return redirect_to_frontend("/hedge" + ("?" + q if q else ""))


@router.post("")
async def process_post(
    body: HedgeFormRequest,
    context: RequestContext = Depends(get_request_context_or_redirect),
):
    if body.action == cfg.CLEAR:
        return json_redirect(f"/hedge?exchange_code={body.exchange_code or ''}")
    if body.action == cfg.HEDGE:
        return json_redirect(
            f"/hedge?right={body.right or ''}&action={body.position_action or ''}&stock_code={body.stock_code or ''}"
            f"&exchange_code={body.exchange_code or ''}&expiry_date={body.expiry_date or ''}&quantity={body.quantity or ''}"
            f"&strike_price={body.strike_price or ''}&top=3"
        )
    if body.action == cfg.BUY:
        return json_redirect(
            f"/order?action={body.action}&product_type={body.product_type or ''}&stock_code={body.stock_code or ''}"
            f"&exchange_code={body.exchange_code or ''}&expiry_date={body.expiry_date or ''}&right={body.right or ''}"
            f"&strike_price={body.strike_price or ''}&quantity={body.quantity or ''}"
        )

    return json_redirect("/hedge")


@router.get("/data", response_model=StockCodesResponse)
async def get_hedge_api(ctx: RequestContext = Depends(get_request_context)):
    user_id = ctx.user_id
    if not ctx.broker_token:
        raise HTTPException(status_code=401, detail="ICICI broker token missing; re-login required")
    stock_codes = breeze.fetch_stock_codes()
    AuditLogger(None).log_operation(user_id, OperationType.PORTFOLIO_VIEW, "Hedge")
    return StockCodesResponse(stock_codes=stock_codes or [])
