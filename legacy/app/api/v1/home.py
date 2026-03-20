"""Home and login routes."""
import logging
import sqlite3
import urllib.parse

from fastapi import APIRouter, Request, Depends, Form, status
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse

from app.auth.context import (
    get_request_context,
    get_optional_request_context,
    get_request_context_or_redirect,
    RequestContext,
    ICICI_BROKER_TOKEN_COOKIE,
    ACCESS_TOKEN_COOKIE,
    CREDENTIAL_FULL_SECRET_COOKIE,
    LOGIN_USER_ID_COOKIE,
)
from app.domain.auth import LegacyLoginFormRequest
from app.domain.responses import HomeDataResponse
from app.services.processor import processor
from app.api.error_utils import render_error_page
from app.api.v1.route_admin import get_common_template_vars
from app.api.v1.route_google_auth import GOOGLE_OAUTH_COOKIE
from app.auth.credentials import decrypt_google_oauth_cookie
from app.auth.user_account import get_user_id_by_google_id, get_google_id_by_user_id
import app.core.config as cfg


def get_legacy_login_form(
    user_id: str | None = Form(None),
    secret_user: str | None = Form(None),
    action: str | None = Form(None),
) -> LegacyLoginFormRequest:
    return LegacyLoginFormRequest(user_id=user_id, secret_user=secret_user, action=action)

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="templates")


def _render_challenge(request: Request, user_id: str, apisession: str | int | None, secret_user: str = "", error_message: str | None = None):
    """Render challenge page with optional error message."""
    messages = [{"type": "alert-danger", "message": error_message}] if error_message else []
    return templates.TemplateResponse(
        "challenge.html",
        {
            "request": request,
            "is_logged_in": False,
            "login_url": None,
            "active": "home",
            "user_id": user_id,
            "apisession": apisession,
            "secret_user": secret_user,
            "messages": messages,
            "display_theme": "dark",
        },
    )
router = APIRouter()
breeze = processor()


@router.get("/")
async def serve_landing(request: Request):
    """Home page: show login if not authenticated, else dashboard. Handles ICICI callback (apisession) -> challenge page."""
    ctx = get_optional_request_context(request)
    if not ctx or not ctx.broker_token:
        apisession = (
            request.query_params.get("apisession")
            or request.query_params.get("session_token")
            or request.query_params.get("API_Session")
            or request.query_params.get("api_session")
        )
        user_id = request.cookies.get(LOGIN_USER_ID_COOKIE)
        if apisession and user_id:
            return templates.TemplateResponse(
                "challenge.html",
                {"request": request, "is_logged_in": False, "login_url": None, "active": "home", "user_id": user_id, "apisession": apisession, "secret_user": "", "display_theme": "dark", "messages": []},
            )
        messages = []
        if request.query_params.get("registered"):
            messages.append({"type": "alert-success", "message": "Registration complete. Please log in."})
        if request.query_params.get("corrected"):
            messages.append({"type": "alert-success", "message": "Credentials updated. Please log in."})
        if request.query_params.get("deleted"):
            messages.append({"type": "alert-success", "message": "Registration deleted. You can register again."})
        err = request.query_params.get("error")
        if err == "no_account":
            messages.append({"type": "alert-warning", "message": "No account found for this Google account. Please register first."})
        elif err == "no_credentials":
            messages.append({"type": "alert-warning", "message": "No credentials found. Please register or update your credentials."})
        elif err == "oauth_invalid":
            messages.append({"type": "alert-danger", "message": "Sign-in failed. Please try again."})
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "is_logged_in": False, "display_theme": "dark", "messages": messages},
        )

    user_id = ctx.user_id
    warnings = []

    customer = breeze.get_customer_details(user_id)
    if customer is None:
        breeze.store_error({"location": "route_home.serve_landing get_customer_details", "contents": f"get_customer_details() returned None for user_id = {user_id}"})
        warnings.append("Customer details could not be loaded.")
        customer = {"Status": 400, "Error": "Not available"}
    elif customer.get("Status") != 200:
        breeze.store_error({"location": "route_home.serve_landing get_customer_details", "contents": f"customer Status={customer.get('Status')} Error={customer.get('Error', '')}"})
        warnings.append("Customer details could not be loaded.")

    margin = breeze.get_margin_situation(user_id, target_margin_ute=100)
    if margin.get("Status") != 200:
        breeze.store_error({"location": "route_home.serve_landing get_margin_situation", "contents": f"margin Status={margin.get('Status')} Error={margin.get('Error', '')}"})
        warnings.append("Margin information could not be loaded.")
        margin = {"Status": 400, "Error": margin.get("Error", "Not available"), "Success": {"last_refresh": "—", "actual_margin_ute": 0, "cash_limit": 0, "actual_margin_avl": 0, "target_margin_free": 0, "limits": 0}}

    master_file_age = breeze.get_ICICImaster_date()
    if master_file_age.get("Status") != 200:
        breeze.store_error({"location": "route_home.serve_landing get_ICICImaster_date", "contents": f"master_file_age Status={master_file_age.get('Status')} Error={master_file_age.get('Error', '')}"})
        warnings.append("Master file age could not be loaded.")
        master_file_age = {"Status": 400, "Error": master_file_age.get("Error", ""), "Success": {"date": "—", "age": 0}}

    positions = breeze.get_positions(user_id)
    position_count = len(positions.get("Success") or []) if positions.get("Status") == 200 else 0

    return templates.TemplateResponse(
        "home.html",
        {
            "request": request,
            "is_logged_in": True,
            "active": "home",
            "customer": customer,
            "margin": margin,
            "master_file_age": master_file_age,
            "position_count": position_count,
            "warnings": warnings,
            **get_common_template_vars(ctx),
        },
    )


