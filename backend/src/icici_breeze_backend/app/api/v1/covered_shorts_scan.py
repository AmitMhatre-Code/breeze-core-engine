"""Covered-shorts scan: uncovered_shorts + best hedge per row (shared by API routes)."""
import logging
from typing import Any, Optional

from fastapi import HTTPException

from icici_breeze_backend.app.domain.responses import UncoveredShortsScanResponse
from icici_breeze_backend.app.services.processor import _expiry_display_to_api
from icici_breeze_backend.app.services.nsccl_baseline import MARGIN_SOURCE_BREEZE
import icici_breeze_backend.app.core.config as cfg

logger = logging.getLogger(__name__)


def attach_best_hedge_to_shorts(
    breeze: Any,
    user_id: str,
    side: dict[str, Any],
    right: str,
    exchange_code: str,
) -> None:
    """Mutate each option dict in side['Success'] with hedge_match: Status, Error, best."""
    if side.get("Status") != 200 or not isinstance(side.get("Success"), list):
        return
    for opt in side["Success"]:
        if not isinstance(opt, dict):
            continue
        exp_disp = opt.get("expiry_date")
        try:
            exp_api = (
                _expiry_display_to_api(str(exp_disp).strip()) if exp_disp else ""
            )
        except (ValueError, TypeError):
            opt["hedge_match"] = {
                "Status": 400,
                "Error": "Invalid expiry_date on short row",
                "best": None,
            }
            continue
        strike = opt.get("strike_price")
        qty = opt.get("quantity")
        stock = opt.get("stock_code")
        if stock is None or qty is None or strike is None:
            opt["hedge_match"] = {
                "Status": 400,
                "Error": "Missing stock_code, quantity, or strike_price",
                "best": None,
            }
            continue
        try:
            h = breeze.hedge(
                user_id,
                right,
                cfg.SELL,
                str(stock).strip(),
                float(qty),
                exp_api,
                int(strike),
                1,
                exchange_code=exchange_code or cfg.NFO,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("covered-shorts-scan hedge failed: %s", e, exc_info=True)
            opt["hedge_match"] = {
                "Status": 400,
                "Error": str(e) or "Hedge request failed",
                "best": None,
            }
            continue
        best = None
        if h.get("Status") == 200 and h.get("Success"):
            lst = h["Success"]
            if isinstance(lst, list) and len(lst) > 0:
                best = lst[0]
        opt["hedge_match"] = {
            "Status": h.get("Status"),
            "Error": (h.get("Error") or "")
            if isinstance(h.get("Error"), str)
            else str(h.get("Error") or ""),
            "best": best,
        }


def run_covered_shorts_scan(
    breeze: Any,
    user_id: str,
    stock_code: str,
    expiry_date: str,
    limits: int,
    top: int,
    otm_call_min: int = 10,
    otm_call_max: int = 10,
    otm_put_min: int = 10,
    otm_put_max: int = 10,
    provision_elm: Optional[str] = None,
    exchange_code: str = cfg.NFO,
    margin_source: str = MARGIN_SOURCE_BREEZE,
) -> UncoveredShortsScanResponse:
    """Uncovered short scan (top 1–5) plus best hedge per candidate."""
    if limits <= 0:
        raise HTTPException(status_code=400, detail="limits (margin lacs) must be positive")
    if top < 1 or top > 5:
        raise HTTPException(status_code=400, detail="top must be between 1 and 5")
    if min(otm_call_min, otm_call_max, otm_put_min, otm_put_max) < 0 or max(otm_call_min, otm_call_max, otm_put_min, otm_put_max) > 20:
        raise HTTPException(status_code=400, detail="OTM range values must be between 0 and 20")
    if otm_call_min > otm_call_max or otm_put_min > otm_put_max:
        raise HTTPException(status_code=400, detail="OTM min must be <= OTM max")
    elm = cfg.CHECKED if provision_elm in (cfg.CHECKED, "on", "true", "1") else None
    ex = exchange_code or cfg.NFO
    raw = breeze.uncovered_shorts(
        user_id,
        stock_code=stock_code.strip(),
        expiry_date=expiry_date.strip(),
        limits=limits,
        elm=elm,
        otm_call_min=otm_call_min,
        otm_call_max=otm_call_max,
        otm_put_min=otm_put_min,
        otm_put_max=otm_put_max,
        top=top,
        exchange_code=ex,
        margin_source=margin_source,
    )
    ce = raw.get("ce_options") or {}
    pe = raw.get("pe_options") or {}
    attach_best_hedge_to_shorts(breeze, user_id, ce, cfg.CALL, ex)
    attach_best_hedge_to_shorts(breeze, user_id, pe, cfg.PUT, ex)
    return UncoveredShortsScanResponse(ce_options=ce, pe_options=pe)
