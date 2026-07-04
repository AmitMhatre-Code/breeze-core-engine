"""Settings JSON API under /api/settings."""
import datetime
import json
import logging
import os
import sqlite3
from typing import Any, List

import httpx
import time
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response

from icici_breeze_backend.app.services.market_calendar import (
    is_market_open as is_user_market_open,
    market_closed_reason as user_market_closed_reason,
)
import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.auth.context import get_request_context, RequestContext
from icici_breeze_backend.app.services.processor import processor
from icici_breeze_backend.app.auth.credentials import CredentialManager
from icici_breeze_backend.app.auth.user_account import change_user_id
from icici_breeze_backend.app.domain.breeze_api_tester_catalog import (
    ALLOWED_METHODS,
    build_invoke_args_permissive,
    get_catalog_response,
)
from icici_breeze_backend.app.domain.settings_api import (
    ApiUsagePreferencesResponse,
    ApiUsagePreferencesUpdateBody,
    ApiUsageStateResponse,
    CredentialsStateResponse,
    CredentialsUpdateBody,
    MarginSourceStateResponse,
    MarginSourceUpdateBody,
    BreezeApiTesterCatalogEntry,
    BreezeApiTesterCatalogResponse,
    BreezeApiTesterInvokeBody,
    BreezeApiTesterInvokeResponse,
    StrategyBuilderAuditLogItem,
    StrategyBuilderAuditLogsResponse,
    StrategyBuilderAuditExplainabilityResponse,
    BreezeApiTesterRiskStatusResponse,
    ExchangeCalendarAddHolidayBody,
    ExchangeCalendarHolidayItem,
    ExchangeCalendarStateResponse,
    ExchangeCalendarSyncBody,
    ExchangeCalendarSyncPreviewResponse,
    ExchangeCalendarUpdateBody,
    ExchangeCalendarWorkingHours,
    MarketStatusResponse,
    QuantityLimitsStateResponse,
    QuantityLimitsUpdateBody,
    ScripMasterStateResponse,
    ReferenceDataLoadsStateResponse,
    ReferenceDataScheduleUpdateBody,
    BreezeApiTesterWsSubscribeBody,
    WsReleaseRequest,
)
from icici_breeze_backend.app.services.breeze_api_tester_risk import (
    get_breeze_api_tester_risk_accepted_at,
    is_breeze_api_tester_risk_accepted,
    set_breeze_api_tester_risk_accepted,
)
from icici_breeze_backend.app.services.api_usage import (
    get_daily_usage_by_api,
    get_daily_usage_by_category,
    get_daily_usage_by_route,
)
from icici_breeze_backend.app.services.user_rate_limit_prefs import (
    get_icici_rate_limit_pause_seconds,
    set_icici_rate_limit_pause_seconds,
)
from icici_breeze_backend.app.core.timezone import today_ist_date
from icici_breeze_backend.app.services.nsccl_baseline import (
    MARGIN_SOURCE_BREEZE,
    MARGIN_SOURCE_EXCHANGE,
    ensure_exchange_margin_baseline_table,
    ingest_exchange_baseline_upload,
    refresh_exchange_risk_baseline,
)
from icici_breeze_backend.app.repositories import user_exchange_calendar as uec_repo
from icici_breeze_backend.app.services.portal_exchange_calendar import (
    fetch_console_exchange_calendar,
    portal_exchange_calendar_configured,
)
from icici_breeze_backend.audit.strategy_builder_audit import (
    _MAX_AUDIT_LOGS_PER_USER,
    build_audit_zip_for_user,
    list_audit_log_index_for_user,
    resolve_audit_file_for_user,
    resolve_explainability_for_session,
)

router = APIRouter(prefix="/api/settings", tags=["settings"])
_logger = logging.getLogger(__name__)
breeze = processor()
cred_manager = CredentialManager(encryption_key=(cfg.JWT_SECRET or "").strip())
_BREEZE_API_TESTER_INVOKE_LAST_TS: dict[str, float] = {}
_BREEZE_API_TESTER_INVOKE_MIN_INTERVAL_SEC = 2.0



def _ensure_user_margin_source_column() -> None:
    with sqlite3.connect(cfg.DATA_PATH + cfg.USERS_DB) as conn:
        try:
            conn.execute(
                "ALTER TABLE user_account ADD COLUMN strategy_builder_margin_source TEXT NOT NULL DEFAULT 'breeze_api'"
            )
            conn.commit()
        except sqlite3.OperationalError:
            pass


