"""Settings: ICICI credentials. Theme is client-side (localStorage)."""
import sqlite3

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

import app.core.config as cfg
from app.auth.context import get_request_context_or_redirect, RequestContext
from app.api.v1.route_admin import get_common_template_vars
from app.services.processor import processor
from app.auth.credentials import CredentialManager
from app.auth.user_account import change_user_id, get_google_id_by_user_id
from typing import List, Any

templates = Jinja2Templates(directory="templates")
router = APIRouter()
breeze = processor()
cred_manager = CredentialManager(encryption_key=(cfg.JWT_SECRET or "").strip())


@router.get("/display")
async def settings_display_redirect():
    """Redirect to credentials; theme is now client-side."""
    return RedirectResponse("/settings/credentials", status_code=302)


@router.get("/credentials")
async def settings_credentials_get(
    request: Request,
    ctx: RequestContext = Depends(get_request_context_or_redirect),
):
    user_id = ctx.user_id
    customer = breeze.get_customer_details(user_id)
    if customer is None:
        customer = {"Status": 400, "Error": "Not available", "Success": {"idirect_user_name": "—"}}
    elif customer.get("Status") != 200:
        customer = {"Status": 400, "Error": customer.get("Error", ""), "Success": {"idirect_user_name": "—"}}

    margin = breeze.get_margin_situation(user_id, target_margin_ute=100)
    if margin.get("Status") != 200:
        margin = {"Status": 400, "Error": margin.get("Error", ""), "Success": {"last_refresh": "—", "actual_margin_ute": 0, "cash_limit": 0, "actual_margin_avl": 0, "target_margin_free": 0, "limits": 0}}

    message = request.query_params.get("updated") and "Credentials saved. Remember your challenge fragment (the part you did not store). Log out and log in again via ICICI to use the new API key."
    return templates.TemplateResponse(
        "settings_credentials.html",
        {
            "request": request,
            "is_logged_in": True,
            "login_url": None,
            "active": "settings",
            "customer": customer,
            "margin": margin,
            "message": message,
            "user_id": user_id,
            **get_common_template_vars(ctx),
        },
    )


@router.post("/credentials")
async def settings_credentials_post(
    request: Request,
    ctx: RequestContext = Depends(get_request_context_or_redirect),
    user_id: str = Form(...),
    api_key: str = Form(...),
    secret_fragment: str = Form(...),
):
    user_id = (user_id or "").strip()
    api_key = (api_key or "").strip()
    secret_fragment = (secret_fragment or "").strip()
    if not user_id or not api_key or not secret_fragment:
        return RedirectResponse("/settings/credentials?error=1", status_code=302)
    if user_id == ctx.user_id:
        if cred_manager.update_credentials(ctx.user_id, api_key, secret_fragment):
            return RedirectResponse("/settings/credentials?updated=1", status_code=302)
        return RedirectResponse("/settings/credentials?error=1", status_code=302)
    google_id = getattr(ctx, "google_id", None)
    if not google_id:
        with sqlite3.connect(cfg.DATA_PATH + "db.sqlite3") as conn:
            google_id = get_google_id_by_user_id(conn, ctx.user_id)
    if not google_id:
        return RedirectResponse("/settings/credentials?error=no_account", status_code=302)
    with sqlite3.connect(cfg.DATA_PATH + "db.sqlite3") as conn:
        row = conn.execute(
            "SELECT roles FROM user_account WHERE google_id = ?",
            (google_id,),
        ).fetchone()
    if not row:
        return RedirectResponse("/settings/credentials?error=no_account", status_code=302)
    roles = row[0] or '["trader"]'
    try:
        with sqlite3.connect(cfg.DATA_PATH + "db.sqlite3") as conn:
            if change_user_id(
                conn,
                ctx.user_id,
                user_id,
                google_id,
                roles,
                cred_manager,
                api_key,
                secret_fragment,
            ):
                response = RedirectResponse("/logout", status_code=302)
                return response
    except sqlite3.IntegrityError:
        pass
    return RedirectResponse("/settings/credentials?error=user_id_taken", status_code=302)


@router.get("/quantity-limits")
async def settings_quantity_limits_get(
    request: Request,
    ctx: RequestContext = Depends(get_request_context_or_redirect),
):
    message = None
    if request.query_params.get("updated"):
        message = "Quantity limits updated."

    customer = breeze.get_customer_details(ctx.user_id)
    if customer is None:
        customer = {"Status": 400, "Error": "Not available", "Success": {"idirect_user_name": "—"}}
    elif customer.get("Status") != 200:
        customer = {"Status": 400, "Error": customer.get("Error", ""), "Success": {"idirect_user_name": "—"}}

    margin = breeze.get_margin_situation(ctx.user_id, target_margin_ute=100)
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

    limits: List[dict[str, Any]] = []
    with sqlite3.connect(cfg.DATA_PATH + cfg.SCRIP_DB) as conn:
        try:
            rows = conn.execute(
                '''
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
                '''
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
            message = message or "Quantity limits not available. Ensure master data has been loaded."

    return templates.TemplateResponse(
        "settings_quantity_limits.html",
        {
            "request": request,
            "is_logged_in": True,
            "login_url": None,
            "active": "settings",
            "customer": customer,
            "margin": margin,
            "limits": limits,
            "message": message,
            "user_id": ctx.user_id,
            **get_common_template_vars(ctx),
        },
    )


@router.post("/quantity-limits")
async def settings_quantity_limits_post(
    request: Request,
    ctx: RequestContext = Depends(get_request_context_or_redirect),
    short_names: List[str] = Form(...),
    exchange_codes: List[str] = Form(...),
    segment_codes: List[str] = Form(...),
    qty_limits: List[int] = Form(...),
):
    if not (short_names and exchange_codes and segment_codes and qty_limits) or not (
        len(short_names) == len(exchange_codes) == len(segment_codes) == len(qty_limits)
    ):
        return RedirectResponse("/settings/quantity-limits?error=1", status_code=302)

    with sqlite3.connect(cfg.DATA_PATH + cfg.SCRIP_DB) as conn:
        cur = conn.cursor()
        for sn, ec, seg, q in zip(short_names, exchange_codes, segment_codes, qty_limits):
            # Update the canonical limits table.
            cur.execute(
                '''
                UPDATE raw_limits_data
                SET QtyLimit = ?
                WHERE ShortName = ? AND ExchangeCode = ? AND SegmentCode = ?
                ''',
                (int(q), sn, ec, seg or None),
            )
            # Keep scrip_master QuantityLimit in sync for the runtime lookups.
            cur.execute(
                '''
                UPDATE scrip_master
                SET QuantityLimit = ?
                WHERE ShortName = ? AND ExchangeCode = ? AND SegmentCode = ?
                ''',
                (int(q), sn, ec, seg or None),
            )
        conn.commit()

    return RedirectResponse("/settings/quantity-limits?updated=1", status_code=302)
