"""Market data WebSocket subscription lifecycle API."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from icici_breeze_backend.app.auth.context import RequestContext, get_request_context
from icici_breeze_backend.app.domain.settings_api import WsReleaseRequest
from icici_breeze_backend.app.services.breeze_websocket_manager import release_holder

router = APIRouter(prefix="/market-data", tags=["market-data"])


@router.post("/ws/release")
async def release_ws_subscriptions(
    body: WsReleaseRequest,
    ctx: RequestContext = Depends(get_request_context),
):
    if not ctx.broker_token:
        raise HTTPException(status_code=401, detail="ICICI broker token missing; re-login required")
    result = release_holder(body.holder_id.strip())
    return {"Status": 200, "Error": None, "Success": result}
