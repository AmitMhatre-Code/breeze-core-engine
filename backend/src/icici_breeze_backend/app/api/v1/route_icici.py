"""ICICI broker pacing status for frontend polling."""
from fastapi import APIRouter, Depends, HTTPException

from icici_breeze_backend.app.auth.context import RequestContext, get_request_context
from icici_breeze_backend.app.domain.responses import IciciPacingStatusResponse
from icici_breeze_backend.app.services.icici_api_pacing import GlobalIciciApiPacer

router = APIRouter(prefix="/api/icici", tags=["icici"])


@router.get("/pacing-status", response_model=IciciPacingStatusResponse)
async def get_icici_pacing_status(ctx: RequestContext = Depends(get_request_context)):
    if not ctx.broker_token:
        raise HTTPException(status_code=401, detail="ICICI broker token missing; re-login required")
    snap = GlobalIciciApiPacer.get_backoff_snapshot(ctx.user_id)
    if snap is None:
        return IciciPacingStatusResponse()
    return IciciPacingStatusResponse(
        throttling_active=snap.throttling_active,
        backing_off=snap.active and snap.seconds_remaining > 0,
        reason=snap.reason or None,
        seconds_remaining=max(0, snap.seconds_remaining),
    )
