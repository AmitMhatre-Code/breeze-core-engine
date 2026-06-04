"""Home and login routes (JSON + redirects; no Jinja)."""
import json
import logging
import sqlite3
from urllib.parse import parse_qs, quote, urlparse

from fastapi import APIRouter, Request, Depends, HTTPException, status
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import ValidationError

from icici_breeze_backend.app.auth.auth_cookies import (
    BROKER_RECOVERY_PENDING_COOKIE,
    BROKER_RECOVERY_TOKEN_COOKIE,
    RECOVERY_TOKEN_MAX_AGE,
)
from icici_breeze_backend.app.auth.context import (
    get_request_context,
    get_optional_request_context,
    get_request_context_or_redirect,
    RequestContext,
    ICICI_BROKER_TOKEN_COOKIE,
    ACCESS_TOKEN_COOKIE,
    CREDENTIAL_FULL_SECRET_COOKIE,
    LOGIN_USER_ID_COOKIE,
)
from icici_breeze_backend.app.domain.auth import (
    LegacyLoginFormRequest,
    IciciSessionRequest,
    ChallengeContextResponse,
)
from icici_breeze_backend.app.domain.responses import HomeDataResponse
from icici_breeze_backend.app.services.processor import processor
from icici_breeze_backend.app.api.error_utils import raise_route_errors
from icici_breeze_backend.app.api.frontend_redirect import redirect_to_frontend, frontend_url
from icici_breeze_backend.app.api.v1.google_icici_redirect import redirect_to_icici_login
from icici_breeze_backend.app.auth.user_account import get_google_id_by_user_id, user_account_exists_by_user_id
import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.timezone import now_ist


logger = logging.getLogger(__name__)

_ICICI_SESSION_QUERY_KEYS = (
    "apisession",
    "session_token",
    "API_Session",
    "api_session",
    "sessionToken",
    "SessionToken",
    "token",
)

router = APIRouter()
breeze = processor()


def _icici_session_from_query(request: Request) -> str | None:
    for key in _ICICI_SESSION_QUERY_KEYS:
        v = request.query_params.get(key)
        if v and str(v).strip() and str(v).strip().lower() != "none":
            return str(v).strip()
    return None


def _icici_session_from_referer(request: Request) -> str | None:
    ref = (request.headers.get("referer") or request.headers.get("Referer") or "").strip()
    if not ref:
        return None
    try:
        q = urlparse(ref).query
        if not q:
            return None
        params = parse_qs(q, keep_blank_values=False)
        for key in _ICICI_SESSION_QUERY_KEYS:
            vals = params.get(key)
            if vals and vals[0] and vals[0].strip().lower() != "none":
                return vals[0].strip()
    except Exception:
        return None
    return None


def _login_query_from_request(request: Request) -> str:
    parts = []
    if request.query_params.get("registered"):
        parts.append("registered=1")
    if request.query_params.get("corrected"):
        parts.append("corrected=1")
    if request.query_params.get("deleted"):
        parts.append("deleted=1")
    err = request.query_params.get("error")
    if err in ("no_account", "no_credentials", "oauth_invalid"):
        parts.append(f"error={quote(err)}")
    return ("?" + "&".join(parts)) if parts else ""


def _set_auth_cookies(response: Response, icici_token: str, access_token: str, full_secret: str | None) -> None:
    from datetime import timedelta

    def _secs_until_midnight() -> int:
        now = now_ist()
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        return int((midnight - now).total_seconds())

    response.delete_cookie(key=LOGIN_USER_ID_COOKIE, path="/")
    response.set_cookie(
        key=ICICI_BROKER_TOKEN_COOKIE,
        value=icici_token,
        httponly=True,
        secure=cfg.COOKIE_SECURE,
        samesite="lax",
        max_age=_secs_until_midnight(),
        path="/",
    )
    response.set_cookie(
        key=ACCESS_TOKEN_COOKIE,
        value=access_token,
        httponly=True,
        secure=cfg.COOKIE_SECURE,
        samesite="lax",
        max_age=cfg.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
    )
    enc_key = (cfg.JWT_SECRET or "").strip()
    if enc_key and full_secret:
        from icici_breeze_backend.app.auth.credentials import encrypt_for_session_cookie

        enc_secret = encrypt_for_session_cookie(full_secret, enc_key)
        if enc_secret:
            response.set_cookie(
                key=CREDENTIAL_FULL_SECRET_COOKIE,
                value=enc_secret,
                httponly=True,
                secure=cfg.COOKIE_SECURE,
                samesite="lax",
                max_age=_secs_until_midnight(),
                path="/",
            )