@router.get("/updatemaster")
async def updatemaster(ctx: RequestContext = Depends(get_request_context_or_redirect)):
    breeze.update_ICICImaster()
    return RedirectResponse("/", status_code=status.HTTP_302_FOUND)


@router.get("/login")
async def login_via_google(request: Request):
    """Post-OAuth: look up user_id by google_id, redirect to ICICI login. Called after /auth/google/callback with next=/login."""
    cookie = request.cookies.get(GOOGLE_OAUTH_COOKIE)
    if not cookie:
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    enc_key = (cfg.JWT_SECRET or "").strip()
    data = decrypt_google_oauth_cookie(cookie, enc_key) if enc_key else None
    if not data:
        return RedirectResponse(url="/?error=oauth_invalid", status_code=status.HTTP_302_FOUND)
    google_id, _ = data
    with sqlite3.connect(cfg.DATA_PATH + "db.sqlite3") as conn:
        user_id = get_user_id_by_google_id(conn, google_id)
    if not user_id:
        return RedirectResponse(url="/?error=no_account", status_code=status.HTTP_302_FOUND)
    login_url = breeze.get_login_url(user_id)
    if not login_url:
        return RedirectResponse(url="/?error=no_credentials", status_code=status.HTTP_302_FOUND)
    response = RedirectResponse(urllib.parse.quote(login_url, safe=":/?=&"), status_code=status.HTTP_307_TEMPORARY_REDIRECT)
    response.set_cookie(key=LOGIN_USER_ID_COOKIE, value=user_id, max_age=300, httponly=True, secure=cfg.COOKIE_SECURE, samesite="lax", path="/")
    return response


