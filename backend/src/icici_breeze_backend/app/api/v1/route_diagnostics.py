"""Self-service application log download for the deployment's own users."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel

from icici_breeze_backend.app.api.deps import get_current_user
from icici_breeze_backend.app.auth.context import RequestContext
from icici_breeze_backend.app.core.log_sink import (
    retention_days,
    sink_enabled,
    sink_level,
)
from icici_breeze_backend.app.services import log_bundle

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/diagnostics", tags=["diagnostics"])


class LogFileSummary(BaseModel):
    name: str
    size_bytes: int
    modified_at: float


class LogsStatus(BaseModel):
    enabled: bool
    retention_days: int
    level: str
    files: list[LogFileSummary]
    total_bytes: int


@router.get("/logs/status", response_model=LogsStatus)
async def logs_status(
    days: int = Query(default=7, ge=1, le=log_bundle.MAX_DAYS),
    _: RequestContext = Depends(get_current_user),
) -> LogsStatus:
    """What's available to download, so the UI can show a size before requesting it."""
    files = log_bundle.list_log_files(days)
    return LogsStatus(
        enabled=sink_enabled(),
        retention_days=retention_days(),
        level=logging.getLevelName(sink_level()),
        files=[
            LogFileSummary(
                name=f.name, size_bytes=f.size_bytes, modified_at=f.modified_at
            )
            for f in files
        ],
        total_bytes=sum(f.size_bytes for f in files),
    )


@router.get("/logs/download")
async def logs_download(
    days: int = Query(default=7, ge=1, le=log_bundle.MAX_DAYS),
    ctx: RequestContext = Depends(get_current_user),
) -> Response:
    """Zip of the deployment's application logs for the last `days` days.

    Deliberately not admin-gated: this is a customer's own instance and the bundle is
    deployment-scoped by design (see `services/log_bundle`). Authentication is still
    required — the files carry user ids and client IPs.
    """
    payload = log_bundle.build_zip(days)
    logger.info(
        "log bundle downloaded by %s (%s days, %s bytes)",
        ctx.user_id,
        log_bundle.clamp_days(days),
        len(payload),
    )
    return Response(
        content=payload,
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{log_bundle.bundle_filename(days)}"'
            ),
            # The bundle changes on every request; a cached copy would be misleading.
            "Cache-Control": "no-store",
        },
    )