def _get_user_margin_source(user_id: str) -> str:
    _ensure_user_margin_source_column()
    with sqlite3.connect(cfg.DATA_PATH + cfg.USERS_DB) as conn:
        row = conn.execute(
            "SELECT strategy_builder_margin_source FROM user_account WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    source = (row[0] if row and row[0] else MARGIN_SOURCE_BREEZE).strip().lower()
    if source not in (MARGIN_SOURCE_BREEZE, MARGIN_SOURCE_EXCHANGE):
        return MARGIN_SOURCE_BREEZE
    return source


def _set_user_margin_source(user_id: str, source: str) -> None:
    _ensure_user_margin_source_column()
    with sqlite3.connect(cfg.DATA_PATH + cfg.USERS_DB) as conn:
        conn.execute(
            "UPDATE user_account SET strategy_builder_margin_source = ? WHERE user_id = ?",
            (source, user_id),
        )
        conn.commit()


def _latest_baseline_meta() -> dict[str, Any]:
    ensure_exchange_margin_baseline_table()
    out: dict[str, Any] = {"exchanges": {}}
    with sqlite3.connect(cfg.DATA_PATH + cfg.SCRIP_DB) as conn:
        for ex in (cfg.NFO, cfg.BFO):
            row = conn.execute(
                """
                SELECT source_file, source_date, source_version, refreshed_at, COUNT(*)
                FROM exchange_margin_baseline
                WHERE exchange_code = ?
                GROUP BY source_file, source_date, source_version, refreshed_at
                ORDER BY source_date DESC, source_version DESC
                LIMIT 1
                """,
                (ex,),
            ).fetchone()
            if row:
                out["exchanges"][ex] = {
                    "source_file": row[0],
                    "source_date": row[1],
                    "source_version": row[2],
                    "refreshed_at": row[3],
                    "rows": row[4],
                }
    primary = out["exchanges"].get(cfg.NFO) or out["exchanges"].get(cfg.BFO)
    if primary:
        out.update(primary)
    return out


def _scrip_master_meta() -> dict[str, Any]:
    master = breeze.get_ICICImaster_date()
    if master.get("Status") != 200 or not master.get("Success"):
        return {
            "master_date": None,
            "master_age_days": None,
            "has_past_expiries": False,
            "past_expiries_count": 0,
            "message": master.get("Error") or "Scrip master not loaded.",
        }

    success = master.get("Success") or {}
    master_date = success.get("date")
    master_age_days = success.get("age")
    past_expiries_count = 0
    parse_errors = 0
    today = today_ist_date()

    with sqlite3.connect(cfg.DATA_PATH + cfg.SCRIP_DB) as conn:
        try:
            rows = conn.execute(
                "SELECT DISTINCT ExpiryDate FROM scrip_master WHERE ExpiryDate IS NOT NULL AND TRIM(ExpiryDate) != ''"
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []

    for (raw_expiry,) in rows:
        expiry = str(raw_expiry or "").strip()
        if not expiry:
            continue
        try:
            exp_date = datetime.datetime.strptime(expiry, "%d-%b-%Y").date()
            if exp_date < today:
                past_expiries_count += 1
        except ValueError:
            parse_errors += 1

    message = None
    if parse_errors > 0:
        message = f"Could not parse {parse_errors} expiry date value(s)."

    return {
        "master_date": master_date,
        "master_age_days": int(master_age_days) if master_age_days is not None else None,
        "has_past_expiries": past_expiries_count > 0,
        "past_expiries_count": past_expiries_count,
        "message": message,
    }


def _customer_margin_defaults(user_id: str) -> tuple[dict, dict]:
    customer = breeze.get_customer_details(user_id)
    if customer is None:
        customer = {"Status": 400, "Error": "Not available", "Success": {"idirect_user_name": "—"}}
    elif customer.get("Status") != 200:
        customer = {"Status": 400, "Error": customer.get("Error", ""), "Success": {"idirect_user_name": "—"}}

    margin = breeze.get_margin_situation(user_id, target_margin_ute=100)
    if margin.get("Status") != 200:
        margin = {
            "Status": 400,
            "Error": margin.get("Error", ""),
            "Success": {
                "last_refresh": "—",
                "actual_margin_ute": 0,
                "cash_limit": 0,
                "actual_margin_avl": 0,
                "target_margin_free": 0,
                "limits": 0,
            },
        }
    return customer, margin


@router.get("/credentials/data", response_model=CredentialsStateResponse)
async def settings_credentials_data(ctx: RequestContext = Depends(get_request_context)):
    customer, margin = _customer_margin_defaults(ctx.user_id)
    return CredentialsStateResponse(customer=customer, margin=margin, user_id=ctx.user_id)


@router.post("/credentials")
async def settings_credentials_post(
    body: CredentialsUpdateBody,
    ctx: RequestContext = Depends(get_request_context),
):
    user_id = (body.user_id or "").strip()
    api_key = (body.api_key or "").strip()
    secret_fragment = (body.secret_fragment or "").strip()
    if not user_id or not api_key or not secret_fragment:
        raise HTTPException(status_code=400, detail="user_id, api_key, and secret_fragment are required")
    if user_id == ctx.user_id:
        if cred_manager.update_credentials(ctx.user_id, api_key, secret_fragment):
            return JSONResponse({"ok": True, "message": "Credentials saved. Log out and log in again via ICICI to use the new API key."})
        raise HTTPException(status_code=400, detail="Could not save credentials")
    with sqlite3.connect(cfg.DATA_PATH + cfg.USERS_DB) as conn:
        row = conn.execute(
            "SELECT roles FROM user_account WHERE user_id = ?",
            (ctx.user_id,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=400, detail="No account linked")
    roles = row[0] or '["trader"]'
    try:
        with sqlite3.connect(cfg.DATA_PATH + cfg.USERS_DB) as conn:
            if change_user_id(
                conn,
                ctx.user_id,
                user_id,
                roles,
                cred_manager,
                api_key,
                secret_fragment,
            ):
                return JSONResponse({"ok": True, "redirect": "/logout", "message": "User id changed; please sign in again."})
    except sqlite3.IntegrityError:
        pass
    raise HTTPException(status_code=409, detail="That user id is already taken")


def _load_quantity_limits() -> tuple[list[dict[str, Any]], str | None]:
    limits: List[dict[str, Any]] = []
    warn: str | None = None
    with sqlite3.connect(cfg.DATA_PATH + cfg.SCRIP_DB) as conn:
        try:
            rows = conn.execute(
                """
                SELECT
                  rl.SegmentCode as segment_code,
                  rl.InstrumentName as instrument_name,
                  rl.ShortName as short_name,
                  rl.ExchangeCode as exchange_code,
                  rl.QtyLimit as qty_limit,
                  (SELECT sm.LotSize FROM scrip_master sm
                   WHERE sm.ShortName = rl.ShortName AND sm.ExchangeCode = rl.ExchangeCode
                     AND (sm.SegmentCode = rl.SegmentCode OR (rl.SegmentCode IS NULL AND sm.SegmentCode IS NULL))
                   LIMIT 1) as lot_size
                FROM raw_limits_data rl
                ORDER BY rl.SegmentCode, rl.ShortName, rl.ExchangeCode
                """
            ).fetchall()

            for segment_code, instrument_name, short_name, exchange_code, qty_limit, lot_size in rows:
                limits.append(
                    {
                        "segment_code": segment_code or "",
                        "instrument_name": instrument_name or "",
                        "short_name": short_name or "",
                        "exchange_code": exchange_code or "",
                        "qty_limit": int(qty_limit) if qty_limit is not None else 0,
                        "lot_size": int(lot_size) if lot_size is not None else None,
                    }
                )
        except sqlite3.OperationalError:
            warn = "Quantity limits not available. Ensure master data has been loaded."
    return limits, warn


@router.get("/quantity-limits/data", response_model=QuantityLimitsStateResponse)
async def settings_quantity_limits_data(ctx: RequestContext = Depends(get_request_context)):
    customer, margin = _customer_margin_defaults(ctx.user_id)
    limits, warn = _load_quantity_limits()
    return QuantityLimitsStateResponse(
        customer=customer,
        margin=margin,
        limits=limits,
        message=warn,
        user_id=ctx.user_id,
    )


@router.get("/api-usage/data", response_model=ApiUsageStateResponse)
async def settings_api_usage_data(
    days: int = Query(default=30, ge=1, le=120),
    ctx: RequestContext = Depends(get_request_context),
):
    return ApiUsageStateResponse(
        user_id=ctx.user_id,
        days=days,
        by_api=get_daily_usage_by_api(ctx.user_id, days=days),
        by_route=get_daily_usage_by_route(ctx.user_id, days=days),
        by_category=get_daily_usage_by_category(ctx.user_id, days=days),
        rate_limit_pause_seconds=get_icici_rate_limit_pause_seconds(ctx.user_id),
    )


@router.get("/api-usage/preferences", response_model=ApiUsagePreferencesResponse)
async def settings_api_usage_preferences_get(ctx: RequestContext = Depends(get_request_context)):
    return ApiUsagePreferencesResponse(
        user_id=ctx.user_id,
        rate_limit_pause_seconds=get_icici_rate_limit_pause_seconds(ctx.user_id),
    )


@router.post("/api-usage/preferences", response_model=ApiUsagePreferencesResponse)
async def settings_api_usage_preferences_post(
    body: ApiUsagePreferencesUpdateBody,
    ctx: RequestContext = Depends(get_request_context),
):
    v = set_icici_rate_limit_pause_seconds(ctx.user_id, body.rate_limit_pause_seconds)
    return ApiUsagePreferencesResponse(user_id=ctx.user_id, rate_limit_pause_seconds=v)


@router.post("/quantity-limits")
async def settings_quantity_limits_post(
    body: QuantityLimitsUpdateBody,
    ctx: RequestContext = Depends(get_request_context),
):
    if not body.rows:
        raise HTTPException(status_code=400, detail="rows are required")
    with sqlite3.connect(cfg.DATA_PATH + cfg.SCRIP_DB) as conn:
        cur = conn.cursor()
        for row in body.rows:
            seg = (row.segment_code or "").strip() or None
            cur.execute(
                """
                UPDATE raw_limits_data
                SET QtyLimit = ?
                WHERE ShortName = ? AND ExchangeCode = ? AND SegmentCode = ?
                """,
                (int(row.qty_limit), row.short_name, row.exchange_code, seg),
            )
            cur.execute(
                """
                UPDATE scrip_master
                SET QuantityLimit = ?
                WHERE ShortName = ? AND ExchangeCode = ? AND SegmentCode = ?
                """,
                (int(row.qty_limit), row.short_name, row.exchange_code, seg),
            )
        conn.commit()
    return JSONResponse({"ok": True, "message": "Quantity limits updated."})


@router.get("/margin-source/data", response_model=MarginSourceStateResponse)
async def settings_margin_source_data(ctx: RequestContext = Depends(get_request_context)):
    return MarginSourceStateResponse(
        user_id=ctx.user_id,
        margin_source=_get_user_margin_source(ctx.user_id),
        latest_baseline=_latest_baseline_meta(),
    )


@router.post("/margin-source")
async def settings_margin_source_post(
    body: MarginSourceUpdateBody,
    ctx: RequestContext = Depends(get_request_context),
):
    source = (body.margin_source or "").strip().lower()
    if source not in (MARGIN_SOURCE_BREEZE, MARGIN_SOURCE_EXCHANGE):
        raise HTTPException(status_code=400, detail="margin_source must be breeze_api or exchange_baseline")
    _set_user_margin_source(ctx.user_id, source)
    return JSONResponse({"ok": True, "message": "Strategy Builder margin source updated."})


@router.post("/margin-source/refresh-baseline")
async def settings_margin_source_refresh_baseline(ctx: RequestContext = Depends(get_request_context)):
    out = refresh_exchange_risk_baseline()
    if out.get("Status") != 200:
        raise HTTPException(status_code=400, detail=out.get("Error") or "Baseline refresh failed")
    return JSONResponse({"ok": True, "message": "Exchange Risk Baseline refreshed.", "result": out.get("Success")})


@router.post("/margin-source/upload-baseline")
async def settings_margin_source_upload_baseline(
    ctx: RequestContext = Depends(get_request_context),
    file: UploadFile = File(...),
    market: str = Form(...),
):
    """Upload NSE or BSE SPAN XML (or ZIP containing XML). BSE ingests BSXOPT/BKXOPT (BSESEN/BANKEX on BFO) only."""
    body = await file.read()
    out = ingest_exchange_baseline_upload(
        body,
        file.filename or "upload.xml",
        market=market,
    )
    if out.get("Status") != 200:
        raise HTTPException(status_code=400, detail=out.get("Error") or "Baseline upload failed")
    market_l = (market or "").strip().lower()
    if market_l == "bse":
        import uuid

        from icici_breeze_backend.app.core.timezone import now_ist
        from icici_breeze_backend.app.services.reference_data.state import append_ingest_history

        success = out.get("Success") or {}
        append_ingest_history(
            {
                "id": str(uuid.uuid4()),
                "kind": "bse_span_baseline",
                "display_name": "BSE SPAN Baseline",
                "source_file_date": success.get("source_date"),
                "row_count": int(success.get("inserted_rows") or 0),
                "ingested_at": now_ist().isoformat(timespec="seconds"),
                "ok": True,
                "notes": file.filename or "upload.xml",
                "source_url": None,
            }
        )
    return JSONResponse({"ok": True, "message": "Exchange Risk Baseline updated from file.", "result": out.get("Success")})


@router.get("/scrip-master/data", response_model=ScripMasterStateResponse)
async def settings_scrip_master_data(ctx: RequestContext = Depends(get_request_context)):
    meta = _scrip_master_meta()
    return ScripMasterStateResponse(user_id=ctx.user_id, **meta)


@router.post("/scrip-master/refresh")
async def settings_scrip_master_refresh(ctx: RequestContext = Depends(get_request_context)):
    breeze.update_ICICImaster()
    meta = _scrip_master_meta()
    if meta.get("master_date") is None:
        raise HTTPException(status_code=400, detail=meta.get("message") or "Scrip master refresh failed")
    return JSONResponse({"ok": True, "message": "Scrip master refreshed.", "result": meta})


@router.get("/reference-data-loads/status", response_model=ReferenceDataLoadsStateResponse)
async def settings_reference_data_status(ctx: RequestContext = Depends(get_request_context)):
    from icici_breeze_backend.app.services.reference_data.admin_status import get_reference_data_admin_status

    return ReferenceDataLoadsStateResponse(**get_reference_data_admin_status())


@router.put("/reference-data-loads/schedule", response_model=ReferenceDataLoadsStateResponse)
async def settings_reference_data_schedule(
    body: ReferenceDataScheduleUpdateBody,
    ctx: RequestContext = Depends(get_request_context),
):
    from icici_breeze_backend.app.services.reference_data.scheduler import configure_reference_data_schedule

    configure_reference_data_schedule(body.enabled, body.hour_ist, body.minute_ist)
    from icici_breeze_backend.app.services.reference_data.admin_status import get_reference_data_admin_status

    return ReferenceDataLoadsStateResponse(**get_reference_data_admin_status())


@router.post("/reference-data-loads/load-now", response_model=ReferenceDataLoadsStateResponse)
async def settings_reference_data_load_now(ctx: RequestContext = Depends(get_request_context)):
    from icici_breeze_backend.app.services.reference_data.orchestrator import trigger_reference_data_load_now
    from icici_breeze_backend.app.services.reference_data.admin_status import get_reference_data_admin_status

    trigger_reference_data_load_now(force=True)
    return ReferenceDataLoadsStateResponse(**get_reference_data_admin_status())



def _exchange_calendar_response(user_id: str) -> ExchangeCalendarStateResponse:
    row = uec_repo.get_user_calendar(user_id)
    holidays_list = [
        ExchangeCalendarHolidayItem(date=k, name=v)
        for k, v in sorted(row.holidays.items())
    ]
    return ExchangeCalendarStateResponse(
        user_id=user_id,
        source=row.source,
        working_hours=ExchangeCalendarWorkingHours(
            open_hour=row.open_hour,
            open_minute=row.open_minute,
            close_hour=row.close_hour,
            close_minute=row.close_minute,
        ),
        holidays=dict(row.holidays),
        holidays_list=holidays_list,
        portal_configured=portal_exchange_calendar_configured(),
        has_local_edits=uec_repo.has_local_edits(row),
        console_updated_at=row.console_updated_at,
        local_updated_at=row.local_updated_at,
        updated_at=row.updated_at,
    )


def _console_payload_to_state(payload: dict) -> ExchangeCalendarStateResponse:
    wh = payload.get("working_hours") or {}
    holidays_raw = payload.get("holidays") or {}
    holidays = {str(k): str(v) for k, v in holidays_raw.items()}
    holidays_list = [
        ExchangeCalendarHolidayItem(date=k, name=v) for k, v in sorted(holidays.items())
    ]
    return ExchangeCalendarStateResponse(
        user_id="",
        source="console_sync",
        working_hours=ExchangeCalendarWorkingHours(
            open_hour=int(wh.get("open_hour", 9)),
            open_minute=int(wh.get("open_minute", 15)),
            close_hour=int(wh.get("close_hour", 15)),
            close_minute=int(wh.get("close_minute", 30)),
        ),
        holidays=holidays,
        holidays_list=holidays_list,
        portal_configured=True,
        has_local_edits=False,
        console_updated_at=payload.get("updated_at"),
        local_updated_at=None,
        updated_at=payload.get("updated_at"),
    )


@router.get("/market-status", response_model=MarketStatusResponse)
async def settings_market_status(
    ctx: RequestContext = Depends(get_request_context),
):
    return MarketStatusResponse(
        is_open=is_user_market_open(ctx.user_id),
        closed_reason=user_market_closed_reason(ctx.user_id),
    )


@router.get("/exchange-calendar/data", response_model=ExchangeCalendarStateResponse)
async def settings_exchange_calendar_data(
    ctx: RequestContext = Depends(get_request_context),
):
    return _exchange_calendar_response(ctx.user_id)


@router.put("/exchange-calendar", response_model=ExchangeCalendarStateResponse)
async def settings_exchange_calendar_put(
    body: ExchangeCalendarUpdateBody,
    ctx: RequestContext = Depends(get_request_context),
):
    holidays = {h.date.strip()[:10]: h.name.strip() for h in body.holidays}
    uec_repo.save_user_calendar(
        ctx.user_id,
        open_hour=body.working_hours.open_hour,
        open_minute=body.working_hours.open_minute,
        close_hour=body.working_hours.close_hour,
        close_minute=body.working_hours.close_minute,
        holidays=holidays,
        source="local",
    )
    return _exchange_calendar_response(ctx.user_id)


@router.post("/exchange-calendar/holidays", response_model=ExchangeCalendarStateResponse)
async def settings_exchange_calendar_add_holiday(
    body: ExchangeCalendarAddHolidayBody,
    ctx: RequestContext = Depends(get_request_context),
):
    iso = body.date.strip()[:10]
    uec_repo.add_holiday(ctx.user_id, iso, body.name.strip())
    return _exchange_calendar_response(ctx.user_id)


@router.delete("/exchange-calendar/holidays/{iso_date}", response_model=ExchangeCalendarStateResponse)
async def settings_exchange_calendar_delete_holiday(
    iso_date: str,
    ctx: RequestContext = Depends(get_request_context),
):
    row = uec_repo.delete_holiday(ctx.user_id, iso_date.strip()[:10])
    if row is None:
        raise HTTPException(status_code=404, detail="Holiday not found")
    return _exchange_calendar_response(ctx.user_id)


@router.get("/exchange-calendar/sync-preview", response_model=ExchangeCalendarSyncPreviewResponse)
async def settings_exchange_calendar_sync_preview(
    ctx: RequestContext = Depends(get_request_context),
):
    local = _exchange_calendar_response(ctx.user_id)
    if not portal_exchange_calendar_configured():
        return ExchangeCalendarSyncPreviewResponse(
            portal_configured=False,
            would_overwrite_local=False,
            message="Breeze Console is not configured (PORTAL_API_BASE_URL).",
        )
    payload = await fetch_console_exchange_calendar()
    if not payload:
        raise HTTPException(
            status_code=503,
            detail="Could not fetch Breeze Console Admin Settings calendar.",
        )
    console = _console_payload_to_state(payload)
    would = local.has_local_edits
    msg = None
    if would:
        msg = (
            "Your local holiday calendar and working hours will be replaced by "
            "Breeze Console Admin Settings."
        )
    return ExchangeCalendarSyncPreviewResponse(
        portal_configured=True,
        would_overwrite_local=would,
        console=console,
        local_holiday_count=len(local.holidays),
        console_holiday_count=len(console.holidays),
        message=msg,
    )


@router.post("/exchange-calendar/sync", response_model=ExchangeCalendarStateResponse)
async def settings_exchange_calendar_sync(
    body: ExchangeCalendarSyncBody,
    ctx: RequestContext = Depends(get_request_context),
):
    if not portal_exchange_calendar_configured():
        raise HTTPException(
            status_code=503,
            detail="Breeze Console is not configured (PORTAL_API_BASE_URL).",
        )
    local = _exchange_calendar_response(ctx.user_id)
    if local.has_local_edits and not body.confirm_override:
        raise HTTPException(
            status_code=409,
            detail=(
                "Local calendar has edits that would be overwritten. "
                "Set confirm_override=true after reviewing sync-preview."
            ),
        )
    payload = await fetch_console_exchange_calendar()
    if not payload:
        raise HTTPException(
            status_code=503,
            detail="Could not fetch Breeze Console Admin Settings calendar.",
        )
    wh = payload.get("working_hours") or {}
    holidays_raw = payload.get("holidays") or {}
    uec_repo.apply_console_sync(
        ctx.user_id,
        open_hour=int(wh.get("open_hour", 9)),
        open_minute=int(wh.get("open_minute", 15)),
        close_hour=int(wh.get("close_hour", 15)),
        close_minute=int(wh.get("close_minute", 30)),
        holidays={str(k): str(v) for k, v in holidays_raw.items()},
        console_updated_at=payload.get("updated_at"),
    )
    return _exchange_calendar_response(ctx.user_id)


@router.get("/breeze-api-tester/catalog", response_model=BreezeApiTesterCatalogResponse)
async def settings_breeze_api_tester_catalog(
    ctx: RequestContext = Depends(get_request_context),
):
    del ctx
    raw = get_catalog_response()
    entries = [BreezeApiTesterCatalogEntry.model_validate(e) for e in raw]
    return BreezeApiTesterCatalogResponse(entries=entries)


@router.get("/breeze-api-tester/risk-status", response_model=BreezeApiTesterRiskStatusResponse)
async def settings_breeze_api_tester_risk_status(
    ctx: RequestContext = Depends(get_request_context),
):
    accepted = is_breeze_api_tester_risk_accepted(ctx.user_id)
    accepted_at = get_breeze_api_tester_risk_accepted_at(ctx.user_id) if accepted else None
    return BreezeApiTesterRiskStatusResponse(accepted=accepted, accepted_at=accepted_at)


@router.post("/breeze-api-tester/acknowledge-risk", response_model=BreezeApiTesterRiskStatusResponse)
async def settings_breeze_api_tester_acknowledge_risk(
    ctx: RequestContext = Depends(get_request_context),
):
    accepted_at = set_breeze_api_tester_risk_accepted(ctx.user_id)
    return BreezeApiTesterRiskStatusResponse(accepted=True, accepted_at=accepted_at)


@router.post("/breeze-api-tester/invoke", response_model=BreezeApiTesterInvokeResponse)
async def settings_breeze_api_tester_invoke(
    body: BreezeApiTesterInvokeBody,
    ctx: RequestContext = Depends(get_request_context),
):
    if not is_breeze_api_tester_risk_accepted(ctx.user_id):
        raise HTTPException(
            status_code=403,
            detail="Accept the risk disclaimer before invoking Breeze APIs.",
        )

    method = (body.method or "").strip()
    if method not in ALLOWED_METHODS:
        raise HTTPException(status_code=400, detail=f"Unknown or disallowed API method: {method}")

    last = _BREEZE_API_TESTER_INVOKE_LAST_TS.get(ctx.user_id)
    now = time.time()
    if last is not None and now - last < _BREEZE_API_TESTER_INVOKE_MIN_INTERVAL_SEC:
        raise HTTPException(status_code=429, detail="Please wait before invoking another API.")
    _BREEZE_API_TESTER_INVOKE_LAST_TS[ctx.user_id] = now

    if method == "get_customer_details":
        sdk = breeze.get_session_breeze(ctx.user_id)
        if sdk is None:
            raise HTTPException(
                status_code=503,
                detail="No active ICICI broker session. Log in with your broker token first.",
            )
        user_params = dict(body.params or {})
        session_token = str(user_params.get("api_session") or "").strip() or breeze.get_session_token(ctx.user_id)
        start = time.time()
        try:
            result = sdk.get_customer_details(session_token)
        except Exception as exc:
            duration_ms = int((time.time() - start) * 1000)
            return BreezeApiTesterInvokeResponse(
                ok=False,
                method=method,
                duration_ms=duration_ms,
                response=str(exc),
                error=None,
            )
        duration_ms = int((time.time() - start) * 1000)
        return BreezeApiTesterInvokeResponse(
            ok=True,
            method=method,
            duration_ms=duration_ms,
            response=result,
            error=None,
        )

    if method in ("ws_connect", "ws_disconnect", "subscribe_feeds"):
        from icici_breeze_backend.app.services import breeze_websocket_manager as bwm

        if method in ("ws_connect", "subscribe_feeds"):
            if breeze.get_session_breeze(ctx.user_id) is None:
                raise HTTPException(
                    status_code=503,
                    detail="No active ICICI broker session. Log in with your broker token first.",
                )
        if method == "ws_connect":
            out = bwm.ws_connect_playground(breeze, ctx.user_id)
        elif method == "ws_disconnect":
            out = bwm.ws_disconnect_playground()
        else:
            _, kwargs = build_invoke_args_permissive(method, dict(body.params or {}))
            out = bwm.playground_subscribe(breeze, ctx.user_id, kwargs)
        return BreezeApiTesterInvokeResponse(
            ok=bool(out.get("ok")),
            method=method,
            duration_ms=0,
            response=out.get("response"),
            error=None,
        )

    positional, kwargs = build_invoke_args_permissive(method, dict(body.params or {}))

    sdk = breeze.get_session_breeze(ctx.user_id)
    if sdk is None:
        raise HTTPException(
            status_code=503,
            detail="No active ICICI broker session. Log in with your broker token first.",
        )

    fn = getattr(sdk, method, None)
    if not callable(fn):
        raise HTTPException(status_code=400, detail=f"Method not available on Breeze session: {method}")

    start = time.time()
    try:
        result = fn(*positional, **kwargs)
    except Exception as exc:
        duration_ms = int((time.time() - start) * 1000)
        return BreezeApiTesterInvokeResponse(
            ok=False,
            method=method,
            duration_ms=duration_ms,
            response=str(exc),
            error=None,
        )

    duration_ms = int((time.time() - start) * 1000)
    return BreezeApiTesterInvokeResponse(
        ok=True,
        method=method,
        duration_ms=duration_ms,
        response=result,
        error=None,
    )


@router.get(
    "/strategy-builder-audit-logs",
    response_model=StrategyBuilderAuditLogsResponse,
)
async def get_strategy_builder_audit_logs(
    ctx: RequestContext = Depends(get_request_context),
):
    """List retained Strategy Builder audit logs for the current user."""
    rows = list_audit_log_index_for_user(ctx.user_id)
    return StrategyBuilderAuditLogsResponse(
        user_id=ctx.user_id,
        max_logs=_MAX_AUDIT_LOGS_PER_USER,
        logs=[StrategyBuilderAuditLogItem(**row) for row in rows],
    )


@router.get("/strategy-builder-audit-logs/download")
async def download_strategy_builder_audit_logs(
    ctx: RequestContext = Depends(get_request_context),
):
    """Download all retained Strategy Builder audit logs as a ZIP archive."""
    try:
        payload, filename = build_audit_zip_for_user(ctx.user_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(
        content=payload,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/strategy-builder-audit-logs/{session_id}/download")
async def download_strategy_builder_audit_log(
    session_id: str,
    ctx: RequestContext = Depends(get_request_context),
):
    """Download one Strategy Builder audit log JSON for the current user."""
    path = resolve_audit_file_for_user(session_id.strip(), ctx.user_id)
    if not path:
        raise HTTPException(status_code=404, detail="Audit log not found")
    fname = os.path.basename(path)
    return FileResponse(
        path,
        media_type="application/json",
        filename=fname,
        headers={"Cache-Control": "no-store"},
    )


@router.get(
    "/strategy-builder-audit-logs/{session_id}/explainability",
    response_model=StrategyBuilderAuditExplainabilityResponse,
)
async def get_strategy_builder_audit_explainability(
    session_id: str,
    ctx: RequestContext = Depends(get_request_context),
):
    """Return Level 1–3 explainability slices for a retained audit log."""
    payload = resolve_explainability_for_session(session_id.strip(), ctx.user_id)
    if payload is None:
        path = resolve_audit_file_for_user(session_id.strip(), ctx.user_id)
        if not path:
            raise HTTPException(status_code=404, detail="Audit log not found")
        raise HTTPException(
            status_code=422,
            detail="Explainability is not available for this audit log.",
        )
    return StrategyBuilderAuditExplainabilityResponse(**payload)


@router.post("/breeze-api-tester/ws/connect")
async def settings_breeze_ws_connect(ctx: RequestContext = Depends(get_request_context)):
    if not is_breeze_api_tester_risk_accepted(ctx.user_id):
        raise HTTPException(status_code=403, detail="Accept the risk disclaimer first.")
    if breeze.get_session_breeze(ctx.user_id) is None:
        raise HTTPException(
            status_code=503,
            detail="No active ICICI broker session. Log in with your broker token first.",
        )
    from icici_breeze_backend.app.services.breeze_websocket_manager import ws_connect_playground

    out = ws_connect_playground(breeze, ctx.user_id)
    _logger.info(
        "breeze-api-tester ws/connect user_id=%s ok=%s connected=%s response=%r",
        ctx.user_id,
        out.get("ok"),
        out.get("connected"),
        out.get("response"),
    )
    return JSONResponse(out)


@router.post("/breeze-api-tester/ws/disconnect")
async def settings_breeze_ws_disconnect(ctx: RequestContext = Depends(get_request_context)):
    from icici_breeze_backend.app.services.breeze_websocket_manager import ws_disconnect_playground

    out = ws_disconnect_playground()
    _logger.info("breeze-api-tester ws/disconnect user_id=%s", ctx.user_id)
    return JSONResponse(out)


@router.post("/breeze-api-tester/ws/release")
async def settings_breeze_ws_release(
    body: WsReleaseRequest,
    ctx: RequestContext = Depends(get_request_context),
):
    if not is_breeze_api_tester_risk_accepted(ctx.user_id):
        raise HTTPException(status_code=403, detail="Accept the risk disclaimer first.")
    from icici_breeze_backend.app.services.breeze_websocket_manager import ws_release_playground

    out = ws_release_playground(body.holder_id.strip())
    _logger.info("breeze-api-tester ws/release user_id=%s holder=%s", ctx.user_id, body.holder_id)
    return JSONResponse(out)


@router.get("/breeze-api-tester/ws/status")
async def settings_breeze_ws_status(ctx: RequestContext = Depends(get_request_context)):
    if not is_breeze_api_tester_risk_accepted(ctx.user_id):
        raise HTTPException(status_code=403, detail="Accept the risk disclaimer first.")
    from icici_breeze_backend.app.services.breeze_websocket_manager import get_playground_status

    return JSONResponse(get_playground_status())


@router.get("/breeze-api-tester/ws/event-log")
async def settings_breeze_ws_event_log(ctx: RequestContext = Depends(get_request_context)):
    if not is_breeze_api_tester_risk_accepted(ctx.user_id):
        raise HTTPException(status_code=403, detail="Accept the risk disclaimer first.")
    from icici_breeze_backend.app.services.breeze_websocket_manager import get_playground_event_log

    return JSONResponse({"events": get_playground_event_log()})


@router.post("/breeze-api-tester/ws/subscribe")
async def settings_breeze_ws_subscribe(
    body: BreezeApiTesterWsSubscribeBody,
    ctx: RequestContext = Depends(get_request_context),
):
    if not is_breeze_api_tester_risk_accepted(ctx.user_id):
        raise HTTPException(status_code=403, detail="Accept the risk disclaimer first.")
    if breeze.get_session_breeze(ctx.user_id) is None:
        raise HTTPException(
            status_code=503,
            detail="No active ICICI broker session. Log in with your broker token first.",
        )
    from icici_breeze_backend.app.services.breeze_websocket_manager import playground_subscribe

    out = playground_subscribe(breeze, ctx.user_id, body.model_dump(exclude_none=True))
    _logger.info(
        "breeze-api-tester ws/subscribe user_id=%s ok=%s stock=%s expiry=%s strike=%s right=%s response=%r",
        ctx.user_id,
        out.get("ok"),
        body.stock_code,
        body.expiry_date,
        body.strike_price,
        body.right,
        out.get("response"),
    )
    return JSONResponse(out)


@router.get("/breeze-api-tester/ws/stream")
async def settings_breeze_ws_stream(ctx: RequestContext = Depends(get_request_context)):
    if not is_breeze_api_tester_risk_accepted(ctx.user_id):
        raise HTTPException(status_code=403, detail="Accept the risk disclaimer first.")
    import asyncio

    from starlette.responses import StreamingResponse

    from icici_breeze_backend.app.services.breeze_websocket_manager import (
        add_playground_listener,
        get_playground_status,
        record_playground_stream_open,
        remove_playground_listener,
        ws_connect_playground,
    )

    def _sse(event: str, payload: dict[str, Any]) -> str:
        return f"event: {event}\ndata: {json.dumps(payload, default=str)}\n\n"

    connect_out = ws_connect_playground(breeze, ctx.user_id)
    stream_event = record_playground_stream_open(ctx.user_id)
    _logger.info(
        "breeze-api-tester ws/stream opened user_id=%s ok=%s connected=%s",
        ctx.user_id,
        connect_out.get("ok"),
        connect_out.get("connected"),
    )
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def _on_tick(payload: dict[str, Any]) -> None:
        try:
            loop.call_soon_threadsafe(queue.put_nowait, payload)
        except Exception:
            pass

    add_playground_listener(_on_tick)

    async def _gen():
        try:
            yield _sse(
                "ws_status",
                {
                    **get_playground_status(),
                    "ok": connect_out.get("ok"),
                    "response": connect_out.get("response"),
                    "icici_command": connect_out.get("icici_command"),
                    "event_id": connect_out.get("event_id"),
                },
            )
            yield _sse("ws_command", stream_event)
            if not connect_out.get("ok"):
                yield _sse(
                    "ws_error",
                    {
                        "ok": False,
                        "response": connect_out.get("response"),
                        "connected": connect_out.get("connected"),
                    },
                )
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield _sse("ws_tick", payload)
                except asyncio.TimeoutError:
                    yield _sse("ws_ping", {**get_playground_status(), "ts": time.time()})
        finally:
            remove_playground_listener(_on_tick)
            _logger.info("breeze-api-tester ws/stream closed user_id=%s", ctx.user_id)

    headers = {"Cache-Control": "no-cache", "Connection": "keep-alive"}
    return StreamingResponse(_gen(), media_type="text/event-stream", headers=headers)