@router.get("/logout")
async def logout(request: Request):
    """Clear auth cookies and redirect to login."""
    broker_token = request.cookies.get(ICICI_BROKER_TOKEN_COOKIE) or ""
    access_token = request.cookies.get(ACCESS_TOKEN_COOKIE) or ""
    if broker_token and access_token:
        try:
            from app.auth.jwt_handler import JWTHandler
            import app.core.config as app_cfg
            secret = (app_cfg.JWT_SECRET or "").strip()
            if secret:
                handler = JWTHandler(secret_key=secret)
                payload = handler.validate_token(access_token)
                if payload and getattr(payload, "user_id", None):
                    from app.services.breeze_session_cache import evict
                    evict(payload.user_id, broker_token)
        except Exception:
            pass
    response = RedirectResponse("/", status_code=status.HTTP_302_FOUND)
    response.delete_cookie(key=ICICI_BROKER_TOKEN_COOKIE, path="/")
    response.delete_cookie(key=ACCESS_TOKEN_COOKIE, path="/")
    response.delete_cookie(key=CREDENTIAL_FULL_SECRET_COOKIE, path="/")
    response.delete_cookie(key=LOGIN_USER_ID_COOKIE, path="/")
    return response


@router.post("/")
async def initiate_session(
    request: Request,
    form: LegacyLoginFormRequest = Depends(get_legacy_login_form),
    apisession: str | None = Form(None, alias="apisession"),
):
    """Login flow: challenge (from Google) → set cookies. Legacy manual user_id removed."""
    error = {}

    if form.action == cfg.LOGIN:
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

    if form.action is None:
        return templates.TemplateResponse("challenge.html", {"request": request, "is_logged_in": False, "login_url": None, "active": "home", "user_id": form.user_id, "apisession": apisession, "display_theme": "dark"})

    elif form.action == cfg.SUBMIT:
        from app.auth.credentials import CredentialManager
        from app.auth.jwt_handler import JWTHandler
        from fastapi.responses import RedirectResponse as RDR
        from breeze_connect import BreezeConnect

        # Resolve apisession: form may have "None" (Jinja) or be empty; fallback to query params (ICICI redirect can put token in URL)
        _raw = (apisession or "").strip()
        if not _raw or _raw.lower() == "none":
            apisession = (
                request.query_params.get("apisession")
                or request.query_params.get("session_token")
                or request.query_params.get("API_Session")
                or request.query_params.get("api_session")
            )
        else:
            apisession = _raw

        if not apisession:
            return _render_challenge(
                request, form.user_id, None, form.secret_user or "",
                error_message="Session token is missing. Please start over: log in with Google, then complete the ICICI login. Do not bookmark or refresh the challenge page.",
            )

        logger.info("login_submit user_id=%s apisession_present=%s secret_fragment_present=%s", form.user_id, bool(apisession), bool(form.secret_user))

        cred_manager = CredentialManager(encryption_key=cfg.JWT_SECRET)
        full_secret = cred_manager.reconstruct_full_api_secret(form.user_id, form.secret_user or "")
        if not full_secret:
            logger.warning("login_submit credential_reconstruction_failed user_id=%s", form.user_id)
            return _render_challenge(
                request, form.user_id, apisession, form.secret_user or "",
                error_message="The secret fragment is incorrect. Please try again.",
            )

        cred_data = breeze.fetch_credentials(form.user_id)
        if cred_data['Status'] != 200:
            logger.warning("login_submit fetch_credentials_failed user_id=%s", form.user_id)
            return _render_challenge(
                request, form.user_id, apisession, form.secret_user or "",
                error_message="Credentials not found. Please register or update your credentials.",
            )

        try:
            api_key = cred_data['Success']['broker_api_key']
            breeze_inst = BreezeConnect(api_key=api_key)
            breeze_inst.generate_session(api_secret=full_secret, session_token=str(apisession or ""))
        except Exception as e:
            logger.warning("login_submit icici_session_failed user_id=%s error=%s", form.user_id, e, exc_info=True)
            return _render_challenge(
                request, form.user_id, apisession, form.secret_user or "",
                error_message="The secret fragment is incorrect. Please try again.",
            )

        try:
            customer_check = breeze_inst.get_customer_details(api_session=str(apisession or ""))
        except TypeError:
            customer_check = breeze_inst.get_customer_details(str(apisession or ""))
        except Exception:
            customer_check = None
        if not customer_check or customer_check.get("Status") != 200:
            logger.warning("login_submit session_validation_failed user_id=%s", form.user_id)
            return _render_challenge(
                request, form.user_id, apisession, form.secret_user or "",
                error_message="The secret fragment is incorrect. Please try again.",
            )

        # Validate secret via checksum-protected endpoint (CustomerDetails works with token only; margin requires secret)
        from app.external.icici_api import fetch_customerdetails_session_token, call_icici_api_direct
        broker_token = str(apisession or "")
        raw_session = fetch_customerdetails_session_token(api_key, broker_token) if broker_token else None
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
            if margin_status != 200 or "invalid checksum" in err:
                logger.warning("login_submit secret_validation_failed user_id=%s status=%s error=%s", form.user_id, margin_status, err)
                return _render_challenge(
                    request, form.user_id, apisession, form.secret_user or "",
                    error_message="The secret fragment is incorrect. Please try again.",
                )

        with sqlite3.connect(cfg.DATA_PATH + "db.sqlite3") as conn:
            google_id = get_google_id_by_user_id(conn, form.user_id)
        if not google_id:
            logger.warning("login_submit no google_id for user_id=%s", form.user_id)
            error['location'] = "route_home.initiate_session no account"
            error['contents'] = "Account not found. Please register with Google."
            breeze.store_error(error)
            errors = breeze.retrieve_errors()
            return render_error_page(request, errors, active="home", log_context="route_home.initiate_session no_google_id")

        handler = JWTHandler(secret_key=cfg.JWT_SECRET, access_token_expire_minutes=cfg.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
        access_token = handler.create_access_token(form.user_id, form.user_id, google_id=google_id)
        icici_token = str(apisession or "")

        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo
        def _secs_until_midnight():
            ist = ZoneInfo("Asia/Kolkata")
            now = datetime.now(ist)
            midnight = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            return int((midnight - now).total_seconds())

        logger.info("login_submit success user_id=%s redirecting_to_landing", form.user_id)
        response = RDR("/", status_code=status.HTTP_302_FOUND)
        response.delete_cookie(key=LOGIN_USER_ID_COOKIE, path="/")
        response.set_cookie(key=ICICI_BROKER_TOKEN_COOKIE, value=icici_token, httponly=True, secure=cfg.COOKIE_SECURE, samesite="lax", max_age=_secs_until_midnight(), path="/")
        response.set_cookie(key=ACCESS_TOKEN_COOKIE, value=access_token, httponly=True, secure=cfg.COOKIE_SECURE, samesite="lax", max_age=cfg.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60, path="/")
        from app.auth.credentials import encrypt_for_session_cookie
        enc_key = (cfg.JWT_SECRET or "").strip()
        if enc_key and full_secret:
            enc_secret = encrypt_for_session_cookie(full_secret, enc_key)
            if enc_secret:
                response.set_cookie(key=CREDENTIAL_FULL_SECRET_COOKIE, value=enc_secret, httponly=True, secure=cfg.COOKIE_SECURE, samesite="lax", max_age=_secs_until_midnight(), path="/")
        return response

    return templates.TemplateResponse("login.html", {"request": request, "is_logged_in": False, "display_theme": "dark"})


@router.get("/data", response_model=HomeDataResponse)
async def get_home_api(ctx: RequestContext = Depends(get_request_context)):
    user_id = ctx.user_id
    if not ctx.broker_token:
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="ICICI broker token missing; re-login required")
    customer = breeze.get_customer_details(user_id)
    margin = breeze.get_margin_situation(user_id)
    from audit.logger import AuditLogger
    AuditLogger(None).log_portfolio_access(user_id)
    return HomeDataResponse(customer=customer or {}, margin=margin or {})
