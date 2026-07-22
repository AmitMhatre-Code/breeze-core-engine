"""Assemble option chain payloads from per-contract quote cells."""
from __future__ import annotations

from typing import Any

from icici_breeze_backend.app.core.strike import Strike, parse_strike


def assemble_chain_payload(
    *,
    stock_code: str,
    exchange_code: str,
    expiry_display: str,
    strikes: list[Strike],
    call_by_strike: dict[Strike, dict[str, Any]],
    put_by_strike: dict[Strike, dict[str, Any]],
    lot_size: int = 0,
    freeze_quantity: int | None = None,
    quote_source: str = "websocket",
    bhavcopy_date: str | None = None,
) -> dict[str, Any] | None:
    calls = list(call_by_strike.values())
    puts = list(put_by_strike.values())
    if not calls and not puts:
        return None

    chain_strikes = sorted(set(strikes) | set(call_by_strike) | set(put_by_strike))
    chain_rows = [
        {
            "strike_price": k,
            "call": call_by_strike.get(k),
            "put": put_by_strike.get(k),
        }
        for k in chain_strikes
    ]

    spot_price = None
    for cell in calls + puts:
        sp = cell.get("spot_price")
        if sp is not None:
            try:
                spot_price = float(sp)
                break
            except (TypeError, ValueError):
                pass

    max_call_oi = max((r.get("open_interest", 0) for r in calls), default=0)
    max_put_oi = max((r.get("open_interest", 0) for r in puts), default=0)
    atm_strike = None
    if spot_price is not None and chain_strikes:
        atm_strike = min(chain_strikes, key=lambda s: abs(s - spot_price))

    return {
        "chain_rows": chain_rows,
        "max_call_oi": max_call_oi,
        "max_put_oi": max_put_oi,
        "expiry_display": expiry_display,
        "stock_code": stock_code,
        "exchange_code": exchange_code,
        "spot_price": spot_price,
        "atm_strike": atm_strike,
        "lot_size": lot_size or None,
        "freeze_quantity": freeze_quantity,
        "quote_source": quote_source,
        "bhavcopy_date": bhavcopy_date,
    }


def cells_from_stored_quotes(
    exchange_code: str,
    stock_code: str,
    expiry_display: str,
    strikes: list[Strike],
    *,
    lot_size: int = 0,
) -> tuple[dict[Strike, dict[str, Any]], dict[Strike, dict[str, Any]]]:
    from icici_breeze_backend.app.db.redis_client import cache_get_json
    from icici_breeze_backend.app.services.reference_data.keys import ws_quote_key

    call_by: dict[Strike, dict[str, Any]] = {}
    put_by: dict[Strike, dict[str, Any]] = {}
    for strike in strikes:
        for right, bucket in (("call", call_by), ("put", put_by)):
            key = ws_quote_key(exchange_code, stock_code, expiry_display, strike, right)
            cell = cache_get_json(key)
            if not cell:
                continue
            strike_f = parse_strike(cell.get("strike_price", strike))
            if strike_f is None:
                continue
            if lot_size:
                cell = dict(cell)
                cell["lot_size"] = lot_size
            bucket[strike_f] = cell
    return call_by, put_by
