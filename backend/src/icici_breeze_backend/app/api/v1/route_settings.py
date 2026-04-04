"""Settings JSON API under /api/settings."""
import datetime
import sqlite3
from typing import Any, List

import httpx
import time
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.auth.ai_provider_keys import AiProviderKeyManager
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
from icici_breeze_backend.app.domain.settings_api import (
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
    QuantityLimitsStateResponse,
    QuantityLimitsUpdateBody,
    ScripMasterStateResponse,
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

router = APIRouter(prefix="/api/settings", tags=["settings"])
breeze = processor()
cred_manager = CredentialManager(encryption_key=(cfg.JWT_SECRET or "").strip())
ai_key_manager = AiProviderKeyManager(encryption_key=(cfg.JWT_SECRET or "").strip())
outlook_preferences_manager = OutlookPreferencesManager()
_GEMINI_DEFAULT_MODELS = ("gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash-latest")
_AI_PROVIDER_TEST_LAST_TS_BY_USER: dict[str, float] = {}


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


@router.get("/ai-provider", response_model=AiProviderStateResponse)
async def settings_ai_provider_data(ctx: RequestContext = Depends(get_request_context)):
    row = ai_key_manager.get_masked(ctx.user_id)
    if not row:
        return AiProviderStateResponse(user_id=ctx.user_id)
    return AiProviderStateResponse(
        user_id=ctx.user_id,
        configured=True,
        enabled=bool(row.get("enabled")),
        provider=row.get("provider"),
        model=row.get("model"),
        masked_api_key=row.get("masked_api_key"),
    )


@router.put("/ai-provider", response_model=AiProviderStateResponse)
async def settings_ai_provider_update(
    body: AiProviderUpdateBody,
    ctx: RequestContext = Depends(get_request_context),
):
    provider = (body.provider or "").strip().lower()
    if provider not in {"gemini", "openai"}:
        raise HTTPException(status_code=400, detail="provider must be gemini or openai")
    api_key = (body.api_key or "").strip()
    if not api_key:
        raise HTTPException(status_code=400, detail="api_key is required")
    ai_key_manager.upsert(
        user_id=ctx.user_id,
        provider=provider,
        api_key=api_key,
        model=body.model,
        enabled=bool(body.enabled),
    )
    row = ai_key_manager.get_masked(ctx.user_id)
    if not row:
        raise HTTPException(status_code=400, detail="Could not persist AI provider settings")
    return AiProviderStateResponse(
        user_id=ctx.user_id,
        configured=True,
        enabled=bool(row.get("enabled")),
        provider=row.get("provider"),
        model=row.get("model"),
        masked_api_key=row.get("masked_api_key"),
        message="AI provider settings saved.",
    )


@router.post("/ai-provider/test")
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
        else:
            payload = {"contents": [{"parts": [{"text": "Ping"}]}]}
            headers = {"Content-Type": "application/json"}
            configured = (body.model or "").strip()
            models = (configured,) if configured else _GEMINI_DEFAULT_MODELS
            res = None
            for model in models:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
                with httpx.Client(timeout=20.0) as client:
                    res = client.post(url, headers=headers, json=payload)
                if res.status_code != 404:
                    break
            if res is None:
                raise HTTPException(status_code=400, detail="Gemini provider test failed with no response")
        if res.status_code in (401, 403):
            raise HTTPException(status_code=400, detail="Invalid API key or unauthorized provider access")
        if res.status_code == 429:
            raise HTTPException(status_code=400, detail="Provider quota or rate limit reached")
        if res.status_code == 404 and provider == "gemini":
            raise HTTPException(
                status_code=400,
                detail="Selected Gemini model unavailable for this key/project. Try gemini-2.5-flash or gemini-2.0-flash.",
            )
        if res.status_code >= 400:
            raise HTTPException(status_code=400, detail=f"Provider returned {res.status_code}")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Provider test failed: {exc}") from exc
    return JSONResponse({"ok": True, "message": "Provider key test successful.", "user_id": ctx.user_id})


@router.delete("/ai-provider")
async def settings_ai_provider_delete(ctx: RequestContext = Depends(get_request_context)):
    if not ai_key_manager.revoke(ctx.user_id):
        raise HTTPException(status_code=404, detail="No AI provider settings found")
    return JSONResponse({"ok": True, "message": "AI provider key revoked."})


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
