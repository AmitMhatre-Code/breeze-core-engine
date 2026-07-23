from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Request, Depends, HTTPException
from icici_breeze_backend.app.services.processor import processor
import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.auth.context import get_request_context, get_request_context_or_redirect, RequestContext
from icici_breeze_backend.app.domain.portfolio import PortfolioActionRequest
from icici_breeze_backend.app.domain.responses import IciciApiResponse
from icici_breeze_backend.audit.logger import AuditLogger
from icici_breeze_backend.app.api.frontend_redirect import redirect_to_frontend, json_redirect


def _maybe_coerce_float(out: Dict[str, Any], key: str) -> None:
    v = out.get(key)
    if v is None or v == "" or v == "Err" or isinstance(v, bool):
        return
    if isinstance(v, (int, float)):
        return
    if isinstance(v, str):
        t = v.strip()
        if not t or t == "*":
            return
        try:
            out[key] = float(t)
        except (ValueError, TypeError):
            pass


def _coerce_position_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize broker strings to JSON numbers; keep spot_price 'Err' as-is."""
    out = dict(row)
    q = out.get("quantity")
    if q is None:
        out["quantity"] = 0
    elif isinstance(q, str):
        try:
            out["quantity"] = int(float(q.strip() or 0))
        except (ValueError, TypeError):
            out["quantity"] = 0
    elif not isinstance(q, int):
        try:
            out["quantity"] = int(q)
        except (ValueError, TypeError):
            out["quantity"] = 0
    p = out.get("pnl")
    if p is None:
        out["pnl"] = 0.0
    elif isinstance(p, str):
        try:
            out["pnl"] = float(p.strip() or 0)
        except (ValueError, TypeError):
            out["pnl"] = 0.0
    elif isinstance(p, bool):
        out["pnl"] = 0.0
    elif not isinstance(p, (int, float)):
        try:
            out["pnl"] = float(p)
        except (ValueError, TypeError):
            out["pnl"] = 0.0
    for key in (
        "average_price",
        "ltp",
        "strike_price",
        "margin_amount",
        "price",
        "current_profit",
        "carry_profit",
        "span_margin_required",
        "carry_margin_returns",
        "elm_margin_required",
        "days_to_expiry",
    ):
        _maybe_coerce_float(out, key)
    return out


def _normalize_portfolio_success_for_ui(data: Dict[str, Any]) -> Dict[str, Any]:
    """ICICI Breeze uses Success: [ {...}, ... ]; UI expects Success: { positions: [...] }."""
    if not isinstance(data, dict) or data.get("Status") != 200:
        return data
    success = data.get("Success")
    if success is None:
        return {**data, "Success": {"positions": []}, "Error": None}
    rows: Optional[List[Any]] = None
    if isinstance(success, list):
        rows = success
    elif isinstance(success, dict) and isinstance(success.get("positions"), list):
        rows = success["positions"]
    if rows is None:
        return data
    positions = [
        _coerce_position_row(r) if isinstance(r, dict) else r for r in rows
    ]
    success_out: Dict[str, Any] = {"positions": positions}
    # Netted group/portfolio SPAN + ELM (computed in processor.get_positions) rides
    # alongside positions. Only present when there are positions, so the empty-portfolio
    # response stays exactly {"positions": []}.
    if isinstance(data.get("groups"), list):
        success_out["groups"] = data["groups"]
    if isinstance(data.get("portfolio"), dict):
        success_out["portfolio"] = data["portfolio"]
    out = {k: v for k, v in data.items() if k not in ("groups", "portfolio")}
    out["Success"] = success_out
    if not positions:
        out["Error"] = None
    return out


router = APIRouter()
breeze = processor()


@router.get("")
async def serve_landing(request: Request):
    q = request.url.query
    return redirect_to_frontend("/portfolio" + ("?" + q if q else ""))


@router.post("")
async def process_post(
    body: PortfolioActionRequest,
    context: RequestContext = Depends(get_request_context_or_redirect),
):
    if not context.broker_token:
        raise HTTPException(status_code=401, detail="ICICI broker token missing; re-login required")

    if body.action == cfg.SQUAREOFF:
        q = urlencode(
            {
                "action": body.action,
                "position": body.position_action,
                "product_type": body.product_type,
                "stock_code": body.stock_code,
                "exchange_code": body.exchange_code,
                "expiry_date": body.expiry_date,
                "right": body.right,
                "strike_price": body.strike_price,
                "quantity": body.quantity,
            }
        )
        return json_redirect(f"/place-order?{q}")

    return json_redirect("/portfolio")


@router.get("/data", response_model=IciciApiResponse)
async def get_portfolio_api(ctx: RequestContext = Depends(get_request_context)):
    user_id = ctx.user_id
    if not ctx.broker_token:
        raise HTTPException(status_code=401, detail="ICICI broker token missing; re-login required")

    # Same enrichment as legacy `processor.get_positions`: NFO/BFO options only,
    # quotes, current_profit / carry_profit, span & ELM.
    data = breeze.get_positions(user_id)
    if isinstance(data, dict):
        from icici_breeze_backend.app.services.portfolio_pnl_engine import sync_positions_from_response

        sync_positions_from_response(user_id, data)
        data = _normalize_portfolio_success_for_ui(data)
    AuditLogger(None).log_portfolio_access(user_id)
    return IciciApiResponse.model_validate(data)