async def _serve_landing_page(request: Request):
    ctx = get_optional_request_context(request)
    if ctx and ctx.broker_token:
        return redirect_to_frontend("/dashboard")

    apisession = _icici_session_from_query(request)
    # ICICI POST to /icici-return is cross-site: SameSite=Lax cookies are omitted on POST but
    # are sent on the following top-level GET (303 → /challenge), so do not require login_user_id here.
    if apisession:
        q = f"?apisession={quote(str(apisession), safe='')}"
        return redirect_to_frontend(f"/challenge{q}")

    return redirect_to_frontend("/login" + _login_query_from_request(request))


@router.get("/")
async def serve_landing(request: Request):
    return await _serve_landing_page(request)


@router.get("/icici-return")
async def serve_icici_return(request: Request):
    return await _serve_landing_page(request)


@router.get("/auth/challenge-context", response_model=ChallengeContextResponse)
async def challenge_context(request: Request):
    return ChallengeContextResponse(user_id=request.cookies.get(LOGIN_USER_ID_COOKIE))


@router.post("/auth/icici-session")
async def auth_icici_session(request: Request, body: IciciSessionRequest):
    form = LegacyLoginFormRequest(
        user_id=body.user_id,
        secret_user=body.secret_user,
        action=body.action,
    )
    return await _complete_icici_session(request, form, body.apisession)


@router.get("/updatemaster")
async def updatemaster(ctx: RequestContext = Depends(get_request_context_or_redirect)):
    breeze.update_ICICImaster()
    return redirect_to_frontend("/dashboard")


@router.get("/login")
async def login_bootstrap_icici(request: Request):
    return await redirect_to_icici_login(request)


@router.get("/logout")
async def logout(request: Request):
    broker_token = request.cookies.get(ICICI_BROKER_TOKEN_COOKIE) or ""
    access_token = request.cookies.get(ACCESS_TOKEN_COOKIE) or ""
    if broker_token and access_token:
        try:
            from icici_breeze_backend.app.auth.jwt_handler import JWTHandler
            import icici_breeze_backend.app.core.config as app_cfg

            secret = (app_cfg.JWT_SECRET or "").strip()
            if secret:
                handler = JWTHandler(secret_key=secret)
                payload = handler.validate_token(access_token)
                if payload and getattr(payload, "user_id", None):
                    from icici_breeze_backend.app.services.breeze_session_cache import evict

                    evict(payload.user_id, broker_token)
        except Exception:
            pass
    response = redirect_to_frontend("/login")
    response.delete_cookie(key=ICICI_BROKER_TOKEN_COOKIE, path="/")
    response.delete_cookie(key=ACCESS_TOKEN_COOKIE, path="/")
    response.delete_cookie(key=CREDENTIAL_FULL_SECRET_COOKIE, path="/")
    response.delete_cookie(key=LOGIN_USER_ID_COOKIE, path="/")
    return response


