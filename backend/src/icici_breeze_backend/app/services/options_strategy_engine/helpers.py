"""Shared helpers for the options strategy engine."""
from __future__ import annotations

import datetime
import math
from typing import Any

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.core.strike import Strike, parse_strike, strike_for_broker
from icici_breeze_backend.app.services.options_strategy_engine.types import (
    POP_PRE_FILTER_TOLERANCE,
    EngineContext,
    QuoteRow,
    Right,
    StrategyResult,
    TradeLeg,
)


def parse_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def normalize_expiry_display(expiry_date: str) -> str:
    s = expiry_date.strip()
    if len(s) == 10 and s[4] == "-":
        from datetime import datetime

        return datetime.strptime(s, "%Y-%m-%d").strftime("%d-%b-%Y")
    return s


def days_to_expiry(expiry_str: str) -> int:
    """Days until expiry; mirrors processor._days_to_expiry without importing processor."""
    s = expiry_str.removesuffix("T06:00:00.000Z")
    try:
        if len(s.split("-")[0]) == 4:
            future_d = datetime.datetime.strptime(s, "%Y-%m-%d").date()
        else:
            future_d = datetime.datetime.strptime(s, "%d-%b-%Y").date()
    except ValueError:
        return 0
    from icici_breeze_backend.app.core.timezone import today_ist_date

    return (future_d - today_ist_date()).days + 1


def years_to_expiry(expiry_display: str) -> float:
    return max(days_to_expiry(expiry_display), 1) / 365.0


def annualized_carry_percent_on_span(
    premium: float, days_to_expiry: int, span_margin: float
) -> float:
    """(Premium / DTE) * 365 / total margin * 100, as a display percentage.

    The margin argument is SPAN alone for strategies, or SPAN + ELM for portfolio carry returns.
    """
    dte = max(1, int(days_to_expiry))
    try:
        sm = float(span_margin)
        pr = float(premium)
    except (TypeError, ValueError):
        return 0.0
    if sm <= 0 or not math.isfinite(sm) or not math.isfinite(pr):
        return 0.0
    return (pr / dte) * (365.0 / sm) * 100.0


def quote_from_api(strike: Strike, right: Right, payload: dict) -> QuoteRow:
    tb = int(payload.get("total_buy_qty") or 0)
    ts = int(payload.get("total_sell_qty") or 0)
    ratio: float | str = 0.0
    if ts > 0:
        ratio = round(tb / ts, 4)
    elif tb > 0:
        ratio = "NA"
    return QuoteRow(
        strike=strike,
        right=right,
        ltp=parse_float(payload.get("ltp")),
        best_bid_price=parse_float(payload.get("best_bid_price")),
        best_offer_price=parse_float(payload.get("best_offer_price")),
        total_buy_qty=tb,
        total_sell_qty=ts,
        buy_sell_ratio=ratio,
        spot_price=parse_float(payload.get("spot_price")) if payload.get("spot_price") is not None else None,
        oi=int(payload.get("open_interest") or payload.get("oi") or 0),
    )


def nearest_atm(strikes: list[Strike], spot: float) -> Strike:
    return min(strikes, key=lambda s: abs(s - spot))


def snap_user_range(strikes: list[Strike], range_lower: float, range_upper: float) -> tuple[float, float]:
    lo_strike = min(strikes, key=lambda s: abs(s - range_lower))
    hi_strike = min(strikes, key=lambda s: abs(s - range_upper))
    return (float(min(lo_strike, hi_strike)), float(max(lo_strike, hi_strike)))


def strike_window(
    all_strikes: list[Strike],
    range_lower: float,
    range_upper: float,
    atm: Strike,
    step: float,
    pad_intervals: int = 3,
) -> list[Strike]:
    lo = range_lower - pad_intervals * step
    hi = range_upper + pad_intervals * step
    window = [s for s in all_strikes if lo <= s <= hi]
    if atm not in window and atm in all_strikes:
        window.append(atm)
    return sorted(set(window))


