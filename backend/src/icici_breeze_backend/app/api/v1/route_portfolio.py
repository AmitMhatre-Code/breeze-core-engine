from fastapi import APIRouter, Request, Depends, HTTPException
from icici_breeze_backend.app.services.processor import processor
import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.auth.context import get_request_context, get_request_context_or_redirect, RequestContext
from icici_breeze_backend.app.domain.portfolio import PortfolioActionRequest
from icici_breeze_backend.app.domain.responses import IciciApiResponse
from icici_breeze_backend.audit.logger import AuditLogger
from icici_breeze_backend.app.api.frontend_redirect import redirect_to_frontend, json_redirect


router = APIRouter()
breeze = processor()


@router.get("")
async def serve_landing(request: Request):
    q = request.url.query
    return redirect_to_frontend("/portfolio" + ("?" + q if q else ""))


@router.post("")
async def process_post(
    body: PortfolioActionRequest,
    context: RequestContext = Depends(get_request_context_or_redirect),
):
    if not context.broker_token:
        raise HTTPException(status_code=401, detail="ICICI broker token missing; re-login required")

    if body.action == cfg.SQUAREOFF:
        return json_redirect(
            f"/order?action={body.action}&position={body.position_action}&product_type={body.product_type}"
            f"&stock_code={body.stock_code}&exchange_code={body.exchange_code}&expiry_date={body.expiry_date}"
            f"&right={body.right}&strike_price={body.strike_price}&quantity={body.quantity}"
        )

    if body.action == cfg.HEDGE:
        return json_redirect(
            f"/hedge?action={body.position_action}&product_type={body.product_type}&stock_code={body.stock_code}"
            f"&exchange_code={body.exchange_code}&expiry_date={body.expiry_date}&right={body.right}"
            f"&strike_price={body.strike_price}&quantity={body.quantity}&top=3"
        )
    return json_redirect("/portfolio")


@router.get("/data", response_model=IciciApiResponse)
async def get_portfolio_api(ctx: RequestContext = Depends(get_request_context)):
    user_id = ctx.user_id
    if not ctx.broker_token:
        raise HTTPException(status_code=401, detail="ICICI broker token missing; re-login required")
    token_str = ctx.broker_token

    from icici_breeze_backend.app.services.processor import get_portfolio_realtime

    data = get_portfolio_realtime(broker_token=token_str, user_id=user_id)
    AuditLogger(None).log_portfolio_access(user_id)
    return IciciApiResponse.model_validate(data)
