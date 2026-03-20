from fastapi import APIRouter
from fastapi import Request, Depends
from fastapi import HTTPException
from icici_breeze_backend.app.auth.context import get_request_context, get_request_context_or_redirect, RequestContext
from icici_breeze_backend.app.domain.responses import UncoveredShortsDataResponse
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
