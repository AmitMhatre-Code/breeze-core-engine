from fastapi import APIRouter
from fastapi import Request, Depends
from icici_breeze_backend.app.auth.context import get_request_context_or_redirect, RequestContext
from icici_breeze_backend.app.domain.order import BookActionRequest
from icici_breeze_backend.app.services.processor import processor
from icici_breeze_backend.app.api.frontend_redirect import redirect_to_frontend, json_redirect
import icici_breeze_backend.app.core.config as cfg

router = APIRouter()
breeze = processor()


@router.get("")
async def serve_landing(request: Request):
    q = request.url.query
    return redirect_to_frontend("/orders" + ("?" + q if q else ""))


@router.post("")
async def process_post(
    body: BookActionRequest,
    context: RequestContext = Depends(get_request_context_or_redirect),
):
    user_id = context.user_id

    if body.action == cfg.CANCEL and body.order_ids:
        messages = breeze.cancel_orders(user_id, body.order_ids)
        breeze.store_messages(user_id, messages)
        return json_redirect("/orders")
    return json_redirect(f"/orders?start={body.start or ''}&end={body.end or ''}")
