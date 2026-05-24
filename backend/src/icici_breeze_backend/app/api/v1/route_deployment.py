"""Deployment metadata routes (license status from portal heartbeat cache)."""
from fastapi import APIRouter, Depends

from icici_breeze_backend.app.auth.context import RequestContext, get_request_context
from icici_breeze_backend.app.domain.responses import DeploymentLicenseStatusResponse
from icici_breeze_backend.app.services.deployment_license_status import (
    get_license_status_for_api,
)

router = APIRouter(tags=["deployment"])


@router.get("/deployment/license-status", response_model=DeploymentLicenseStatusResponse)
async def get_deployment_license_status(
    ctx: RequestContext = Depends(get_request_context),
):
    """JWT-authenticated license probe; does not require ICICI broker session."""
    _ = ctx
    fields = get_license_status_for_api()
    if not fields:
        return DeploymentLicenseStatusResponse()
    return DeploymentLicenseStatusResponse(**fields)
