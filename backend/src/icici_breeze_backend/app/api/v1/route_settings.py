"""Settings JSON API under /api/settings."""
import datetime
import json
import logging
import os
import sqlite3
from typing import Any, List, Literal, Optional

import httpx
import time
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel

from icici_breeze_backend.app.services.market_calendar import (
    is_market_open,
    market_closed_reason,
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
    BotAuditLogItem,
    BotAuditLogsResponse,
    StrategyBuilderAuditExplainabilityResponse,
    BreezeApiTesterRiskStatusResponse,
    ExchangeCalendarAddHolidayBody,
    ExchangeCalendarHolidayItem,
    ExchangeCalendarStateResponse,
    ExchangeCalendarSyncBody,
    ExchangeCalendarSyncPreviewResponse,
    ExchangeCalendarUpdateBody,
    ExchangeCalendarWorkingHours,
    AggressiveOrderPreferencesResponse,
    AggressiveOrderPreferencesUpdateBody,
    MarketStatusResponse,
    IndexSignalPreferencesResponse,
    IndexSignalPreferencesUpdateBody,
    PnlEnginePreferencesResponse,
    PnlEnginePreferencesUpdateBody,
    QuantityLimitsStateResponse,
    QuantityLimitsUpdateBody,
    ScripMasterStateResponse,
    ReferenceDataLoadsStateResponse,
    ReferenceDataScheduleUpdateBody,
    BreezeApiTesterWsSubscribeBody,
    WsReleaseRequest,
)
from icici_breeze_backend.app.services import pnl_engine_settings
from icici_breeze_backend.app.services.index_signal import settings as index_signal_settings
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
from icici_breeze_backend.app.services.aggressive_order_prefs import (
    get_aggressive_order_prefs,
    set_aggressive_order_prefs,
)
from icici_breeze_backend.app.core.timezone import today_ist_date
from icici_breeze_backend.app.services.nsccl_baseline import (
    MARGIN_SOURCE_BREEZE,
    MARGIN_SOURCE_EXCHANGE,
    ensure_exchange_margin_baseline_table,
    refresh_all_span_baselines,
)
from icici_breeze_backend.app.repositories import exchange_calendar as ec_repo
from icici_breeze_backend.app.services.portal_exchange_calendar import (
    fetch_console_exchange_calendar,
    portal_exchange_calendar_configured,
)
from icici_breeze_backend.audit import bot_audit
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


def _aggressive_order_prefs_response(user_id: str, prefs: dict) -> AggressiveOrderPreferencesResponse:
    return AggressiveOrderPreferencesResponse(
        user_id=user_id,
        enabled=bool(cfg.AGGRESSIVE_LIMIT_ORDER_ENABLED),
        mode=prefs["mode"],
        tolerance_pct=prefs["tolerance_pct"],
        default_tolerance_pct=float(cfg.AGGRESSIVE_LIMIT_DEFAULT_TOLERANCE_PCT),
        max_tolerance_pct=float(cfg.AGGRESSIVE_LIMIT_MAX_TOLERANCE_PCT),
    )


@router.get("/aggressive-order/preferences", response_model=AggressiveOrderPreferencesResponse)
async def settings_aggressive_order_preferences_get(
    ctx: RequestContext = Depends(get_request_context),
):
    return _aggressive_order_prefs_response(ctx.user_id, get_aggressive_order_prefs(ctx.user_id))


@router.post("/aggressive-order/preferences", response_model=AggressiveOrderPreferencesResponse)
async def settings_aggressive_order_preferences_post(
    body: AggressiveOrderPreferencesUpdateBody,
    ctx: RequestContext = Depends(get_request_context),
):
    prefs = set_aggressive_order_prefs(
        ctx.user_id, mode=body.mode, tolerance_pct=body.tolerance_pct
    )
    return _aggressive_order_prefs_response(ctx.user_id, prefs)


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
    """Pull the newest published SPAN file for both exchanges. Forced: an operator clicking
    refresh wants the download attempted, not the scheduler's already-current shortcut."""
    results = refresh_all_span_baselines(force=True)
    failed = {m: (out.get("Error") or "refresh failed") for m, out in results.items() if out.get("Status") != 200}
    if len(failed) == len(results):
        raise HTTPException(
            status_code=400,
            detail="; ".join(f"{m.upper()}: {err}" for m, err in failed.items()) or "Baseline refresh failed",
        )
    message = "Exchange Risk Baseline refreshed."
    if failed:
        message += " " + "; ".join(f"{m.upper()} failed: {err}" for m, err in failed.items())
    return JSONResponse(
        {
            "ok": True,
            "message": message,
            "result": {m: out.get("Success") for m, out in results.items()},
        }
    )