async def _complete_icici_session(
    request: Request,
    form: LegacyLoginFormRequest,
    apisession: str | None,
):
    if form.action == cfg.LOGIN:
        return redirect_to_frontend("/login")

    if form.action is None:
        raise HTTPException(
            status_code=400,
            detail="Missing action; open the challenge page and submit the secret fragment.",
        )

    if form.action != cfg.SUBMIT:
        return redirect_to_frontend("/login")

    from icici_breeze_backend.app.auth.credentials import CredentialManager
    from icici_breeze_backend.app.auth.jwt_handler import JWTHandler
    from breeze_connect import BreezeConnect

    _raw = (apisession or "").strip()
    if not _raw or _raw.lower() == "none":
        apisession = _icici_session_from_query(request)
    else:
        apisession = _raw
    if not apisession:
        apisession = _icici_session_from_referer(request)

    if not apisession:
        raise HTTPException(
            status_code=400,
            detail="Session token is missing. Sign in again, then complete ICICI login.",
        )

    if not form.user_id:
        raise HTTPException(status_code=400, detail="user_id is required.")

    logger.info(
        "login_submit user_id=%s apisession_present=%s secret_fragment_present=%s",
        form.user_id,
        bool(apisession),
        bool(form.secret_user),
    )

    cred_manager = CredentialManager(encryption_key=cfg.JWT_SECRET)
    full_secret = cred_manager.reconstruct_full_api_secret(form.user_id, form.secret_user or "")
    if not full_secret:
        logger.warning("login_submit credential_reconstruction_failed user_id=%s", form.user_id)
        raise HTTPException(status_code=400, detail="The secret fragment is incorrect. Please try again.")

    cred_data = breeze.fetch_credentials(form.user_id)
    if cred_data["Status"] != 200:
        logger.warning("login_submit fetch_credentials_failed user_id=%s", form.user_id)
        raise HTTPException(
            status_code=400,
            detail="Credentials not found. Please register or update your credentials.",
        )

    try:
        api_key = cred_data["Success"]["broker_api_key"]
        breeze_inst = BreezeConnect(api_key=api_key)
        breeze_inst.generate_session(api_secret=full_secret, session_token=str(apisession or ""))
    except Exception as e:
        logger.warning("login_submit icici_session_failed user_id=%s error=%s", form.user_id, e, exc_info=True)
        raise HTTPException(status_code=400, detail="The secret fragment is incorrect. Please try again.")

    try:
        customer_check = breeze_inst.get_customer_details(api_session=str(apisession or ""))
    except TypeError:
        customer_check = breeze_inst.get_customer_details(str(apisession or ""))
    except Exception:
        customer_check = None
    if not customer_check or customer_check.get("Status") != 200:
        logger.warning("login_submit session_validation_failed user_id=%s", form.user_id)
        raise HTTPException(status_code=400, detail="The secret fragment is incorrect. Please try again.")

    from icici_breeze_backend.app.external.icici_api import fetch_customerdetails_session_token, call_icici_api_direct

    broker_token = str(apisession or "")
    raw_session = (
        fetch_customerdetails_session_token(api_key, broker_token, user_id=form.user_id)
        if broker_token
        else None
    )
    if broker_token:
        margin_check = call_icici_api_direct(
            "https://api.icicidirect.com/breezeapi/api/v1/margin",
            {"exchange_code": "NFO"},
            api_key,
            full_secret,
            broker_token,
            user_id=form.user_id,
            x_session_token=raw_session if raw_session else None,
        )
        margin_status = margin_check.get("Status") or margin_check.get("status")
        err = (margin_check.get("Error") or margin_check.get("error") or "").lower()
        raw_err = (margin_check.get("Error") or margin_check.get("error") or "").strip()
        if margin_status != 200 or "invalid checksum" in err:
            logger.warning(
                "login_submit margin_probe_failed user_id=%s status=%s error=%s",
                form.user_id,
                margin_status,
                err,
            )
            if "invalid checksum" in err:
                raise HTTPException(status_code=400, detail="The secret fragment is incorrect. Please try again.")
            if "time out" in err:
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "ICICI margin API returned a session timeout while verifying your login. "
                        "Your secret fragment is often still correct. Try again shortly, complete ICICI login a bit faster, "
                        "or sync the host clock (NTP). If this keeps happening, contact ICICI support."
                    ),
                )
            raise HTTPException(
                status_code=502,
                detail=(
                    "Broker validation failed after login. "
                    + (raw_err if raw_err else "Unknown error from ICICI.")
                    + " This is not necessarily a wrong secret fragment."
                ),
            )

    with sqlite3.connect(cfg.DATA_PATH + cfg.USERS_DB) as conn:
        if not user_account_exists_by_user_id(conn, form.user_id):
            logger.warning("login_submit no user_account for user_id=%s", form.user_id)
            breeze.store_error(
                {
                    "location": "route_home.initiate_session no account",
                    "contents": "Account not found. Please register first.",
                }
            )
            errors = breeze.retrieve_errors()
            raise_route_errors(errors, log_context="route_home.initiate_session no_account")
        google_id = get_google_id_by_user_id(conn, form.user_id)

    enc_key = (cfg.JWT_SECRET or "").strip()
    pending_uid = None
    if enc_key:
        from icici_breeze_backend.app.auth.credentials import decrypt_broker_recovery_pending_cookie

        pr = request.cookies.get(BROKER_RECOVERY_PENDING_COOKIE)
        if pr:
            pending_uid = decrypt_broker_recovery_pending_cookie(pr, enc_key)

    if pending_uid and pending_uid == form.user_id:
        from icici_breeze_backend.app.auth.credentials import encrypt_broker_recovery_token

        token_val = encrypt_broker_recovery_token(form.user_id, enc_key, RECOVERY_TOKEN_MAX_AGE)
        if not token_val:
            raise HTTPException(status_code=500, detail="Could not issue recovery token")
        logger.info("login_submit broker_recovery user_id=%s", form.user_id)
        response = JSONResponse({"redirect": "/register/recover-complete"})
        response.delete_cookie(key=LOGIN_USER_ID_COOKIE, path="/")
        response.delete_cookie(key=BROKER_RECOVERY_PENDING_COOKIE, path="/")
        response.set_cookie(
            key=BROKER_RECOVERY_TOKEN_COOKIE,
            value=token_val,
            max_age=RECOVERY_TOKEN_MAX_AGE,
            httponly=True,
            secure=cfg.COOKIE_SECURE,
            samesite="lax",
            path="/",
        )
        return response

    from icici_breeze_backend.app.services.icici_customer_identity import (
        parse_customer_details_identity,
    )
    from icici_breeze_backend.app.services.portal_license_activation import (
        request_portal_license_activation,
    )

    icici_user_id, _ = parse_customer_details_identity(
        customer_check, fallback_user_id=form.user_id
    )

    allowed, activation_error = await request_portal_license_activation(
        customer_check,
        fallback_user_id=form.user_id,
    )
    if not allowed:
        raise HTTPException(
            status_code=403,
            detail=activation_error or "License activation was denied for this ICICI User ID.",
        )

    handler = JWTHandler(
        secret_key=cfg.JWT_SECRET,
        access_token_expire_minutes=cfg.JWT_ACCESS_TOKEN_EXPIRE_MINUTES,
    )
    access_token = handler.create_access_token(form.user_id, form.user_id, google_id=google_id)
    icici_token = str(apisession or "")

    logger.info("login_submit success user_id=%s", form.user_id)
    response = JSONResponse({"redirect": "/dashboard"})
    _set_auth_cookies(response, icici_token, access_token, full_secret)
    from icici_breeze_backend.app.services.portal_deployment_login import notify_portal_deployment_login

    notify_portal_deployment_login(icici_user_id=icici_user_id)
    return response


