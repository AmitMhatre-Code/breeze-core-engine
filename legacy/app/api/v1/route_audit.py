"""Audit log admin API (Phase 6 T094)."""
import sqlite3

import app.core.config as cfg
from app.auth.context import get_request_context, RequestContext
from app.domain.responses import AuditLogEntryResponse, AuditLogListResponse
from fastapi import APIRouter, Depends, HTTPException, Query

router = APIRouter()


def require_admin(ctx: RequestContext = Depends(get_request_context)) -> RequestContext:
    """Dependency: require admin role for audit log access."""
    if "admin" not in (ctx.roles or []):
        raise HTTPException(status_code=403, detail="Admin role required")
    return ctx


@router.get("/logs", response_model=AuditLogListResponse)
async def get_audit_logs(
    ctx: RequestContext = Depends(require_admin),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    user_id: str | None = None,
    operation_type: str | None = None,
) -> AuditLogListResponse:
    """Return audit log entries for compliance (admin only). 30-day retention."""
    db_path = cfg.DATA_PATH + "db.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        query = "SELECT id, user_id, operation_type, resource_type, resource_id, action_status, timestamp, request_id, ip_address, error_details FROM audit_log WHERE 1=1"
        params = []
        if user_id:
            query += " AND user_id = ?"
            params.append(user_id)
        if operation_type:
            query += " AND operation_type = ?"
            params.append(operation_type)
        query += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        cur.execute(query, params)
        rows = cur.fetchall()
    entries = [AuditLogEntryResponse.model_validate(dict(r)) for r in rows]
    return AuditLogListResponse(entries=entries, count=len(entries))