@router.get("/margin-harness/runs")
async def margin_harness_runs(ctx: RequestContext = Depends(get_request_context)):
    """Past comparison runs, newest first, with each run's method ranking."""
    from icici_breeze_backend.app.services.margin_harness import runner, store

    return JSONResponse(
        {
            "running": runner.is_running() or bool(store.active_run_id()),
            "broker_mode": cfg.ICICI_BROKER_MODE,
            "runs": store.list_runs(),
        }
    )


@router.post("/margin-harness/run")
async def margin_harness_run(
    include_open_positions: bool = True,
    ctx: RequestContext = Depends(get_request_context),
):
    """Start a comparison run.

    Live broker calls only, so this is a production-instance action: on a developer machine or
    any host without the registered static IP, ICICI refuses the session and the run reports
    that rather than inventing numbers.
    """
    from icici_breeze_backend.app.services.margin_harness import runner

    if str(cfg.ICICI_BROKER_MODE or "").strip().lower() != "live":
        raise HTTPException(
            status_code=400,
            detail=(
                f"Margin harness needs live broker calls; this instance is in "
                f"'{cfg.ICICI_BROKER_MODE}' mode."
            ),
        )
    out = runner.start_harness_run(ctx.user_id, include_open_positions=include_open_positions)
    if not out.get("started"):
        raise HTTPException(status_code=409, detail="A margin harness run is already in progress.")
    return JSONResponse({"ok": True, "message": "Margin comparison run started."})


@router.get("/margin-harness/runs/{run_id}/download")
async def margin_harness_download(
    run_id: str,
    ctx: RequestContext = Depends(get_request_context),
):
    """The full run as JSON, method catalog included so it stays readable later."""
    import json as _json

    from icici_breeze_backend.app.services.margin_harness import store

    payload = store.get_run_payload(run_id.strip())
    if payload is None:
        raise HTTPException(status_code=404, detail="Run not found or produced no payload")
    return Response(
        content=_json.dumps(payload, indent=2, default=str),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="margin-harness-{run_id[:8]}.json"',
            "Cache-Control": "no-store",
        },
    )


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


@router.get("/pnl-engine/preferences", response_model=PnlEnginePreferencesResponse)
async def settings_pnl_engine_preferences_get(ctx: RequestContext = Depends(get_request_context)):
    """Advanced settings: current WS quote flush + P&L recompute intervals,
    plus the hard/recommended bounds the frontend uses for its risk copy."""
    current = pnl_engine_settings.load_pnl_engine_settings()
    return PnlEnginePreferencesResponse(**current, **pnl_engine_settings.bounds())


