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
from icici_breeze_backend.app.auth.ai_provider_keys import AiProviderKeyManager
from icici_breeze_backend.app.auth.outlook_ai_provider_pref import (
    get_outlook_ai_provider,
    set_outlook_ai_provider,
)
from icici_breeze_backend.app.auth.outlook_preferences import OutlookPreferencesManager
from icici_breeze_backend.app.auth.context import get_request_context, RequestContext
from icici_breeze_backend.app.services.processor import processor
from icici_breeze_backend.app.auth.credentials import CredentialManager
from icici_breeze_backend.app.auth.user_account import change_user_id
from icici_breeze_backend.app.domain.outlook_defaults import (
    DEFAULT_OUTLOOK_FEEDS,
    DEFAULT_OUTLOOK_PROMPT_TEMPLATE,
    DEFAULT_OUTLOOK_SYSTEM_PROMPT,
)
from icici_breeze_backend.app.domain.breeze_api_tester_catalog import (
    ALLOWED_METHODS,
    build_invoke_args,
    get_catalog_response,
)
from icici_breeze_backend.app.domain.settings_api import (
    AiProviderHealthEntry,
    AiProviderOutlookPickBody,
    AiProviderPatchBody,
    AiProviderSideState,
    AiProviderTestModelBody,
    AiProviderTestResponse,
    AiProviderModelTestResult,
    GeminiCatalogModelItem,
    GeminiCatalogPickerEntry,
    GeminiCatalogResponse,
    AiProviderStateResponse,
    AiProviderTestBody,
    AiProviderUpdateBody,
    ApiUsagePreferencesResponse,
    ApiUsagePreferencesUpdateBody,
    ApiUsageStateResponse,
    CredentialsStateResponse,
    CredentialsUpdateBody,
    MarginSourceStateResponse,
    MarginSourceUpdateBody,
    OutlookConfigResetBody,
    OutlookConfigStateResponse,
    OutlookConfigUpdateBody,
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
)
from icici_breeze_backend.app.services.breeze_api_tester_risk import (
    get_breeze_api_tester_risk_accepted_at,
    is_breeze_api_tester_risk_accepted,
    set_breeze_api_tester_risk_accepted,
)
from icici_breeze_backend.app.services.api_usage import get_daily_usage_by_api, get_daily_usage_by_route
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
from icici_breeze_backend.app.services.gemini_model_catalog import (
    dedupe_model_ids,
    fetch_gemini_model_catalog,
    models_list_for_user,
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
ai_key_manager = AiProviderKeyManager(encryption_key=(cfg.JWT_SECRET or "").strip())
outlook_preferences_manager = OutlookPreferencesManager()
_GEMINI_DEFAULT_MODELS = ("gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash-latest")
_AI_PROVIDER_TEST_LAST_TS_BY_USER: dict[str, float] = {}
_BREEZE_API_TESTER_INVOKE_LAST_TS: dict[str, float] = {}
_BREEZE_API_TESTER_INVOKE_MIN_INTERVAL_SEC = 2.0
_GEMINI_CATALOG_TTL_SECONDS = 24 * 60 * 60
_AI_PROVIDER_TEST_MODEL_LAST_TS: dict[str, float] = {}
# Pace Gemini generateContent probes to reduce 429s (~2 requests/sec).
_GEMINI_TEST_MIN_INTERVAL_SEC = 0.55


def _now_utc() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _normalize_models(models: list[str]) -> list[str]:
    """Primary + fallback chain only (max 20)."""
    seen: set[str] = set()
    out: list[str] = []
    for model in models:
        m = str(model or "").strip()
        if not m or m in seen:
            continue
        seen.add(m)
        out.append(m)
    return out[:20]


def _catalog_row() -> tuple | None:
    with sqlite3.connect(cfg.DATA_PATH + cfg.USERS_DB) as conn:
        cols = {
            str(c[1]) for c in conn.execute("PRAGMA table_info(gemini_model_catalog_cache)").fetchall()
        }
        if "display_names_json" in cols:
            return conn.execute(
                """
                SELECT models_json, fetched_at, expires_at, last_health_check_at, health_json,
                       display_names_json
                FROM gemini_model_catalog_cache
                WHERE provider = 'gemini'
                """
            ).fetchone()
        return conn.execute(
            """
            SELECT models_json, fetched_at, expires_at, last_health_check_at, health_json
            FROM gemini_model_catalog_cache
            WHERE provider = 'gemini'
            """
        ).fetchone()


def _write_catalog(models: list[str], *, fetched_at: datetime.datetime, health: dict[str, Any] | None = None) -> None:
    expires_at = fetched_at + datetime.timedelta(seconds=_GEMINI_CATALOG_TTL_SECONDS)
    with sqlite3.connect(cfg.DATA_PATH + cfg.USERS_DB) as conn:
        conn.execute(
            """
            INSERT INTO gemini_model_catalog_cache(
                provider, models_json, fetched_at, expires_at, last_health_check_at, health_json
            ) VALUES ('gemini', ?, ?, ?, ?, ?)
            ON CONFLICT(provider) DO UPDATE SET
                models_json=excluded.models_json,
                fetched_at=excluded.fetched_at,
                expires_at=excluded.expires_at,
                last_health_check_at=excluded.last_health_check_at,
                health_json=excluded.health_json
            """,
            (
                json.dumps(dedupe_model_ids(models)),
                fetched_at.isoformat(),
                expires_at.isoformat(),
                fetched_at.isoformat() if health else None,
                json.dumps(health or {}),
            ),
        )
        conn.commit()


def _write_catalog_model_list_only(
    models: list[str],
    *,
    display_names: dict[str, str],
    fetched_at: datetime.datetime,
) -> None:
    """Refresh cached Gemini model id list only; do not overwrite global health_json."""
    expires_at = fetched_at + datetime.timedelta(seconds=_GEMINI_CATALOG_TTL_SECONDS)
    ids = dedupe_model_ids(models)
    id_set = set(ids)
    dn_compact = {k: v for k, v in display_names.items() if k in id_set and (v or "").strip()}
    with sqlite3.connect(cfg.DATA_PATH + cfg.USERS_DB) as conn:
        cols = {
            str(c[1]) for c in conn.execute("PRAGMA table_info(gemini_model_catalog_cache)").fetchall()
        }
        if "display_names_json" in cols:
            conn.execute(
                """
                INSERT INTO gemini_model_catalog_cache(
                    provider, models_json, fetched_at, expires_at, last_health_check_at, health_json,
                    display_names_json
                ) VALUES ('gemini', ?, ?, ?, NULL, '{}', ?)
                ON CONFLICT(provider) DO UPDATE SET
                    models_json=excluded.models_json,
                    fetched_at=excluded.fetched_at,
                    expires_at=excluded.expires_at,
                    display_names_json=excluded.display_names_json
                """,
                (
                    json.dumps(ids),
                    fetched_at.isoformat(),
                    expires_at.isoformat(),
                    json.dumps(dn_compact),
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO gemini_model_catalog_cache(
                    provider, models_json, fetched_at, expires_at, last_health_check_at, health_json
                ) VALUES ('gemini', ?, ?, ?, NULL, '{}')
                ON CONFLICT(provider) DO UPDATE SET
                    models_json=excluded.models_json,
                    fetched_at=excluded.fetched_at,
                    expires_at=excluded.expires_at
                """,
                (
                    json.dumps(ids),
                    fetched_at.isoformat(),
                    expires_at.isoformat(),
                ),
            )
        conn.commit()


def _parse_display_names_json(raw: str | None) -> dict[str, str]:
    if not raw or not str(raw).strip():
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in data.items():
        ks = str(k or "").strip()
        vs = str(v or "").strip()
        if ks and vs:
            out[ks] = vs
    return out


def _fetch_gemini_models(api_key: str) -> list[str]:
    ids, _ = fetch_gemini_model_catalog(api_key)
    return ids


def _gemini_models_to_validate(single_model: str, fallback_models: list[str]) -> list[str]:
    """Models the user selected (primary + fallbacks). Do not append catalog defaults — those
    may 404/429 and would block PATCH/PUT even when the user's picks are fine."""
    seed: list[str] = []
    if (single_model or "").strip():
        seed.append(single_model)
    seed.extend(fallback_models)
    return _normalize_models(seed)


def _test_gemini_models(api_key: str, models: list[str]) -> tuple[list[AiProviderModelTestResult], bool]:
    results: list[AiProviderModelTestResult] = []
    all_ok = True
    payload = {"contents": [{"parts": [{"text": "Ping"}]}]}
    headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}
    for i, model in enumerate(_normalize_models(models)):
        if i > 0:
            time.sleep(_GEMINI_TEST_MIN_INTERVAL_SEC)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        try:
            with httpx.Client(timeout=20.0) as client:
                res = client.post(url, headers=headers, json=payload)
            ok = res.status_code < 400
            message = None
            if res.status_code in (401, 403):
                message = "Unauthorized"
            elif res.status_code == 404:
                message = "Model not available"
            elif res.status_code == 429:
                message = "Quota or rate limit reached"
            elif res.status_code >= 400:
                message = f"Provider returned {res.status_code}"
            results.append(
                AiProviderModelTestResult(
                    model=model,
                    ok=ok,
                    status_code=res.status_code,
                    message=message,
                )
            )
            if not ok:
                all_ok = False
        except Exception as exc:
            all_ok = False
            results.append(AiProviderModelTestResult(model=model, ok=False, message=str(exc)))
    return results, all_ok


def _openai_test_one_model(api_key: str, model: str) -> AiProviderModelTestResult:
    payload = {
        "model": model,
        "max_tokens": 5,
        "messages": [{"role": "user", "content": "Ping"}],
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    url = "https://api.openai.com/v1/chat/completions"
    try:
        with httpx.Client(timeout=20.0) as client:
            res = client.post(url, headers=headers, json=payload)
        ok = res.status_code < 400
        message = None
        if res.status_code in (401, 403):
            message = "Unauthorized"
        elif res.status_code >= 400:
            message = f"Provider returned {res.status_code}"
        return AiProviderModelTestResult(
            model=model,
            ok=ok,
            status_code=res.status_code,
            message=message,
        )
    except Exception as exc:
        return AiProviderModelTestResult(model=model, ok=False, message=str(exc))


def _side_state(provider: str, user_id: str) -> AiProviderSideState:
    m = ai_key_manager.get_masked(user_id, provider)
    if not m:
        return AiProviderSideState(provider=provider, configured=False, enabled=False)
    mh_raw = m.get("model_health") or {}
    mh: dict[str, AiProviderHealthEntry] = {}
    for k, v in mh_raw.items():
        if isinstance(v, dict):
            mh[str(k)] = AiProviderHealthEntry(
                ok=bool(v.get("ok")),
                message=v.get("message") if v.get("message") is not None else None,
                checked_at=v.get("checked_at") if v.get("checked_at") is not None else None,
            )
    return AiProviderSideState(
        provider=provider,
        configured=True,
        enabled=bool(m.get("enabled")),
        model=m.get("model"),
        fallback_models=list(m.get("fallback_models") or []),
        tracked_models=m.get("tracked_models"),
        masked_api_key=m.get("masked_api_key"),
        models_working=int(m.get("models_working") or 0),
        models_failing=int(m.get("models_failing") or 0),
        last_model_health_at=m.get("last_model_health_at"),
        model_health=mh,
    )


def _read_ai_provider_state_response(user_id: str, message: str | None = None) -> AiProviderStateResponse:
    outlook_p = get_outlook_ai_provider(user_id)
    return AiProviderStateResponse(
        user_id=user_id,
        gemini=_side_state("gemini", user_id),
        openai=_side_state("openai", user_id),
        outlook_ai_provider=outlook_p,
        message=message,
    )


def _schedule_ai_provider_health_refresh_if_needed(user_id: str, background_tasks: BackgroundTasks) -> None:
    """Model reachability checks run only via explicit test endpoints / UI test actions (no background probing)."""
    del user_id, background_tasks
    return


def _ensure_outlook_pref_after_upsert(user_id: str, saved_provider: str) -> None:
    if get_outlook_ai_provider(user_id) is not None:
        return
    set_outlook_ai_provider(user_id, saved_provider)


def _reconcile_outlook_pref_after_delete(user_id: str, deleted_provider: str) -> None:
    pref = get_outlook_ai_provider(user_id)
    if pref != deleted_provider:
        return
    fallback: str | None = None
    if deleted_provider == "openai":
        g = ai_key_manager.get(user_id, "gemini")
        if g and g.enabled and g.api_key:
            fallback = "gemini"
    else:
        o = ai_key_manager.get(user_id, "openai")
        if o and o.enabled and o.api_key:
            fallback = "openai"
    if fallback:
        set_outlook_ai_provider(user_id, fallback)
    else:
        with sqlite3.connect(cfg.DATA_PATH + cfg.USERS_DB) as conn:
            conn.execute(
                "UPDATE user_account SET outlook_ai_provider = NULL WHERE user_id = ?",
                (user_id,),
            )
            conn.commit()


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


@router.get("/ai-provider", response_model=AiProviderStateResponse)
async def settings_ai_provider_data(
    background_tasks: BackgroundTasks,
    ctx: RequestContext = Depends(get_request_context),
):
    _schedule_ai_provider_health_refresh_if_needed(ctx.user_id, background_tasks)
    return _read_ai_provider_state_response(ctx.user_id)


@router.put("/ai-provider", response_model=AiProviderStateResponse)
async def settings_ai_provider_update(
    body: AiProviderUpdateBody,
    background_tasks: BackgroundTasks,
    ctx: RequestContext = Depends(get_request_context),
):
    provider = (body.provider or "").strip().lower()
    if provider not in {"gemini", "openai"}:
        raise HTTPException(status_code=400, detail="provider must be gemini or openai")
    api_key = (body.api_key or "").strip()
    if not api_key:
        raise HTTPException(status_code=400, detail="api_key is required")
    fallback_models = _normalize_models(body.fallback_models)
    ai_key_manager.upsert(
        user_id=ctx.user_id,
        provider=provider,
        api_key=api_key,
        model=body.model,
        fallback_models=fallback_models,
        enabled=bool(body.enabled),
    )
    _ensure_outlook_pref_after_upsert(ctx.user_id, provider)
    _schedule_ai_provider_health_refresh_if_needed(ctx.user_id, background_tasks)
    return _read_ai_provider_state_response(ctx.user_id, message="AI provider settings saved.")


@router.patch("/ai-provider", response_model=AiProviderStateResponse)
async def settings_ai_provider_patch(
    body: AiProviderPatchBody,
    background_tasks: BackgroundTasks,
    ctx: RequestContext = Depends(get_request_context),
):
    provider = (body.provider or "").strip().lower()
    if provider not in {"gemini", "openai"}:
        raise HTTPException(status_code=400, detail="provider must be gemini or openai")
    cfg_row = ai_key_manager.get(ctx.user_id, provider)
    if not cfg_row or not (cfg_row.api_key or "").strip():
        raise HTTPException(status_code=400, detail="Provider is not configured")
    model_next = (body.model if body.model is not None else cfg_row.model) or ""
    model_s = (model_next or "").strip()
    fallbacks_src = cfg_row.fallback_models if body.fallback_models is None else body.fallback_models
    fallbacks_n = _normalize_models(list(fallbacks_src or []))
    fallbacks_n = [f for f in fallbacks_n if f != model_s]
    patch_dump = body.model_dump(exclude_unset=True)
    tracked_kw: dict = {}
    if "tracked_models" in patch_dump:
        if provider != "gemini":
            raise HTTPException(status_code=400, detail="tracked_models applies only to gemini")
        raw_tm = patch_dump["tracked_models"]
        tracked_kw["tracked_models"] = [] if raw_tm is None else list(raw_tm)
    if not ai_key_manager.update_model_fields(
        user_id=ctx.user_id,
        provider=provider,
        model=model_s or None,
        fallback_models=fallbacks_n,
        **tracked_kw,
    ):
        raise HTTPException(status_code=400, detail="Could not update model settings")
    _schedule_ai_provider_health_refresh_if_needed(ctx.user_id, background_tasks)
    return _read_ai_provider_state_response(ctx.user_id, message="Model settings updated.")


@router.put("/ai-provider/outlook-provider", response_model=AiProviderStateResponse)
async def settings_ai_provider_outlook_pick(
    body: AiProviderOutlookPickBody,
    background_tasks: BackgroundTasks,
    ctx: RequestContext = Depends(get_request_context),
):
    p = (body.provider or "").strip().lower()
    if p not in {"gemini", "openai"}:
        raise HTTPException(status_code=400, detail="provider must be gemini or openai")

    def _ok_row(c):
        return c is not None and c.enabled and bool((c.api_key or "").strip())

    gem = ai_key_manager.get(ctx.user_id, "gemini")
    oai = ai_key_manager.get(ctx.user_id, "openai")
    if p == "gemini" and not _ok_row(gem):
        raise HTTPException(status_code=400, detail="Gemini is not configured")
    if p == "openai" and not _ok_row(oai):
        raise HTTPException(status_code=400, detail="OpenAI is not configured")
    set_outlook_ai_provider(ctx.user_id, p)
    _schedule_ai_provider_health_refresh_if_needed(ctx.user_id, background_tasks)
    return _read_ai_provider_state_response(ctx.user_id, message="Outlook LLM provider updated.")


@router.post("/ai-provider/test", response_model=AiProviderTestResponse)
async def settings_ai_provider_test(
    body: AiProviderTestBody,
    ctx: RequestContext = Depends(get_request_context),
):
    last = _AI_PROVIDER_TEST_LAST_TS_BY_USER.get(ctx.user_id)
    now = time.time()
    if last is not None and now - last < 3.0:
        raise HTTPException(status_code=429, detail="Please wait a moment before testing again.")
    _AI_PROVIDER_TEST_LAST_TS_BY_USER[ctx.user_id] = now
    provider = (body.provider or "").strip().lower()
    api_key = (body.api_key or "").strip()
    if provider not in {"gemini", "openai"}:
        raise HTTPException(status_code=400, detail="provider must be gemini or openai")
    if not api_key:
        raise HTTPException(status_code=400, detail="api_key is required")
    try:
        if provider == "openai":
            model = (body.model or "").strip() or "gpt-4o-mini"
            payload = {
                "model": model,
                "max_tokens": 5,
                "messages": [{"role": "user", "content": "Ping"}],
            }
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            url = "https://api.openai.com/v1/chat/completions"
            with httpx.Client(timeout=20.0) as client:
                res = client.post(url, headers=headers, json=payload)
            if res.status_code >= 400:
                raise HTTPException(status_code=400, detail=f"Provider returned {res.status_code}")
            return AiProviderTestResponse(
                ok=True,
                message="Provider key test successful.",
                results=[AiProviderModelTestResult(model=model, ok=True, status_code=res.status_code)],
            )
        else:
            models = _gemini_models_to_validate((body.model or "").strip(), body.fallback_models or [])
            if not models:
                # Smoke-test the key with one widely available model when none was specified.
                models = [_GEMINI_DEFAULT_MODELS[0]]
            results, all_ok = _test_gemini_models(api_key, models)
            msg = "All selected Gemini models are reachable." if all_ok else "One or more Gemini models failed test."
            return AiProviderTestResponse(ok=all_ok, message=msg, results=results)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Provider test failed: {exc}") from exc


@router.post("/ai-provider/test-model", response_model=AiProviderTestResponse)
async def settings_ai_provider_test_model(
    body: AiProviderTestModelBody,
    ctx: RequestContext = Depends(get_request_context),
):
    provider = (body.provider or "").strip().lower()
    model = (body.model or "").strip()
    if provider not in {"gemini", "openai"}:
        raise HTTPException(status_code=400, detail="provider must be gemini or openai")
    if not model:
        raise HTTPException(status_code=400, detail="model is required")
    rate_key = f"{ctx.user_id}:{provider}:{model}"
    now = time.time()
    last_ts = _AI_PROVIDER_TEST_MODEL_LAST_TS.get(rate_key)
    if last_ts is not None and now - last_ts < 3.0:
        raise HTTPException(status_code=429, detail="Please wait a moment before testing again.")
    _AI_PROVIDER_TEST_MODEL_LAST_TS[rate_key] = now
    cfg_row = ai_key_manager.get(ctx.user_id, provider)
    if not cfg_row or not (cfg_row.api_key or "").strip():
        raise HTTPException(status_code=400, detail="Provider is not configured")
    checked = _now_utc().isoformat()
    if provider == "openai":
        res = _openai_test_one_model(cfg_row.api_key, model)
        results = [res]
    else:
        results, _ = _test_gemini_models(cfg_row.api_key, [model])
    if not results:
        raise HTTPException(status_code=400, detail="No test result")
    head = results[0]
    ai_key_manager.merge_model_health_entry(
        ctx.user_id,
        provider,
        model,
        ok=bool(head.ok),
        message=head.message,
        checked_at_iso=checked,
    )
    msg = (head.message or "").strip() or ("Reachable" if head.ok else "Unreachable")
    return AiProviderTestResponse(ok=bool(head.ok), message=msg, results=results)


@router.get("/ai-provider/models", response_model=GeminiCatalogResponse)
async def settings_ai_provider_models(
    provider: str = Query(default="gemini"),
    force_refresh: bool = Query(
        default=False,
        description="Bypass catalog TTL and fetch the latest model list from Gemini.",
    ),
    ctx: RequestContext = Depends(get_request_context),
):
    if provider.strip().lower() != "gemini":
        raise HTTPException(status_code=400, detail="Only gemini model listing is currently supported")
    cfg_row = ai_key_manager.get(ctx.user_id, "gemini")
    if not cfg_row or not cfg_row.api_key:
        raise HTTPException(status_code=400, detail="Configure Gemini API key first")
    now = _now_utc()
    row = _catalog_row()
    models: list[str] = []
    display_names: dict[str, str] = {}
    fetched_at: str | None = None
    last_user_health_at: str | None = None
    needs_refresh = True
    if row and not force_refresh:
        models_json = row[0]
        fetched_raw = row[1]
        expires_raw = row[2]
        if len(row) > 5:
            display_names = _parse_display_names_json(row[5])
        try:
            models = dedupe_model_ids(json.loads(models_json or "[]"))
        except Exception:
            models = []
        fetched_at = fetched_raw
        if expires_raw:
            try:
                needs_refresh = datetime.datetime.fromisoformat(str(expires_raw)) <= now
            except ValueError:
                needs_refresh = True
        else:
            needs_refresh = True
    if needs_refresh:
        models, display_names = fetch_gemini_model_catalog(cfg_row.api_key)
        if not models:
            models = list(_GEMINI_DEFAULT_MODELS)
            display_names = {}
        _write_catalog_model_list_only(models, display_names=display_names, fetched_at=now)
        fetched_at = now.isoformat()

    cfg_row = ai_key_manager.get(ctx.user_id, "gemini")
    user_health = (cfg_row.model_health if cfg_row else {}) or {}
    last_user_health_at = (cfg_row.last_model_health_at if cfg_row else None) or None
    tracked = cfg_row.tracked_models if cfg_row else None
    filtered = models_list_for_user(models, tracked)

    full_catalog = [
        GeminiCatalogPickerEntry(model=m, display_name=display_names.get(m)) for m in models
    ]

    available: list[GeminiCatalogModelItem] = []
    stale: list[GeminiCatalogModelItem] = []
    for model in filtered:
        meta = user_health.get(model) if isinstance(user_health, dict) else None
        if isinstance(meta, dict) and "ok" in meta:
            is_ok = bool(meta.get("ok"))
            status = "healthy" if is_ok else "defunct"
            message = meta.get("message")
        else:
            status = "unknown"
            message = None
        item = GeminiCatalogModelItem(
            model=model,
            status=status,
            message=message if message is not None else None,
            display_name=display_names.get(model),
        )
        if status == "defunct":
            stale.append(item)
        else:
            available.append(item)
    return GeminiCatalogResponse(
        provider="gemini",
        available_models=available,
        stale_models=stale,
        full_catalog=full_catalog,
        last_refreshed_at=fetched_at,
        last_health_check_at=last_user_health_at,
    )


@router.delete("/ai-provider")
async def settings_ai_provider_delete(
    provider: str = Query(..., description="gemini or openai"),
    ctx: RequestContext = Depends(get_request_context),
):
    p = (provider or "").strip().lower()
    if p not in {"gemini", "openai"}:
        raise HTTPException(status_code=400, detail="provider query must be gemini or openai")
    if not ai_key_manager.delete_provider(ctx.user_id, p):
        raise HTTPException(status_code=404, detail="No saved settings for this provider")
    _reconcile_outlook_pref_after_delete(ctx.user_id, p)
    return JSONResponse({"ok": True, "message": f"{p} provider removed."})


@router.get("/outlook-config", response_model=OutlookConfigStateResponse)
async def settings_outlook_config_data(ctx: RequestContext = Depends(get_request_context)):
    prefs = outlook_preferences_manager.get(ctx.user_id)
    return OutlookConfigStateResponse(
        user_id=ctx.user_id,
        feeds=[{"name": f.name, "url": f.url} for f in prefs.feeds],
        prompt_template=prefs.prompt_template,
        system_prompt=prefs.system_prompt,
        using_default_feeds=prefs.using_default_feeds,
        using_default_prompt=prefs.using_default_prompt,
        using_default_system_prompt=prefs.using_default_system_prompt,
    )


@router.put("/outlook-config", response_model=OutlookConfigStateResponse)
async def settings_outlook_config_update(
    body: OutlookConfigUpdateBody,
    ctx: RequestContext = Depends(get_request_context),
):
    feeds = [
        {"name": (f.name or "").strip(), "url": (f.url or "").strip()}
        for f in body.feeds
        if (f.name or "").strip() and (f.url or "").strip()
    ]
    if not feeds:
        feeds = [{"name": n, "url": u} for n, u in DEFAULT_OUTLOOK_FEEDS]
    prompt_template = (body.prompt_template or "").strip() or DEFAULT_OUTLOOK_PROMPT_TEMPLATE
    system_prompt = (body.system_prompt or "").strip() or DEFAULT_OUTLOOK_SYSTEM_PROMPT
    outlook_preferences_manager.upsert(
        user_id=ctx.user_id,
        feeds=feeds,
        prompt_template=prompt_template,
        system_prompt=system_prompt,
    )
    prefs = outlook_preferences_manager.get(ctx.user_id)
    return OutlookConfigStateResponse(
        user_id=ctx.user_id,
        feeds=[{"name": f.name, "url": f.url} for f in prefs.feeds],
        prompt_template=prefs.prompt_template,
        system_prompt=prefs.system_prompt,
        using_default_feeds=prefs.using_default_feeds,
        using_default_prompt=prefs.using_default_prompt,
        using_default_system_prompt=prefs.using_default_system_prompt,
        message="Outlook configuration saved.",
    )


@router.post("/outlook-config/reset", response_model=OutlookConfigStateResponse)
async def settings_outlook_config_reset(
    body: OutlookConfigResetBody,
    ctx: RequestContext = Depends(get_request_context),
):
    if not body.reset_feeds and not body.reset_prompt and not body.reset_system_prompt:
        raise HTTPException(status_code=400, detail="Select at least one setting to reset")
    outlook_preferences_manager.reset(
        user_id=ctx.user_id,
        reset_feeds=bool(body.reset_feeds),
        reset_prompt=bool(body.reset_prompt),
        reset_system_prompt=bool(body.reset_system_prompt),
    )
    prefs = outlook_preferences_manager.get(ctx.user_id)
    return OutlookConfigStateResponse(
        user_id=ctx.user_id,
        feeds=[{"name": f.name, "url": f.url} for f in prefs.feeds],
        prompt_template=prefs.prompt_template,
        system_prompt=prefs.system_prompt,
        using_default_feeds=prefs.using_default_feeds,
        using_default_prompt=prefs.using_default_prompt,
        using_default_system_prompt=prefs.using_default_system_prompt,
        message="Outlook configuration reset to defaults.",
    )


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
        start = time.time()
        result = breeze.get_customer_details(ctx.user_id)
        duration_ms = int((time.time() - start) * 1000)
        if result is None:
            raise HTTPException(
                status_code=503,
                detail="No active ICICI broker session. Log in with your broker token first.",
            )
        ok = True
        if isinstance(result, dict):
            st = result.get("Status") or result.get("status")
            if st not in (200, None):
                ok = False
        return BreezeApiTesterInvokeResponse(
            ok=ok,
            method=method,
            duration_ms=duration_ms,
            response=result,
            error=None,
        )

    if method in ("ws_connect", "ws_disconnect", "subscribe_feeds"):
        from icici_breeze_backend.app.services import breeze_websocket_manager as bwm

        if method == "ws_connect":
            out = bwm.ws_connect_playground(breeze, ctx.user_id)
        elif method == "ws_disconnect":
            out = bwm.ws_disconnect_playground()
        else:
            try:
                positional, kwargs = build_invoke_args(method, dict(body.params or {}))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            out = bwm.playground_subscribe(breeze, ctx.user_id, kwargs)
        return BreezeApiTesterInvokeResponse(
            ok=bool(out.get("ok")),
            method=method,
            duration_ms=0,
            response=out,
            error=None if out.get("ok") else out.get("message"),
        )

    if method == "place_order" and not is_user_market_open(ctx.user_id):
        return BreezeApiTesterInvokeResponse(
            ok=False,
            method=method,
            duration_ms=0,
            response=None,
            error=(
                f"Market is closed ({user_market_closed_reason(ctx.user_id)}). "
                "place_order is not available after market hours."
            ),
        )

    try:
        positional, kwargs = build_invoke_args(method, dict(body.params or {}))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

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
            response=None,
            error=str(exc),
        )

    duration_ms = int((time.time() - start) * 1000)
    ok = True
    if isinstance(result, dict):
        st = result.get("Status") or result.get("status")
        if st not in (200, None):
            ok = False
    return BreezeApiTesterInvokeResponse(
        ok=ok,
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
    from icici_breeze_backend.app.services.breeze_websocket_manager import ws_connect_playground

    out = ws_connect_playground(breeze, ctx.user_id)
    _logger.info(
        "breeze-api-tester ws/connect user_id=%s ok=%s connected=%s last_error=%s",
        ctx.user_id,
        out.get("ok"),
        out.get("connected"),
        out.get("last_error"),
    )
    if not out.get("ok"):
        raise HTTPException(status_code=503, detail=out.get("error") or "WebSocket connect failed")
    return JSONResponse(out)


@router.post("/breeze-api-tester/ws/disconnect")
async def settings_breeze_ws_disconnect(ctx: RequestContext = Depends(get_request_context)):
    from icici_breeze_backend.app.services.breeze_websocket_manager import ws_disconnect_playground

    out = ws_disconnect_playground()
    _logger.info("breeze-api-tester ws/disconnect user_id=%s", ctx.user_id)
    return JSONResponse(out)


@router.get("/breeze-api-tester/ws/status")
async def settings_breeze_ws_status(ctx: RequestContext = Depends(get_request_context)):
    if not is_breeze_api_tester_risk_accepted(ctx.user_id):
        raise HTTPException(status_code=403, detail="Accept the risk disclaimer first.")
    from icici_breeze_backend.app.services.breeze_websocket_manager import get_playground_status

    return JSONResponse(get_playground_status())


@router.post("/breeze-api-tester/ws/subscribe")
async def settings_breeze_ws_subscribe(
    body: BreezeApiTesterWsSubscribeBody,
    ctx: RequestContext = Depends(get_request_context),
):
    if not is_breeze_api_tester_risk_accepted(ctx.user_id):
        raise HTTPException(status_code=403, detail="Accept the risk disclaimer first.")
    from icici_breeze_backend.app.services.breeze_websocket_manager import playground_subscribe

    out = playground_subscribe(breeze, ctx.user_id, body.model_dump())
    _logger.info(
        "breeze-api-tester ws/subscribe user_id=%s ok=%s stock=%s expiry=%s strike=%s right=%s last_error=%s",
        ctx.user_id,
        out.get("ok"),
        body.stock_code,
        body.expiry_date,
        body.strike_price,
        body.right,
        out.get("last_error"),
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
        remove_playground_listener,
        ws_connect_playground,
    )

    def _sse(event: str, payload: dict[str, Any]) -> str:
        return f"event: {event}\ndata: {json.dumps(payload, default=str)}\n\n"

    connect_out = ws_connect_playground(breeze, ctx.user_id)
    _logger.info(
        "breeze-api-tester ws/stream opened user_id=%s ok=%s connected=%s",
        ctx.user_id,
        connect_out.get("ok"),
        connect_out.get("connected"),
    )
    queue: asyncio.Queue = asyncio.Queue()

    def _on_tick(cell: dict) -> None:
        try:
            queue.put_nowait(cell)
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
                    "message": connect_out.get("message") or connect_out.get("error"),
                },
            )
            if not connect_out.get("ok"):
                yield _sse(
                    "ws_error",
                    {
                        "message": connect_out.get("error") or "WebSocket connect failed",
                        "last_error": connect_out.get("last_error"),
                    },
                )
            while True:
                try:
                    cell = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield _sse("ws_tick", cell)
                except asyncio.TimeoutError:
                    yield _sse("ws_ping", {**get_playground_status(), "ts": time.time()})
        finally:
            remove_playground_listener(_on_tick)
            _logger.info("breeze-api-tester ws/stream closed user_id=%s", ctx.user_id)

    headers = {"Cache-Control": "no-cache", "Connection": "keep-alive"}
    return StreamingResponse(_gen(), media_type="text/event-stream", headers=headers)