def _icici_session_body_to_legacy(body: IciciSessionRequest) -> LegacyLoginFormRequest:
    return LegacyLoginFormRequest(
        user_id=body.user_id,
        secret_user=body.secret_user,
        action=body.action,
    )


async def _post_icici_callback(request: Request):
    """ICICI browser POST (form/empty + apisession in query) or JSON body; same as legacy /icici-return."""
    ct = (request.headers.get("content-type") or "").lower()
    if "application/json" in ct:
        raw = await request.body()
        if raw.strip():
            try:
                data = json.loads(raw)
                body = IciciSessionRequest.model_validate(data)
            except (json.JSONDecodeError, ValidationError):
                body = None
            else:
                form = _icici_session_body_to_legacy(body)
                return await _complete_icici_session(request, form, body.apisession)

    resp = await _serve_landing_page(request)
    if isinstance(resp, RedirectResponse):
        resp.status_code = 303
    return resp


@router.post("/")
async def initiate_session(request: Request):
    """ICICI may POST here (callback URL = app root); SPA completion uses /auth/icici-session."""
    return await _post_icici_callback(request)


@router.post("/icici-return")
async def initiate_session_icici_return(request: Request):
    """ICICI redirects here with POST (often form or empty body + apisession in query); SPA submits JSON to /auth/icici-session."""
    return await _post_icici_callback(request)


@router.get("/home/data", response_model=HomeDataResponse)
async def get_home_api(ctx: RequestContext = Depends(get_request_context)):
    user_id = ctx.user_id
    if not ctx.broker_token:
        raise HTTPException(status_code=401, detail="ICICI broker token missing; re-login required")
    customer = breeze.get_customer_details(user_id)
    margin = breeze.get_margin_situation(user_id, target_margin_ute=100)
    from icici_breeze_backend.audit.logger import AuditLogger

    AuditLogger(None).log_portfolio_access(user_id)
    from icici_breeze_backend.app.services.api_usage import get_usage_for_display

    usage = get_usage_for_display(user_id)
    from icici_breeze_backend.app.services.deployment_license_status import (
        get_license_status_for_api,
    )

    license_fields = get_license_status_for_api() or {}
    return HomeDataResponse(
        customer=customer or {},
        margin=margin or {},
        api_calls_today=usage["api_calls_today"],
        api_calls_limit=usage["api_calls_limit"],
        api_usage_band=usage["api_usage_band"],
        **license_fields,
    )


@router.get("/data", response_model=HomeDataResponse, include_in_schema=False)
async def get_home_api_legacy_alias(ctx: RequestContext = Depends(get_request_context)):
    return await get_home_api(ctx)