def floor_lots(qty_rupees: float, per_lot_cost: float, lot_size: int) -> int:
    if per_lot_cost <= 0 or lot_size <= 0:
        return 0
    lots = math.floor(qty_rupees / per_lot_cost)
    return max(0, lots) * lot_size


def margin_key(legs: list[TradeLeg], stock: str, expiry: str, ex: str) -> tuple:
    parts = tuple(
        sorted(
            (stock, ex, expiry, leg.right, leg.side, leg.strike, leg.quantity)
            for leg in legs
        )
    )
    return parts


def legs_to_margin_input(
    legs: list[TradeLeg],
    stock_code: str,
    exchange_code: str,
    expiry_display: str,
) -> list[dict]:
    out = []
    for leg in legs:
        out.append(
            {
                "stock_code": stock_code,
                "exchange_code": exchange_code,
                "expiry_date": expiry_display,
                "product_type": cfg.OPTIONS,
                "right": leg.right,
                "strike_price": strike_for_broker(leg.strike),
                "quantity": str(leg.quantity),
                "price": str(leg.premium_per_unit),
                "action": leg.side,
            }
        )
    return out


def net_premium(legs: list[TradeLeg]) -> float:
    total = 0.0
    for leg in legs:
        flow = leg.premium_per_unit * leg.quantity
        if leg.side == "Sell":
            total += flow
        else:
            total -= flow
    return round(total, 2)


def elm_addon(spot: float, lot_size: int, short_lots: int, provision_elm: bool) -> float:
    if not provision_elm or short_lots <= 0:
        return 0.0
    return spot * lot_size * short_lots * 0.02


def short_lots_in_legs(legs: list[TradeLeg], lot_size: int) -> int:
    """Count short lots across all sell legs (ELM applies per short leg)."""
    if lot_size <= 0:
        return 0
    return sum(leg.quantity // lot_size for leg in legs if leg.side == "Sell")


def elm_for_legs(ctx: EngineContext, legs: list[TradeLeg]) -> float | None:
    if not ctx.provision_elm or not legs:
        return None
    short_lots = short_lots_in_legs(legs, ctx.lot_size)
    if short_lots <= 0:
        return None
    return round(elm_addon(ctx.spot, ctx.lot_size, short_lots, True), 2)


def skip(strategy_id: str, name: str, reason: str, modified: bool = False) -> StrategyResult:
    return StrategyResult(strategy_id, name, "skipped", reason, modified)


def sigma_for_pop(ctx: EngineContext) -> float:
    if ctx.atm_iv and ctx.atm_iv > 0:
        return ctx.atm_iv
    return 0.20


def requires_pop_gate(ctx: EngineContext) -> bool:
    return ctx.strategy_category == "income"


def pre_filter_pop_floor(min_pop_pct: float) -> float:
    """Soft PoP floor for strike/pair search only; never used as a hard reject gate."""
    return max(0.0, min_pop_pct - POP_PRE_FILTER_TOLERANCE)


def meets_pop_floor(ctx: EngineContext, pop: float) -> bool:
    if not requires_pop_gate(ctx):
        return True
    return pop >= ctx.min_pop_pct


_LAC = 100_000.0
_CRORE = 10_000_000.0


def format_indian_money_compact(amount: float) -> str:
    """Compact ₹ display using Lac / Crore (and K below 1 Lac). Mirrors frontend format-money-in.ts."""
    if not isinstance(amount, (int, float)) or math.isnan(amount):
        return "—"
    abs_amt = abs(amount)
    wrap = (lambda s: f"({s})") if amount < 0 else (lambda s: s)
    if abs_amt >= _CRORE:
        return wrap(f"₹{abs_amt / _CRORE:,.2f} Cr")
    if abs_amt >= _LAC:
        return wrap(f"₹{abs_amt / _LAC:,.2f} Lac")
    if abs_amt >= 1000:
        return wrap(f"₹{abs_amt / 1000:,.2f} K")
    return wrap(f"₹{abs_amt:,.0f}")

