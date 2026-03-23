"""Settings JSON API under /api/settings."""
import sqlite3
from typing import Any, List

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.auth.context import get_request_context, RequestContext
from icici_breeze_backend.app.services.processor import processor
from icici_breeze_backend.app.auth.credentials import CredentialManager
from icici_breeze_backend.app.auth.user_account import change_user_id
from icici_breeze_backend.app.domain.settings_api import (
    CredentialsStateResponse,
    CredentialsUpdateBody,
    QuantityLimitsStateResponse,
    QuantityLimitsUpdateBody,
)

router = APIRouter(prefix="/api/settings", tags=["settings"])
breeze = processor()
cred_manager = CredentialManager(encryption_key=(cfg.JWT_SECRET or "").strip())


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
