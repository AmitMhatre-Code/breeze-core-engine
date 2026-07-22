"""Parse and normalize ICICI Breeze WebSocket option quote ticks."""
from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass
from typing import Any

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.strike import Strike, parse_strike


@dataclass(frozen=True)
class ParsedTick:
    exchange_code: str
    stock_code: str
    expiry_display: str
    strike: Strike
    right: str  # "call" | "put"
    raw: dict[str, Any]


def _normalize_expiry_display(expiry: str) -> str:
    s = str(expiry or "").strip()
    if not s:
        return s
    if "T" in s or (len(s) == 10 and s[4] == "-"):
        try:
            if "T" in s:
                s = s.split("T", 1)[0]
            return dt.datetime.strptime(s[:10], "%Y-%m-%d").strftime("%d-%b-%Y")
        except ValueError:
            pass
    return s


def _resolve_exchange_code(tick: dict[str, Any]) -> str:
    ex_raw = str(tick.get("exchange_code") or tick.get("exchange") or "").upper()
    if "BSE" in ex_raw:
        return cfg.BFO
    return cfg.NFO


def _resolve_stock_short(tick: dict[str, Any]) -> str:
    stock = str(tick.get("stock_code") or tick.get("stock_name") or "").strip()
    return stock.split()[0].upper() if stock else stock


def _resolve_right_key(right_raw: Any) -> str:
    return "call" if str(right_raw or "").lower().startswith("c") else "put"


def _parse_tick_from_fields(ticks: dict[str, Any]) -> ParsedTick | None:
    stock = _resolve_stock_short(ticks)
    expiry = _normalize_expiry_display(str(ticks.get("expiry_date") or "").strip())
    strike = parse_strike(ticks.get("strike_price"))
    right_raw = ticks.get("right") or ticks.get("right_type") or ""
    if not stock or not expiry or strike is None:
        return None
    return ParsedTick(
        exchange_code=_resolve_exchange_code(ticks),
        stock_code=stock,
        expiry_display=expiry,
        strike=strike,
        right=_resolve_right_key(right_raw),
        raw=ticks,
    )


def _parse_tick_from_symbol(ticks: dict[str, Any]) -> ParsedTick | None:
    from icici_breeze_backend.app.services.reference_data.ws_token_index import (
        lookup_contract_by_ws_symbol,
        option_type_to_right,
    )

    contract = lookup_contract_by_ws_symbol(str(ticks.get("symbol") or ""))
    if contract is None:
        return None
    strike = parse_strike(contract.strike_price)
    if strike is None:
        return None
    expiry = _normalize_expiry_display(contract.expiry_display)
    if not expiry:
        return None
    return ParsedTick(
        exchange_code=contract.exchange_code,
        stock_code=contract.stock_code,
        expiry_display=expiry,
        strike=strike,
        right=option_type_to_right(contract.option_type),
        raw=ticks,
    )


def parse_icici_tick(ticks: Any) -> ParsedTick | None:
    """Extract contract identity from a raw ICICI tick dict, or None if unusable."""
    if not isinstance(ticks, dict):
        return None
    parsed = _parse_tick_from_fields(ticks)
    if parsed is not None:
        return parsed
    return _parse_tick_from_symbol(ticks)


def normalize_tick_cell(
    tick: dict[str, Any],
    *,
    stock_code: str,
    expiry_display: str,
    right: str,
    strike: Strike,
    updated_at: float | None = None,
) -> dict[str, Any]:
    """Map ICICI field names to the app's chain cell shape."""
    total_buy = int(tick.get("totalBuyQt") or tick.get("total_buy_qty") or 0)
    total_sell = int(tick.get("totalSellQ") or tick.get("total_sell_qty") or 0)
    if total_sell > 0:
        ratio: float | str = total_buy / total_sell
    else:
        ratio = 0.0 if total_buy == 0 else "NA"
    ltp = tick.get("last") if tick.get("last") is not None else tick.get("ltp")
    return {
        "stock_code": stock_code,
        "strike_price": strike,
        "right": cfg.CALL if str(right).lower().startswith("c") else cfg.PUT,
        "expiry_date": expiry_display,
        "ltp": ltp,
        "open_interest": int(tick.get("OI") or tick.get("open_interest") or 0),
        "total_buy_qty": total_buy,
        "total_sell_qty": total_sell,
        "buy_sell_ratio": ratio,
        "best_bid_price": tick.get("bPrice") or tick.get("best_bid_price"),
        "best_offer_price": tick.get("sPrice") or tick.get("best_offer_price"),
        "spot_price": tick.get("spot_price"),
        "lot_size": tick.get("lot_size"),
        "updated_at": updated_at if updated_at is not None else time.time(),
    }


def normalize_icici_tick(ticks: Any, *, updated_at: float | None = None) -> tuple[ParsedTick, dict[str, Any]] | None:
    """Parse + normalize a raw tick. Returns (parsed, cell) or None."""
    parsed = parse_icici_tick(ticks)
    if parsed is None:
        return None
    cell = normalize_tick_cell(
        parsed.raw,
        stock_code=parsed.stock_code,
        expiry_display=parsed.expiry_display,
        right=parsed.right,
        strike=parsed.strike,
        updated_at=updated_at,
    )
    return parsed, cell
