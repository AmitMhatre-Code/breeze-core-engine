from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Request, Depends, HTTPException, Query
from icici_breeze_backend.app.services.processor import _expiry_display_to_api, processor
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
        return data
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
    data = {**data, "Success": {"positions": positions}}
    return data


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

    if body.action == cfg.HEDGE:
        return json_redirect(
            f"/hedge?action={body.position_action}&product_type={body.product_type}&stock_code={body.stock_code}"
            f"&exchange_code={body.exchange_code}&expiry_date={body.expiry_date}&right={body.right}"
            f"&strike_price={body.strike_price}&quantity={body.quantity}&top=3"
        )
    return json_redirect("/portfolio")


@router.get("/data", response_model=IciciApiResponse)
async def get_portfolio_api(ctx: RequestContext = Depends(get_request_context)):
    user_id = ctx.user_id
    if not ctx.broker_token:
        raise HTTPException(status_code=401, detail="ICICI broker token missing; re-login required")

    # Same enrichment as legacy `processor.get_positions`: NFO/BFO options only,
    # quotes, current_profit / carry_profit, span & ELM, hedgeable.
    data = breeze.get_positions(user_id)
    if isinstance(data, dict):
        data = _normalize_portfolio_success_for_ui(data)
    AuditLogger(None).log_portfolio_access(user_id)
    return IciciApiResponse.model_validate(data)


def _normalize_option_right(raw: Optional[str]) -> Optional[str]:
    if raw is None:
        return None
    t = str(raw).strip().lower()
    if t in ("call", "c", str(cfg.CALL).lower()):
        return cfg.CALL
    if t in ("put", "p", str(cfg.PUT).lower()):
        return cfg.PUT
    return None


@router.get("/hedge-candidates", response_model=IciciApiResponse)
async def get_portfolio_hedge_candidates(
    ctx: RequestContext = Depends(get_request_context),
    stock_code: str = Query(..., min_length=1),
    exchange_code: str = Query(default=cfg.NFO),
    expiry_date: str = Query(..., min_length=1, description="Display expiry e.g. 24-Mar-2026"),
    right: str = Query(..., min_length=1),
    strike_price: str = Query(..., min_length=1),
    quantity: str = Query(..., min_length=1),
    top: int = Query(default=5, ge=1, le=10),
):
    """Top-N hedge legs for a short option row (same logic as Strategy Builder / processor.hedge)."""
    user_id = ctx.user_id
    if not ctx.broker_token:
        raise HTTPException(status_code=401, detail="ICICI broker token missing; re-login required")
    r = _normalize_option_right(right)
    if not r:
        return IciciApiResponse.model_validate(
            {
                "Status": 400,
                "Success": None,
                "Error": "Invalid right; expected Call or Put",
            }
        )
    try:
        exp_api = _expiry_display_to_api(str(expiry_date).strip())
    except (ValueError, TypeError):
        return IciciApiResponse.model_validate(
            {
                "Status": 400,
                "Success": None,
                "Error": "Invalid expiry_date format",
            }
        )
    try:
        k = int(float(str(strike_price).strip().replace(",", "")))
    except (ValueError, TypeError):
        return IciciApiResponse.model_validate(
            {
                "Status": 400,
                "Success": None,
                "Error": "Invalid strike_price",
            }
        )
    try:
        qf = abs(float(str(quantity).strip().replace(",", "")))
    except (ValueError, TypeError):
        return IciciApiResponse.model_validate(
            {
                "Status": 400,
                "Success": None,
                "Error": "Invalid quantity",
            }
        )
    if qf <= 0:
        return IciciApiResponse.model_validate(
            {
                "Status": 400,
                "Success": None,
                "Error": "Quantity must be positive",
            }
        )
    ex = (exchange_code or cfg.NFO).strip() or cfg.NFO
    data = breeze.hedge(
        user_id,
        r,
        cfg.SELL,
        str(stock_code).strip(),
        qf,
        exp_api,
        k,
        top,
        exchange_code=ex,
    )
    if isinstance(data, dict):
        return IciciApiResponse.model_validate(data)
    return IciciApiResponse.model_validate(
        {"Status": 400, "Success": None, "Error": "Unexpected hedge response"}
    )
