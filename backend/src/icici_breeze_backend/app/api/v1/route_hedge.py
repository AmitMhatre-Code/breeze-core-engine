"""Strategy-level hedging API."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.api.deps_license import require_trading_not_revoked
from icici_breeze_backend.app.api.frontend_redirect import redirect_to_frontend
from icici_breeze_backend.app.auth.context import RequestContext, get_request_context
from icici_breeze_backend.app.domain.hedge import StrategyHedgeRequest, StrategyHedgeResponse
from icici_breeze_backend.app.domain.responses import IciciApiResponse
from icici_breeze_backend.app.services.options_strategy_engine.strategy_hedger import (
    flatten_chain_payload,
    generate_strategy_level_hedges,
)
from icici_breeze_backend.app.services.portfolio_margin_netting import normalize_stock
from icici_breeze_backend.app.services.processor import _days_to_expiry, processor
from icici_breeze_backend.app.services.quote_source_router import (
    _normalize_expiry_display,
    fetch_chain_payload_routed,
)
from icici_breeze_backend.audit.logger import AuditLogger, OperationType

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/hedge", tags=["hedge"])
breeze = processor()


def _positions_list(raw: dict[str, Any]) -> list[dict[str, Any]]:
    if raw.get("Status") != 200:
        return []
    success = raw.get("Success")
    if isinstance(success, dict):
        rows = success.get("positions")
        return list(rows) if isinstance(rows, list) else []
    if isinstance(success, list):
        return success
    return []


def _filter_bucket_positions(
    positions: list[dict[str, Any]],
    stock_code: str,
    expiry_display: str,
) -> list[dict[str, Any]]:
    target_stock = normalize_stock(stock_code)
    target_expiry = _normalize_expiry_display(expiry_display)
    out: list[dict[str, Any]] = []
    for row in positions:
        if not isinstance(row, dict):
            continue
        row_stock = normalize_stock(str(row.get("stock_code") or ""))
        row_expiry = _normalize_expiry_display(str(row.get("expiry_date") or ""))
        if row_stock == target_stock and row_expiry == target_expiry:
            out.append(row)
    return out


@router.get("")
async def serve_landing(request: Request):
    q = request.url.query
    return redirect_to_frontend("/hedge" + ("?" + q if q else ""))


@router.post("/strategy-options", response_model=IciciApiResponse)
async def strategy_options(
    body: StrategyHedgeRequest,
    ctx: RequestContext = Depends(get_request_context),
    _: None = Depends(require_trading_not_revoked),
):
    if not ctx.broker_token:
        raise HTTPException(status_code=401, detail="ICICI broker token missing; re-login required")

    stock_code = body.stock_code.strip()
    expiry_display = _normalize_expiry_display(body.expiry_date.strip())
    exchange_code = (body.exchange_code or cfg.NFO).strip() or cfg.NFO

    if not stock_code or not expiry_display:
        raise HTTPException(status_code=400, detail="stock_code and expiry_date required")

    pos_raw = breeze.get_positions(ctx.user_id)
    if pos_raw.get("Status") != 200:
        return IciciApiResponse(
            Status=pos_raw.get("Status") or 400,
            Error=str(pos_raw.get("Error") or "Unable to load portfolio positions"),
            Success=None,
        )

    bucket_positions = _filter_bucket_positions(
        _positions_list(pos_raw),
        stock_code,
        expiry_display,
    )
    if not bucket_positions:
        return IciciApiResponse(
            Status=404,
            Error=f"No open positions for {stock_code} expiring {expiry_display}",
            Success=None,
        )

    chain_payload = fetch_chain_payload_routed(
        breeze,
        ctx.user_id,
        stock_code,
        exchange_code,
        expiry_display,
    )
    if not chain_payload:
        return IciciApiResponse(
            Status=400,
            Error="Unable to fetch option chain from live cache or REST",
            Success=None,
        )

    options_chain = flatten_chain_payload(chain_payload)
    spot_price = float(chain_payload.get("spot_price") or 0.0)
    if spot_price <= 0:
        for row in bucket_positions:
            sp = row.get("spot_price")
            try:
                if sp and str(sp) != "Err":
                    spot_price = float(sp)
                    break
            except (TypeError, ValueError):
                continue
    if spot_price <= 0 and options_chain:
        for opt in options_chain:
            sp = opt.get("spot_price")
            if sp:
                try:
                    spot_price = float(sp)
                    break
                except (TypeError, ValueError):
                    pass

    lot_size = int(chain_payload.get("lot_size") or 0)
    if lot_size <= 0:
        lot_size = breeze.fetch_lot_size(stock_code, expiry_display, exchange_code=exchange_code) or 0
    if lot_size <= 0:
        return IciciApiResponse(
            Status=400,
            Error="Could not resolve lot size for expiry",
            Success=None,
        )

    days_to_expiry = max(1, _days_to_expiry(expiry_display))
    for row in bucket_positions:
        dte = row.get("days_to_expiry")
        if dte is not None:
            try:
                days_to_expiry = max(1, int(dte))
                break
            except (TypeError, ValueError):
                pass

    result = generate_strategy_level_hedges(
        bucket_positions,
        options_chain,
        spot_price,
        body.user_max_loss_preference,
        days_to_expiry=days_to_expiry,
        lot_size=int(lot_size),
        exchange_code=exchange_code,
    )

    AuditLogger(None).log_operation(
        ctx.user_id,
        OperationType.PORTFOLIO_VIEW,
        "StrategyLevelHedge",
    )

    response = StrategyHedgeResponse(
        summary=result["summary"],
        candidates=result["candidates"],
    )
    return IciciApiResponse(Status=200, Error=None, Success=response.model_dump())