@router.put("/pnl-engine/preferences", response_model=PnlEnginePreferencesResponse)
async def settings_pnl_engine_preferences_put(
    body: PnlEnginePreferencesUpdateBody,
    ctx: RequestContext = Depends(get_request_context),
):
    """Global (not per-user) — takes effect for the running process within
    one flush/recompute cycle, no restart required."""
    try:
        updated = pnl_engine_settings.save_pnl_engine_settings(
            quote_flush_interval_seconds=body.quote_flush_interval_seconds,
            pnl_recompute_interval_seconds=body.pnl_recompute_interval_seconds,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return PnlEnginePreferencesResponse(**updated, **pnl_engine_settings.bounds())


@router.get("/index-signal/preferences", response_model=IndexSignalPreferencesResponse)
async def settings_index_signal_preferences_get(ctx: RequestContext = Depends(get_request_context)):
    """Settings -> Index Signal: current tuning plus the hard/recommended bounds the screen uses
    for its warnings (docs/design-decisions.md #30)."""
    current = index_signal_settings.load_index_signal_settings()
    return IndexSignalPreferencesResponse(**current.to_dict(), bounds=index_signal_settings.bounds())


@router.put("/index-signal/preferences", response_model=IndexSignalPreferencesResponse)
async def settings_index_signal_preferences_put(
    body: IndexSignalPreferencesUpdateBody,
    ctx: RequestContext = Depends(get_request_context),
):
    """Global (the signal is app-wide). The running publisher applies it within one loop:
    switching off unsubscribes the depth feed, and a new tau restarts the smoothing."""
    try:
        updated = index_signal_settings.save_index_signal_settings(
            **body.model_dump(exclude_none=True)
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return IndexSignalPreferencesResponse(**updated.to_dict(), bounds=index_signal_settings.bounds())


@router.get("/index-signal/weights")
async def settings_index_signal_weights_get(ctx: RequestContext = Depends(get_request_context)):
    """Per index: the tracked basket, its weights and their provenance, and whether a refresh
    is running."""
    from icici_breeze_backend.app.services.index_signal import weights as index_weights

    top_n = index_signal_settings.load_index_signal_settings().top_n
    return {
        "refreshing": index_weights.refresh_running(),
        "top_n": top_n,
        "indices": {
            label: index_weights.weights_overview(label, top_n) for label in index_weights.LABELS
        },
    }


@router.post("/index-signal/weights/refresh")
async def settings_index_signal_weights_refresh(ctx: RequestContext = Depends(get_request_context)):
    """Refetch both indices' weights now. Runs in the background -- SENSEX alone is ~30 BSE
    calls -- so the screen polls the GET above until `refreshing` clears."""
    from icici_breeze_backend.app.services.index_signal import weights as index_weights

    started = index_weights.refresh_due_weights_in_background(force=True)
    return {"started": started, "refreshing": index_weights.refresh_running()}


@router.get("/index-signal/shadow-report")
async def settings_index_signal_shadow_report(
    days: int = Query(5, ge=1, le=365),
    min_move_bps: Optional[float] = Query(None, ge=0, le=100),
    ctx: RequestContext = Depends(get_request_context),
):
    """Shadow-mode evidence: per-state forward index returns and hit rates over the last `days`
    -- what has to be reviewed before any bot may act on the signal. An index move smaller than
    `min_move_bps` counts as flat, neither a hit nor a miss; omitted, each index uses its
    breakeven move, priced from Settings -> Trading Costs."""
    from icici_breeze_backend.app.services.index_signal import shadow_log

    return {
        "days": days,
        "min_move_bps": min_move_bps,
        "indices": {
            label: shadow_log.shadow_report(label, days=days, min_move_bps=min_move_bps)
            for label in ("nifty", "sensex")
        },
        # Every mechanism gets the same table, so the signals page can tab between them
        # instead of showing only whichever one happens to be published (#34).
        "mechanisms": {
            label: shadow_log.shadow_report(label, days=days, min_move_bps=min_move_bps)
            for label in (
                "nifty:flow", "sensex:flow", "nifty:expansion", "sensex:expansion",
            )
        },
    }


@router.get("/index-signal/readiness")
async def settings_index_signal_readiness(ctx: RequestContext = Depends(get_request_context)):
    """The plain-language verdict above the shadow evidence: per index, whether flips beat the
    market's trend at +5 minutes by more than the breakeven move. Fixed test, no parameters --
    see `shadow_log.readiness`."""
    from icici_breeze_backend.app.services.index_signal import flow, publisher, shadow_log

    labels = ("nifty", "sensex")
    return {
        "indices": {label: shadow_log.readiness(label) for label in labels},
        # The order-flow challengers, judged by the same fixed test (`index_signal.flow`).
        "challengers": {
            label: {**shadow_log.readiness(flow.challenger_label(label)), "name": flow.CHALLENGER_NAME[label]}
            for label in labels
        },
        # The price/volume/OI mechanism (#34), judged by that same fixed test. `requires_oi`
        # is False for SENSEX, which cannot read OI at all -- ICICI serves none for BSE -- so
        # its version cannot tell a breakout from a blow-off and must be labelled as such.
        "expansion": {
            label: {
                **shadow_log.readiness(publisher.expansion_label(label)),
                "name": f"{label.upper()} volume-confirmed expansion",
                "requires_oi": publisher.EXPANSION_REQUIRES_OI[label],
                "published": publisher.PUBLISHED_MECHANISM.get(label) == "expansion",
            }
            for label in labels
        },
    }


_SIGNAL_LOG_LABELS = (
    "nifty", "sensex",
    "nifty:flow", "sensex:flow",
    "nifty:expansion", "sensex:expansion",
    "nifty:expansion:backtest", "sensex:expansion:backtest",
)


def _known_signal_label(label: str) -> bool:
    """A fixed mechanism's label, or a signal variant's live or replay label (#38)."""
    from icici_breeze_backend.app.services.index_signal import variants

    return label in _SIGNAL_LOG_LABELS or variants.is_variant_label(label)


class SignalVariantCreate(BaseModel):
    """A new way of reading the expansion mechanism (#38). NIFTY only; see `variants`."""

    name: str
    window_minutes: int
    # None reads price and volume alone -- no OI confirmation at all.
    oi_window_minutes: Optional[int] = None
    hold_minutes: int
    direction: Literal["follow", "fade"]


def _variant_view(variant: Any, user_id: str) -> dict[str, Any]:
    from icici_breeze_backend.app.repositories import bots as bots_repo
    from icici_breeze_backend.app.services.index_signal import shadow_log

    return {
        **variant.to_dict(),
        "readiness": shadow_log.readiness(variant.log_label),
        # This account's bots set to it -- shown on the screen, and why a delete is refused.
        "used_by": [
            bot.bot_type for owner, bot in bots_repo.bots_using_signal_variant(variant.id) if owner == user_id
        ],
    }


@router.get("/index-signal/variants")
async def settings_index_signal_variants(ctx: RequestContext = Depends(get_request_context)):
    """Every signal variant with its live evidence verdict (the same fixed readiness test the
    mechanisms get), and the bounds the create form checks against."""
    from icici_breeze_backend.app.services.index_signal import variants

    return {
        "variants": [_variant_view(v, ctx.user_id) for v in variants.list_variants(fresh=True)],
        "bounds": {
            "window_minutes": [variants.WINDOW_MIN, variants.WINDOW_MAX],
            "oi_window_minutes": [variants.OI_WINDOW_MIN, variants.OI_WINDOW_MAX],
            "hold_minutes": [variants.HOLD_MIN, variants.HOLD_MAX],
            "name_max": variants.NAME_MAX,
        },
    }


@router.post("/index-signal/variants", status_code=201)
async def settings_index_signal_variant_create(
    body: SignalVariantCreate, ctx: RequestContext = Depends(get_request_context)
):
    """Create a variant. It starts with an empty record: evidence belongs to one definition."""
    from icici_breeze_backend.app.services.index_signal import variants

    try:
        created = variants.create_variant(
            name=body.name,
            window_minutes=body.window_minutes,
            oi_window_minutes=body.oi_window_minutes,
            hold_minutes=body.hold_minutes,
            direction=body.direction,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _variant_view(created, ctx.user_id)


@router.delete("/index-signal/variants/{variant_id}")
async def settings_index_signal_variant_delete(
    variant_id: str, ctx: RequestContext = Depends(get_request_context)
):
    """Delete a user variant and its evidence. Refused while any bot is set to it -- a bot left
    on a deleted variant would read `unavailable` and never trade, with nothing saying why."""
    from icici_breeze_backend.app.repositories import bots as bots_repo
    from icici_breeze_backend.app.services.index_signal import variants

    users = bots_repo.bots_using_signal_variant(variant_id.strip().lower())
    if users:
        names = sorted({bot.bot_type for _owner, bot in users})
        raise HTTPException(
            status_code=409,
            detail="A bot is still set to this variant (" + ", ".join(names) + "). "
            "Choose another signal in its settings first.",
        )
    try:
        deleted = variants.delete_variant(variant_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"deleted": deleted.id}


@router.get("/index-signal/flips")
async def settings_index_signal_flips(
    label: str = Query(...),
    days: int = Query(5, ge=1, le=3650),
    min_move_bps: Optional[float] = Query(None, ge=0, le=100),
    ctx: RequestContext = Depends(get_request_context),
):
    """Each time one mechanism turned bullish or bearish, newest first, with the index 5 and 15
    minutes later -- the "when it turned" list on the signals page. `label` is a shadow-log
    label, including a backtest replay's (`nifty:expansion:backtest`)."""
    from icici_breeze_backend.app.services.index_signal import shadow_log

    key = label.strip().lower()
    if not _known_signal_label(key):
        raise HTTPException(status_code=400, detail=f"unknown signal label: {label}")
    return shadow_log.flip_list(key, days=days, min_move_bps=min_move_bps)


class ExpansionBacktestRequest(BaseModel):
    """The signals page's backtest dialog asks one thing: the period (#36)."""

    period: Literal["last_day", "last_week", "last_month", "custom"]
    from_date: Optional[datetime.date] = None
    to_date: Optional[datetime.date] = None


@router.post("/index-signal/expansion/backtest")
def settings_index_signal_expansion_backtest(
    req: ExpansionBacktestRequest,
    ctx: RequestContext = Depends(get_request_context),
):
    """Start a backtest of the expansion mechanism for both indices (#34).

    Fetches the futures bars the range is missing from ICICI -- live broker, outside market
    hours, within the day's backtest budget -- then replays and scores them with the live test.
    Runs as the shared backtest job, polled at `/bots/backtest/job`. Only mechanisms that need
    no order book have one: W-OBI and the flow challengers cannot be replayed, because history
    carries no books and no quotes."""
    from icici_breeze_backend.app.services.bots import backtest_jobs as jobs

    try:
        return jobs.start_signal_backtest(ctx.user_id, req.period, req.from_date, req.to_date)
    except jobs.Busy as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/index-signal/expansion/backtest")
def settings_index_signal_expansion_last_backtest(ctx: RequestContext = Depends(get_request_context)):
    """The last backtest: its range, notes, and each index's replay summary and verdict."""
    from icici_breeze_backend.app.services.index_signal import expansion_backtest

    return {"run": expansion_backtest.last_run()}


def _signal_log_label(index: str) -> str:
    label = index.strip().lower()
    if not _known_signal_label(label):
        raise HTTPException(
            status_code=400,
            detail="index must be nifty or sensex, its :flow or :expansion mechanism, "
            "a signal variant, or an :expansion:backtest replay",
        )
    return label


def _csv_download(content: str, filename: str) -> Response:
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/index-signal/readings/download")
async def settings_index_signal_readings_download(
    index: str = Query(...),
    days: int = Query(5, ge=1, le=3650),
    ctx: RequestContext = Depends(get_request_context),
):
    """The minute readings behind the shadow report as CSV, for Excel or a charting tool: the
    signal and index level at each reading, and the index 1/5/15 minutes later. A backtest
    replay's label downloads its replayed readings; `days` then has to reach back to the start
    of the replayed range, as for its flip list."""
    from icici_breeze_backend.app.services.index_signal import shadow_log

    label = _signal_log_label(index)
    return _csv_download(
        shadow_log.readings_csv(label, days=days), shadow_log.readings_filename(label, days)
    )


@router.get("/index-signal/calls/download")
async def settings_index_signal_calls_download(
    index: str = Query(...),
    days: int = Query(5, ge=1, le=3650),
    min_move_bps: Optional[float] = Query(None, ge=0, le=100),
    ctx: RequestContext = Depends(get_request_context),
):
    """One row per call (each turn bullish or bearish) as CSV: what the mechanism read when it
    fired, and how the call went at +5 and +15 minutes -- for working out offline which calls
    fail and why. Judged against the breakeven unless `min_move_bps` is given."""
    from icici_breeze_backend.app.services.index_signal import shadow_log

    label = _signal_log_label(index)
    return _csv_download(
        shadow_log.calls_csv(label, days=days, min_move_bps=min_move_bps),
        shadow_log.calls_filename(label, days),
    )


def _exchange_calendar_response() -> ExchangeCalendarStateResponse:
    row = ec_repo.get_calendar()
    holidays_list = [
        ExchangeCalendarHolidayItem(date=k, name=v)
        for k, v in sorted(row.holidays.items())
    ]
    return ExchangeCalendarStateResponse(
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
        has_local_edits=ec_repo.has_local_edits(row),
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
        is_open=is_market_open(),
        closed_reason=market_closed_reason(),
    )


@router.get("/exchange-calendar/data", response_model=ExchangeCalendarStateResponse)
async def settings_exchange_calendar_data(
    ctx: RequestContext = Depends(get_request_context),
):
    return _exchange_calendar_response()


@router.put("/exchange-calendar", response_model=ExchangeCalendarStateResponse)
async def settings_exchange_calendar_put(
    body: ExchangeCalendarUpdateBody,
    ctx: RequestContext = Depends(get_request_context),
):
    holidays = {h.date.strip()[:10]: h.name.strip() for h in body.holidays}
    ec_repo.save_calendar(
        open_hour=body.working_hours.open_hour,
        open_minute=body.working_hours.open_minute,
        close_hour=body.working_hours.close_hour,
        close_minute=body.working_hours.close_minute,
        holidays=holidays,
        source="local",
    )
    return _exchange_calendar_response()


@router.post("/exchange-calendar/holidays", response_model=ExchangeCalendarStateResponse)
async def settings_exchange_calendar_add_holiday(
    body: ExchangeCalendarAddHolidayBody,
    ctx: RequestContext = Depends(get_request_context),
):
    iso = body.date.strip()[:10]
    ec_repo.add_holiday(iso, body.name.strip())
    return _exchange_calendar_response()


@router.delete("/exchange-calendar/holidays/{iso_date}", response_model=ExchangeCalendarStateResponse)
async def settings_exchange_calendar_delete_holiday(
    iso_date: str,
    ctx: RequestContext = Depends(get_request_context),
):
    row = ec_repo.delete_holiday(iso_date.strip()[:10])
    if row is None:
        raise HTTPException(status_code=404, detail="Holiday not found")
    return _exchange_calendar_response()


@router.get("/exchange-calendar/sync-preview", response_model=ExchangeCalendarSyncPreviewResponse)
async def settings_exchange_calendar_sync_preview(
    ctx: RequestContext = Depends(get_request_context),
):
    local = _exchange_calendar_response()
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
    local = _exchange_calendar_response()
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
    ec_repo.apply_console_sync(
        open_hour=int(wh.get("open_hour", 9)),
        open_minute=int(wh.get("open_minute", 15)),
        close_hour=int(wh.get("close_hour", 15)),
        close_minute=int(wh.get("close_minute", 30)),
        holidays={str(k): str(v) for k, v in holidays_raw.items()},
        console_updated_at=payload.get("updated_at"),
    )
    return _exchange_calendar_response()


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


@router.get("/bot-audit-logs", response_model=BotAuditLogsResponse)
async def get_bot_audit_logs(ctx: RequestContext = Depends(get_request_context)):
    """List retained scalping-bot audit files for the current user, newest first."""
    rows = bot_audit.list_index_for_user(ctx.user_id)
    return BotAuditLogsResponse(
        user_id=ctx.user_id,
        retention_days=bot_audit.RETENTION_DAYS,
        logs=[BotAuditLogItem(**row) for row in rows],
    )


@router.get("/bot-audit-logs/download")
async def download_bot_audit_logs(ctx: RequestContext = Depends(get_request_context)):
    """Download every retained bot audit file as one ZIP."""
    try:
        payload, filename = bot_audit.build_zip_for_user(ctx.user_id)
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


@router.get("/bot-audit-logs/backtest/{name}/download")
async def download_bot_backtest_audit_log(
    name: str, ctx: RequestContext = Depends(get_request_context)
):
    """Download one backtest's whole trail (#35). Separate from the daily trails: a replay is
    one record of one run, not a trading day's file."""
    path = bot_audit.resolve_backtest_file_for_user(name.strip(), ctx.user_id)
    if not path:
        raise HTTPException(status_code=404, detail="Backtest audit trail not found")
    return FileResponse(
        path,
        media_type="application/x-ndjson",
        filename=os.path.basename(path),
        headers={"Cache-Control": "no-store"},
    )


@router.get("/bot-audit-logs/{name}/download")
async def download_bot_audit_log(
    name: str, ctx: RequestContext = Depends(get_request_context)
):
    """Download one bot/day audit file as JSONL.

    `resolve_file_for_user` re-derives the name from its parts before touching the disk, so
    a traversal attempt resolves to nothing rather than escaping the audit directory.
    """
    path = bot_audit.resolve_file_for_user(name.strip(), ctx.user_id)
    if not path:
        raise HTTPException(status_code=404, detail="Bot audit log not found")
    return FileResponse(
        path,
        media_type="application/x-ndjson",
        filename=os.path.basename(path),
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
