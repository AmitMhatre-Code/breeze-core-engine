"""Batch options strategy engine per docs/options-strategies.md."""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Optional

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.services.iv_compute import implied_volatility
from icici_breeze_backend.app.services.processor import (
    _annualized_carry_percent_on_span,
    _days_to_expiry,
    _expiry_display_to_api,
    processor,
)

_logger = logging.getLogger(__name__)

Right = Literal["Call", "Put"]
Side = Literal["Buy", "Sell"]

STRATEGY_CATALOG: list[tuple[str, str]] = [
    ("naked_ce_short", "Naked CE Short"),
    ("naked_pe_short", "Naked PE Short"),
    ("bull_call_spread", "Bull Call Spread"),
    ("bear_put_spread", "Bear Put Spread"),
    ("bear_call_spread", "Bear Call Spread"),
    ("bull_put_spread", "Bull Put Spread"),
    ("long_straddle", "Long Straddle"),
    ("short_straddle", "Short Straddle"),
    ("short_strangle", "Short Strangle"),
    ("long_call_butterfly", "Long Call Butterfly"),
    ("iron_condor", "Iron Condor"),
    ("iron_butterfly", "Iron Butterfly"),
]


@dataclass
class QuoteRow:
    strike: int
    right: Right
    ltp: float
    best_bid_price: float
    best_offer_price: float
    total_buy_qty: int
    total_sell_qty: int
    buy_sell_ratio: float | str
    spot_price: float | None = None

    @property
    def liquid(self) -> bool:
        return self.total_buy_qty > 0 and self.total_sell_qty > 0


@dataclass
class TradeLeg:
    right: Right
    side: Side
    strike: int
    quantity: int
    premium_per_unit: float

    def to_out(self, cache: dict[tuple[int, Right], QuoteRow]) -> dict[str, Any]:
        q = cache.get((self.strike, self.right))
        return {
            "right": self.right,
            "side": self.side,
            "strike": self.strike,
            "quantity": self.quantity,
            "premium_per_unit": round(self.premium_per_unit, 4),
            "ltp": q.ltp if q else None,
            "best_bid_price": q.best_bid_price if q else None,
            "best_offer_price": q.best_offer_price if q else None,
            "total_buy_qty": q.total_buy_qty if q else None,
            "total_sell_qty": q.total_sell_qty if q else None,
            "buy_sell_ratio": q.buy_sell_ratio if q else None,
        }


@dataclass
class StrategyResult:
    strategy_id: str
    strategy_name: str
    status: Literal["ok", "skipped"] = "skipped"
    skip_reason: str | None = None
    structure_modified: bool = False
    net_premium: float | None = None
    max_loss: float | None = None
    annualized_return_pct: float | None = None
    risk_reward_ratio: str | None = None
    legs: list[TradeLeg] = field(default_factory=list)
    margin_key: tuple | None = None
    span_margin: float | None = None


@dataclass
class EngineContext:
    processor: processor
    user_id: str
    stock_code: str
    exchange_code: str
    expiry_display: str
    range_lower: float
    range_upper: float
    margin_rupees: float
    max_loss_rupees: float
    provision_elm: bool
    lot_size: int
    strikes: list[int]
    strike_step: int
    spot: float
    atm_strike: int
    cache: dict[tuple[int, Right], QuoteRow] = field(default_factory=dict)
    structure_modified: bool = False
    halted: bool = False
    halt_reason: str | None = None

    @property
    def liquid_ce_strikes(self) -> list[int]:
        return sorted(s for s in self.strikes if (s, "Call") in self.cache and self.cache[(s, "Call")].liquid)

    @property
    def liquid_pe_strikes(self) -> list[int]:
        return sorted(s for s in self.strikes if (s, "Put") in self.cache and self.cache[(s, "Put")].liquid)


def _parse_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _normalize_expiry_display(expiry_date: str) -> str:
    s = expiry_date.strip()
    if len(s) == 10 and s[4] == "-":
        from datetime import datetime

        return datetime.strptime(s, "%Y-%m-%d").strftime("%d-%b-%Y")
    return s


def _quote_from_api(strike: int, right: Right, payload: dict) -> QuoteRow:
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
        ltp=_parse_float(payload.get("ltp")),
        best_bid_price=_parse_float(payload.get("best_bid_price")),
        best_offer_price=_parse_float(payload.get("best_offer_price")),
        total_buy_qty=tb,
        total_sell_qty=ts,
        buy_sell_ratio=ratio,
        spot_price=_parse_float(payload.get("spot_price")) if payload.get("spot_price") is not None else None,
    )


def _fetch_quotes(
    processor: processor,
    user_id: str,
    stock_code: str,
    exchange_code: str,
    expiry_display: str,
    pairs: set[tuple[int, Right]],
) -> dict[tuple[int, Right], QuoteRow]:
    cache: dict[tuple[int, Right], QuoteRow] = {}
    expiry_api = _expiry_display_to_api(expiry_display)
    for strike, right in sorted(pairs):
        quote = processor.get_quote(
            user_id,
            stock_code,
            expiry_api,
            cfg.OPTIONS,
            right,
            str(strike),
            exchange_code=exchange_code,
        )
        if quote.get("Status") != 200:
            continue
        rows = quote.get("Success") or []
        if not rows:
            continue
        cache[(strike, right)] = _quote_from_api(strike, right, rows[0])
    return cache


def _strike_window(
    all_strikes: list[int],
    range_lower: float,
    range_upper: float,
    atm: int,
    step: int,
    pad_intervals: int = 3,
) -> list[int]:
    lo = range_lower - pad_intervals * step
    hi = range_upper + pad_intervals * step
    window = [s for s in all_strikes if lo <= s <= hi]
    if atm not in window and atm in all_strikes:
        window.append(atm)
    return sorted(set(window))


def _nearest_atm(strikes: list[int], spot: float) -> int:
    return min(strikes, key=lambda s: abs(s - spot))


def _first_liquid_above(strikes: list[int], level: float) -> int | None:
    for s in strikes:
        if s > level:
            return s
    return None


def _first_liquid_below(strikes: list[int], level: float) -> int | None:
    for s in reversed(strikes):
        if s < level:
            return s
    return None


def _nearest_liquid_ge(strikes: list[int], level: float) -> int | None:
    for s in strikes:
        if s >= level:
            return s
    return None


def _nearest_liquid_le(strikes: list[int], level: float) -> int | None:
    for s in reversed(strikes):
        if s <= level:
            return s
    return None


def _floor_lots(qty_rupees: float, per_lot_cost: float, lot_size: int) -> int:
    if per_lot_cost <= 0 or lot_size <= 0:
        return 0
    lots = math.floor(qty_rupees / per_lot_cost)
    return max(0, lots) * lot_size


def _margin_key(legs: list[TradeLeg], stock: str, expiry: str, ex: str) -> tuple:
    parts = tuple(
        sorted(
            (stock, ex, expiry, leg.right, leg.side, leg.strike, leg.quantity)
            for leg in legs
        )
    )
    return parts


def _legs_to_margin_input(
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
                "strike_price": str(leg.strike),
                "quantity": str(leg.quantity),
                "price": str(leg.premium_per_unit),
                "action": leg.side,
            }
        )
    return out


def _elm_addon(spot: float, lot_size: int, short_lots: int, provision_elm: bool) -> float:
    if not provision_elm:
        return 0.0
    return spot * lot_size * short_lots * 0.02


def _net_premium(legs: list[TradeLeg]) -> float:
    total = 0.0
    for leg in legs:
        flow = leg.premium_per_unit * leg.quantity
        if leg.side == "Sell":
            total += flow
        else:
            total -= flow
    return round(total, 2)


def _build_liquidity_cache(ctx: EngineContext) -> None:
    """Populate quote cache with fallback protocol (doc §4)."""
    all_strikes = ctx.strikes
    if not all_strikes:
        ctx.halted = True
        ctx.halt_reason = "No strikes found in scrip master for this expiry."
        return

    mid = (ctx.range_lower + ctx.range_upper) / 2
    seed_strike = min(all_strikes, key=lambda s: abs(s - mid))
    seed_pairs = {(seed_strike, "Call"), (seed_strike, "Put")}
    ctx.cache.update(_fetch_quotes(ctx.processor, ctx.user_id, ctx.stock_code, ctx.exchange_code, ctx.expiry_display, seed_pairs))

    spot = ctx.spot
    for q in ctx.cache.values():
        if q.spot_price and q.spot_price > 0:
            spot = q.spot_price
            break
    if spot <= 0:
        spot = mid
    ctx.spot = spot
    ctx.atm_strike = _nearest_atm(all_strikes, spot)

    def window_strikes(pad: int) -> list[int]:
        return _strike_window(all_strikes, ctx.range_lower, ctx.range_upper, ctx.atm_strike, ctx.strike_step, pad)

    def fetch_window(pad: int) -> None:
        ws = window_strikes(pad)
        pairs: set[tuple[int, Right]] = set()
        for s in ws:
            pairs.add((s, "Call"))
            pairs.add((s, "Put"))
        new_pairs = pairs - set(ctx.cache.keys())
        if new_pairs:
            ctx.cache.update(
                _fetch_quotes(ctx.processor, ctx.user_id, ctx.stock_code, ctx.exchange_code, ctx.expiry_display, new_pairs)
            )

    fetch_window(3)
    def _has_liquid(ws: list[int]) -> list[int]:
        out: list[int] = []
        for s in ws:
            ce = ctx.cache.get((s, "Call"))
            pe = ctx.cache.get((s, "Put"))
            if (ce and ce.liquid) or (pe and pe.liquid):
                out.append(s)
        return out

    liquid = _has_liquid(window_strikes(3))

    if not liquid:
        fetch_window(6)
        ctx.structure_modified = True
        liquid = _has_liquid(window_strikes(6))

    if not liquid:
        # Step B: compress toward spot — use nearest liquid to ATM
        for pad in [1, 2, 3, 4, 5, 6]:
            near = [s for s in all_strikes if abs(s - ctx.atm_strike) <= pad * ctx.strike_step]
            pairs = {(s, r) for s in near for r in ("Call", "Put")}
            new_pairs = pairs - set(ctx.cache.keys())
            if new_pairs:
                ctx.cache.update(
                    _fetch_quotes(ctx.processor, ctx.user_id, ctx.stock_code, ctx.exchange_code, ctx.expiry_display, new_pairs)
                )
            if ctx.liquid_ce_strikes or ctx.liquid_pe_strikes:
                ctx.structure_modified = True
                break

    if not ctx.liquid_ce_strikes and not ctx.liquid_pe_strikes:
        # Step C: ATM straddle metrics only — still populate ATM quotes
        pairs = {(ctx.atm_strike, "Call"), (ctx.atm_strike, "Put")}
        ctx.cache.update(
            _fetch_quotes(ctx.processor, ctx.user_id, ctx.stock_code, ctx.exchange_code, ctx.expiry_display, pairs)
        )
        if not any(ctx.cache.get((ctx.atm_strike, r)) and ctx.cache[(ctx.atm_strike, r)].liquid for r in ("Call", "Put")):
            ctx.halted = True
            ctx.halt_reason = "Insufficient market depth: no liquid strikes found."
            return
        ctx.structure_modified = True


def _compute_atm_iv(ctx: EngineContext) -> float | None:
    dte = _days_to_expiry(ctx.expiry_display)
    t = max(dte, 1) / 365.0
    ce = ctx.cache.get((ctx.atm_strike, "Call"))
    pe = ctx.cache.get((ctx.atm_strike, "Put"))
    ivs: list[float] = []
    for q, opt in ((ce, "call"), (pe, "put")):
        if not q:
            continue
        px = q.ltp or q.best_offer_price or q.best_bid_price
        if px > 0:
            iv = implied_volatility(px, ctx.spot, ctx.atm_strike, t, opt)
            if iv:
                ivs.append(iv)
    if not ivs:
        return None
    return sum(ivs) / len(ivs)


# --- Strategy calculators ---


def _skip(strategy_id: str, name: str, reason: str, modified: bool = False) -> StrategyResult:
    return StrategyResult(strategy_id, name, "skipped", reason, modified)


def _ok(
    strategy_id: str,
    name: str,
    legs: list[TradeLeg],
    max_loss: float | None,
    rr: str,
    modified: bool = False,
) -> StrategyResult:
    return StrategyResult(
        strategy_id=strategy_id,
        strategy_name=name,
        status="ok",
        legs=legs,
        net_premium=_net_premium(legs),
        max_loss=max_loss,
        risk_reward_ratio=rr,
        structure_modified=modified,
        margin_key=_margin_key(legs, "", "", ""),
    )


def calc_naked_ce_short(ctx: EngineContext) -> StrategyResult:
    sid, name = "naked_ce_short", "Naked CE Short"
    if ctx.halted:
        return _skip(sid, name, ctx.halt_reason or "Market halted")
    stp = _first_liquid_above(ctx.liquid_ce_strikes, ctx.range_upper)
    if stp is None:
        return _skip(sid, name, "No liquid CE strike above range upper bound.")
    q = ctx.cache.get((stp, "Call"))
    if not q:
        return _skip(sid, name, "Quote missing for selected strike.")
    prem = q.best_bid_price or q.ltp
    L = ctx.lot_size
    margin_res = ctx.processor.strategy_builder_margin(
        ctx.user_id,
        ctx.exchange_code,
        _legs_to_margin_input([TradeLeg("Call", "Sell", stp, L, prem)], ctx.stock_code, ctx.exchange_code, ctx.expiry_display),
    )
    span = _parse_float((margin_res.get("Success") or {}).get("span_margin_required"))
    if span <= 0:
        return _skip(sid, name, "Margin calculator returned no SPAN for naked CE.")
    cap_per_lot = span + _elm_addon(ctx.spot, L, 1, ctx.provision_elm)
    qty = _floor_lots(ctx.margin_rupees, cap_per_lot, L)
    if qty < L:
        return _skip(sid, name, "Insufficient margin for one lot.")
    legs = [TradeLeg("Call", "Sell", stp, qty, prem)]
    max_profit = prem * qty
    return StrategyResult(
        sid, name, "ok", legs=legs, net_premium=max_profit,
        max_loss=None,
        risk_reward_ratio=f"Unlimited : {max_profit:.0f}",
        structure_modified=ctx.structure_modified,
        margin_key=_margin_key(legs, ctx.stock_code, ctx.expiry_display, ctx.exchange_code),
    )


def calc_naked_pe_short(ctx: EngineContext) -> StrategyResult:
    sid, name = "naked_pe_short", "Naked PE Short"
    if ctx.halted:
        return _skip(sid, name, ctx.halt_reason or "Market halted")
    stp = _first_liquid_below(ctx.liquid_pe_strikes, ctx.range_lower)
    if stp is None:
        return _skip(sid, name, "No liquid PE strike below range lower bound.")
    q = ctx.cache.get((stp, "Put"))
    if not q:
        return _skip(sid, name, "Quote missing for selected strike.")
    prem = q.best_bid_price or q.ltp
    L = ctx.lot_size
    margin_res = ctx.processor.strategy_builder_margin(
        ctx.user_id,
        ctx.exchange_code,
        _legs_to_margin_input([TradeLeg("Put", "Sell", stp, L, prem)], ctx.stock_code, ctx.exchange_code, ctx.expiry_display),
    )
    span = _parse_float((margin_res.get("Success") or {}).get("span_margin_required"))
    if span <= 0:
        return _skip(sid, name, "Margin calculator returned no SPAN for naked PE.")
    cap_per_lot = span + _elm_addon(ctx.spot, L, 1, ctx.provision_elm)
    qty = _floor_lots(ctx.margin_rupees, cap_per_lot, L)
    if qty < L:
        return _skip(sid, name, "Insufficient margin for one lot.")
    legs = [TradeLeg("Put", "Sell", stp, qty, prem)]
    max_profit = prem * qty
    max_risk = (stp - prem) * qty
    return StrategyResult(
        sid, name, "ok", legs=legs, net_premium=max_profit,
        max_loss=max_risk,
        risk_reward_ratio=f"{max_risk:.0f} : {max_profit:.0f}",
        structure_modified=ctx.structure_modified,
        margin_key=_margin_key(legs, ctx.stock_code, ctx.expiry_display, ctx.exchange_code),
    )


def calc_bull_call_spread(ctx: EngineContext) -> StrategyResult:
    sid, name = "bull_call_spread", "Bull Call Spread"
    if ctx.halted:
        return _skip(sid, name, ctx.halt_reason or "Market halted")
    stp_l = _nearest_liquid_ge(ctx.liquid_ce_strikes, ctx.spot)
    stp_h = _nearest_liquid_ge(ctx.liquid_ce_strikes, ctx.range_upper)
    if stp_l is None or stp_h is None or stp_h <= stp_l:
        return _skip(sid, name, "Could not resolve liquid long/short CE strikes.")
    ql, qh = ctx.cache[(stp_l, "Call")], ctx.cache[(stp_h, "Call")]
    net_per = (ql.best_offer_price or ql.ltp) - (qh.best_bid_price or qh.ltp)
    L = ctx.lot_size
    max_loss_lot = net_per * L
    if max_loss_lot <= 0:
        return _skip(sid, name, "Non-debit bull call spread.")
    qty_m = _floor_lots(ctx.margin_rupees, (ql.best_offer_price or ql.ltp) * L, L)
    qty_l = _floor_lots(ctx.max_loss_rupees, max_loss_lot, L)
    qty = min(qty_m, qty_l) if qty_m and qty_l else 0
    if qty < L:
        return _skip(sid, name, "Insufficient risk appetite for one lot.")
    legs = [
        TradeLeg("Call", "Buy", stp_l, qty, ql.best_offer_price or ql.ltp),
        TradeLeg("Call", "Sell", stp_h, qty, qh.best_bid_price or qh.ltp),
    ]
    max_loss = net_per * qty
    max_profit = ((stp_h - stp_l) - net_per) * qty
    return StrategyResult(
        sid, name, "ok", legs=legs, net_premium=-max_loss, max_loss=max_loss,
        risk_reward_ratio=f"{max_loss:.0f} : {max_profit:.0f}",
        structure_modified=ctx.structure_modified,
        margin_key=_margin_key(legs, ctx.stock_code, ctx.expiry_display, ctx.exchange_code),
    )


def calc_bear_put_spread(ctx: EngineContext) -> StrategyResult:
    sid, name = "bear_put_spread", "Bear Put Spread"
    if ctx.halted:
        return _skip(sid, name, ctx.halt_reason or "Market halted")
    stp_h = _nearest_liquid_le(ctx.liquid_pe_strikes, ctx.spot)
    stp_l = _nearest_liquid_le(ctx.liquid_pe_strikes, ctx.range_lower)
    if stp_l is None or stp_h is None or stp_h <= stp_l:
        return _skip(sid, name, "Could not resolve liquid long/short PE strikes.")
    qh, ql = ctx.cache[(stp_h, "Put")], ctx.cache[(stp_l, "Put")]
    net_per = (qh.best_offer_price or qh.ltp) - (ql.best_bid_price or ql.ltp)
    L = ctx.lot_size
    max_loss_lot = net_per * L
    if max_loss_lot <= 0:
        return _skip(sid, name, "Non-debit bear put spread.")
    qty_m = _floor_lots(ctx.margin_rupees, (qh.best_offer_price or qh.ltp) * L, L)
    qty_l = _floor_lots(ctx.max_loss_rupees, max_loss_lot, L)
    qty = min(qty_m, qty_l) if qty_m and qty_l else 0
    if qty < L:
        return _skip(sid, name, "Insufficient risk appetite for one lot.")
    legs = [
        TradeLeg("Put", "Buy", stp_h, qty, qh.best_offer_price or qh.ltp),
        TradeLeg("Put", "Sell", stp_l, qty, ql.best_bid_price or ql.ltp),
    ]
    max_loss = net_per * qty
    max_profit = ((stp_h - stp_l) - net_per) * qty
    return StrategyResult(
        sid, name, "ok", legs=legs, net_premium=-max_loss, max_loss=max_loss,
        risk_reward_ratio=f"{max_loss:.0f} : {max_profit:.0f}",
        structure_modified=ctx.structure_modified,
        margin_key=_margin_key(legs, ctx.stock_code, ctx.expiry_display, ctx.exchange_code),
    )


def _credit_spread_wing(
    ctx: EngineContext,
    short_stp: int,
    short_right: Right,
    wing_strikes: list[int],
    wing_is_higher: bool,
) -> tuple[int, float, float] | None:
    """Return (wing_stp, net_credit_per_unit, max_loss_per_unit) or None."""
    L = ctx.lot_size
    qs = ctx.cache.get((short_stp, short_right))
    if not qs:
        return None
    candidates = [s for s in wing_strikes if (s > short_stp if wing_is_higher else s < short_stp)]
    if wing_is_higher:
        candidates.sort()
    else:
        candidates.sort(reverse=True)
    qty_margin = _floor_lots(ctx.margin_rupees, max(qs.best_bid_price, 0.05) * L * 2, L) or L
    for wing in candidates:
        qw = ctx.cache.get((wing, short_right))
        if not qw:
            continue
        credit = (qs.best_bid_price or qs.ltp) - (qw.best_offer_price or qw.ltp)
        width = abs(wing - short_stp)
        max_loss_u = width - credit
        if max_loss_u <= 0:
            continue
        if max_loss_u * qty_margin <= ctx.max_loss_rupees:
            return wing, credit, max_loss_u
    if candidates:
        wing = candidates[0]
        qw = ctx.cache.get((wing, short_right))
        if qw:
            credit = (qs.best_bid_price or qs.ltp) - (qw.best_offer_price or qw.ltp)
            width = abs(wing - short_stp)
            max_loss_u = width - credit
            return wing, credit, max_loss_u
    return None


def calc_bear_call_spread(ctx: EngineContext) -> StrategyResult:
    sid, name = "bear_call_spread", "Bear Call Spread"
    if ctx.halted:
        return _skip(sid, name, ctx.halt_reason or "Market halted")
    stp_s = _first_liquid_above(ctx.liquid_ce_strikes, ctx.range_upper)
    if stp_s is None:
        return _skip(sid, name, "No liquid short CE above range.")
    wing = _credit_spread_wing(ctx, stp_s, "Call", ctx.liquid_ce_strikes, True)
    if not wing:
        return _skip(sid, name, "No viable call wing within risk limits.")
    stp_l, credit, max_loss_u = wing
    L = ctx.lot_size
    qty = _floor_lots(ctx.max_loss_rupees, max_loss_u * L, L)
    if qty < L:
        qty = L
    qs, ql = ctx.cache[(stp_s, "Call")], ctx.cache[(stp_l, "Call")]
    legs = [
        TradeLeg("Call", "Sell", stp_s, qty, qs.best_bid_price or qs.ltp),
        TradeLeg("Call", "Buy", stp_l, qty, ql.best_offer_price or ql.ltp),
    ]
    max_loss = max_loss_u * qty
    net_collected = credit * qty
    return StrategyResult(
        sid, name, "ok", legs=legs, net_premium=net_collected, max_loss=max_loss,
        risk_reward_ratio=f"{max_loss:.0f} : {net_collected:.0f}",
        structure_modified=ctx.structure_modified,
        margin_key=_margin_key(legs, ctx.stock_code, ctx.expiry_display, ctx.exchange_code),
    )


def calc_bull_put_spread(ctx: EngineContext) -> StrategyResult:
    sid, name = "bull_put_spread", "Bull Put Spread"
    if ctx.halted:
        return _skip(sid, name, ctx.halt_reason or "Market halted")
    stp_s = _first_liquid_below(ctx.liquid_pe_strikes, ctx.range_lower)
    if stp_s is None:
        return _skip(sid, name, "No liquid short PE below range.")
    wing = _credit_spread_wing(ctx, stp_s, "Put", ctx.liquid_pe_strikes, False)
    if not wing:
        return _skip(sid, name, "No viable put wing within risk limits.")
    stp_l, credit, max_loss_u = wing
    L = ctx.lot_size
    qty = _floor_lots(ctx.max_loss_rupees, max_loss_u * L, L)
    if qty < L:
        qty = L
    qs, ql = ctx.cache[(stp_s, "Put")], ctx.cache[(stp_l, "Put")]
    legs = [
        TradeLeg("Put", "Sell", stp_s, qty, qs.best_bid_price or qs.ltp),
        TradeLeg("Put", "Buy", stp_l, qty, ql.best_offer_price or ql.ltp),
    ]
    max_loss = max_loss_u * qty
    net_collected = credit * qty
    return StrategyResult(
        sid, name, "ok", legs=legs, net_premium=net_collected, max_loss=max_loss,
        risk_reward_ratio=f"{max_loss:.0f} : {net_collected:.0f}",
        structure_modified=ctx.structure_modified,
        margin_key=_margin_key(legs, ctx.stock_code, ctx.expiry_display, ctx.exchange_code),
    )


def _atm_with_liquidity(ctx: EngineContext) -> int | None:
    for s in sorted(ctx.strikes, key=lambda x: abs(x - ctx.atm_strike)):
        ce = ctx.cache.get((s, "Call"))
        pe = ctx.cache.get((s, "Put"))
        if ce and pe and ce.liquid and pe.liquid:
            return s
    return None


def calc_long_straddle(ctx: EngineContext) -> StrategyResult:
    sid, name = "long_straddle", "Long Straddle"
    if ctx.halted:
        return _skip(sid, name, ctx.halt_reason or "Market halted")
    stp = _atm_with_liquidity(ctx)
    if stp is None:
        return _skip(sid, name, "No liquid ATM straddle strike.")
    ce, pe = ctx.cache[(stp, "Call")], ctx.cache[(stp, "Put")]
    debit_lot = ((ce.best_offer_price or ce.ltp) + (pe.best_offer_price or pe.ltp)) * ctx.lot_size
    qty = _floor_lots(min(ctx.margin_rupees, ctx.max_loss_rupees), debit_lot, ctx.lot_size)
    if qty < ctx.lot_size:
        return _skip(sid, name, "Insufficient capital for one straddle lot.")
    legs = [
        TradeLeg("Call", "Buy", stp, qty, ce.best_offer_price or ce.ltp),
        TradeLeg("Put", "Buy", stp, qty, pe.best_offer_price or pe.ltp),
    ]
    max_loss = debit_lot * (qty // ctx.lot_size)
    return StrategyResult(
        sid, name, "ok", legs=legs, net_premium=-max_loss, max_loss=max_loss,
        risk_reward_ratio=f"{max_loss:.0f} : Unlimited",
        structure_modified=ctx.structure_modified,
        margin_key=_margin_key(legs, ctx.stock_code, ctx.expiry_display, ctx.exchange_code),
    )


def calc_short_straddle(ctx: EngineContext) -> StrategyResult:
    sid, name = "short_straddle", "Short Straddle"
    if ctx.halted:
        return _skip(sid, name, ctx.halt_reason or "Market halted")
    stp = _atm_with_liquidity(ctx)
    if stp is None:
        return _skip(sid, name, "No liquid ATM straddle strike.")
    ce, pe = ctx.cache[(stp, "Call")], ctx.cache[(stp, "Put")]
    prem_c, prem_p = ce.best_bid_price or ce.ltp, pe.best_bid_price or pe.ltp
    L = ctx.lot_size
    probe = [
        TradeLeg("Call", "Sell", stp, L, prem_c),
        TradeLeg("Put", "Sell", stp, L, prem_p),
    ]
    margin_res = ctx.processor.strategy_builder_margin(
        ctx.user_id, ctx.exchange_code,
        _legs_to_margin_input(probe, ctx.stock_code, ctx.exchange_code, ctx.expiry_display),
    )
    span = _parse_float((margin_res.get("Success") or {}).get("span_margin_required"))
    if span <= 0:
        return _skip(sid, name, "Margin calculator failed for short straddle.")
    cap = span + _elm_addon(ctx.spot, L, 2, ctx.provision_elm)
    qty = _floor_lots(ctx.margin_rupees, cap, L)
    if qty < L:
        return _skip(sid, name, "Insufficient margin for one lot.")
    legs = [
        TradeLeg("Call", "Sell", stp, qty, prem_c),
        TradeLeg("Put", "Sell", stp, qty, prem_p),
    ]
    max_profit = (prem_c + prem_p) * qty
    return StrategyResult(
        sid, name, "ok", legs=legs, net_premium=max_profit, max_loss=None,
        risk_reward_ratio=f"Unlimited : {max_profit:.0f}",
        structure_modified=ctx.structure_modified,
        margin_key=_margin_key(legs, ctx.stock_code, ctx.expiry_display, ctx.exchange_code),
    )


def calc_short_strangle(ctx: EngineContext) -> StrategyResult:
    sid, name = "short_strangle", "Short Strangle"
    if ctx.halted:
        return _skip(sid, name, ctx.halt_reason or "Market halted")
    stp_c = _first_liquid_above(ctx.liquid_ce_strikes, ctx.range_upper)
    stp_p = _first_liquid_below(ctx.liquid_pe_strikes, ctx.range_lower)
    if stp_c is None or stp_p is None:
        return _skip(sid, name, "Could not resolve liquid strangle strikes.")
    ce, pe = ctx.cache[(stp_c, "Call")], ctx.cache[(stp_p, "Put")]
    prem_c, prem_p = ce.best_bid_price or ce.ltp, pe.best_bid_price or pe.ltp
    L = ctx.lot_size
    probe = [
        TradeLeg("Call", "Sell", stp_c, L, prem_c),
        TradeLeg("Put", "Sell", stp_p, L, prem_p),
    ]
    margin_res = ctx.processor.strategy_builder_margin(
        ctx.user_id, ctx.exchange_code,
        _legs_to_margin_input(probe, ctx.stock_code, ctx.exchange_code, ctx.expiry_display),
    )
    span = _parse_float((margin_res.get("Success") or {}).get("span_margin_required"))
    if span <= 0:
        return _skip(sid, name, "Margin calculator failed for short strangle.")
    cap = span + _elm_addon(ctx.spot, L, 2, ctx.provision_elm)
    qty = _floor_lots(ctx.margin_rupees, cap, L)
    if qty < L:
        return _skip(sid, name, "Insufficient margin for one lot.")
    legs = [
        TradeLeg("Call", "Sell", stp_c, qty, prem_c),
        TradeLeg("Put", "Sell", stp_p, qty, prem_p),
    ]
    max_profit = (prem_c + prem_p) * qty
    return StrategyResult(
        sid, name, "ok", legs=legs, net_premium=max_profit, max_loss=None,
        risk_reward_ratio=f"Unlimited : {max_profit:.0f}",
        structure_modified=ctx.structure_modified,
        margin_key=_margin_key(legs, ctx.stock_code, ctx.expiry_display, ctx.exchange_code),
    )


def calc_long_call_butterfly(ctx: EngineContext) -> StrategyResult:
    sid, name = "long_call_butterfly", "Long Call Butterfly"
    if ctx.halted:
        return _skip(sid, name, ctx.halt_reason or "Market halted")
    mid = (ctx.range_lower + ctx.range_upper) / 2
    stp_m = min(ctx.liquid_ce_strikes, key=lambda s: abs(s - mid), default=None)
    if stp_m is None:
        return _skip(sid, name, "No liquid center strike for butterfly.")
    stp_l = _first_liquid_below(ctx.liquid_ce_strikes, ctx.range_lower) or _nearest_liquid_le(ctx.liquid_ce_strikes, ctx.range_lower)
    stp_h = _first_liquid_above(ctx.liquid_ce_strikes, ctx.range_upper) or _nearest_liquid_ge(ctx.liquid_ce_strikes, ctx.range_upper)
    if stp_l is None or stp_h is None or not (stp_l < stp_m < stp_h):
        return _skip(sid, name, "Could not resolve butterfly wing strikes.")
    ql, qm, qh = ctx.cache[(stp_l, "Call")], ctx.cache[(stp_m, "Call")], ctx.cache[(stp_h, "Call")]
    net_per = (ql.best_offer_price or ql.ltp) + (qh.best_offer_price or qh.ltp) - 2 * (qm.best_bid_price or qm.ltp)
    L = ctx.lot_size
    left_w = stp_m - stp_l
    right_w = stp_h - stp_m
    extra_risk = max(0, right_w - left_w)
    max_loss_lot = net_per * L + extra_risk * L
    if max_loss_lot <= 0:
        return _skip(sid, name, "Invalid butterfly debit.")
    qty_m = _floor_lots(ctx.margin_rupees, net_per * L, L)
    qty_l = _floor_lots(ctx.max_loss_rupees, max_loss_lot, L)
    qty = min(qty_m, qty_l) if qty_m and qty_l else 0
    if qty < L:
        return _skip(sid, name, "Insufficient risk appetite for one lot.")
    short_qty = 2 * (qty // L) * L
    legs = [
        TradeLeg("Call", "Buy", stp_l, qty, ql.best_offer_price or ql.ltp),
        TradeLeg("Call", "Sell", stp_m, short_qty, qm.best_bid_price or qm.ltp),
        TradeLeg("Call", "Buy", stp_h, qty, qh.best_offer_price or qh.ltp),
    ]
    max_loss = net_per * qty + extra_risk * (qty // L) * L
    max_profit = (left_w - net_per) * qty
    return StrategyResult(
        sid, name, "ok", legs=legs, net_premium=-(net_per * qty), max_loss=max_loss,
        risk_reward_ratio=f"{max_loss:.0f} : {max_profit:.0f}",
        structure_modified=ctx.structure_modified,
        margin_key=_margin_key(legs, ctx.stock_code, ctx.expiry_display, ctx.exchange_code),
    )


def _iron_wings(
    ctx: EngineContext,
    short_put: int,
    short_call: int,
    symmetric: bool,
) -> tuple[int, int, float, float] | None:
    """Return (long_put, long_call, credit_per_unit, max_loss_per_unit)."""
    L = ctx.lot_size
    qty_margin = _floor_lots(ctx.margin_rupees, ctx.margin_rupees / 4, L) or L
    steps = sorted({s - short_put for s in ctx.liquid_pe_strikes if s < short_put} | {s - short_call for s in ctx.liquid_ce_strikes if s > short_call})
    steps = [abs(x) for x in steps if x != 0]
    if not steps:
        steps = [ctx.strike_step]
    for w in sorted(set(steps), reverse=True):
        lp = short_put - w
        lc = short_call + w
        if lp not in ctx.liquid_pe_strikes or lc not in ctx.liquid_ce_strikes:
            continue
        sp, sc = ctx.cache[(short_put, "Put")], ctx.cache[(short_call, "Call")]
        lpq, lcq = ctx.cache[(lp, "Put")], ctx.cache[(lc, "Call")]
        credit = (sp.best_bid_price or sp.ltp) + (sc.best_bid_price or sc.ltp) - (lpq.best_offer_price or lpq.ltp) - (lcq.best_offer_price or lcq.ltp)
        max_loss_u = w - credit
        if max_loss_u <= 0:
            continue
        if max_loss_u * qty_margin <= ctx.max_loss_rupees:
            return lp, lc, credit, max_loss_u
    w = min(steps)
    lp, lc = short_put - w, short_call + w
    if lp in ctx.liquid_pe_strikes and lc in ctx.liquid_ce_strikes:
        sp, sc = ctx.cache[(short_put, "Put")], ctx.cache[(short_call, "Call")]
        lpq, lcq = ctx.cache[(lp, "Put")], ctx.cache[(lc, "Call")]
        credit = (sp.best_bid_price or sp.ltp) + (sc.best_bid_price or sc.ltp) - (lpq.best_offer_price or lpq.ltp) - (lcq.best_offer_price or lcq.ltp)
        max_loss_u = w - credit
        return lp, lc, credit, max_loss_u
    return None


def calc_iron_condor(ctx: EngineContext) -> StrategyResult:
    sid, name = "iron_condor", "Iron Condor"
    if ctx.halted:
        return _skip(sid, name, ctx.halt_reason or "Market halted")
    stp_sp = _first_liquid_below(ctx.liquid_pe_strikes, ctx.range_lower)
    stp_sc = _first_liquid_above(ctx.liquid_ce_strikes, ctx.range_upper)
    if stp_sp is None or stp_sc is None:
        return _skip(sid, name, "Could not resolve iron condor short strikes.")
    wings = _iron_wings(ctx, stp_sp, stp_sc, True)
    if not wings:
        return _skip(sid, name, "No symmetric wings within risk limits.")
    lp, lc, credit, max_loss_u = wings
    L = ctx.lot_size
    qty = _floor_lots(ctx.max_loss_rupees, max_loss_u * L, L)
    if qty < L:
        qty = L
    sp, sc, lpq, lcq = ctx.cache[(stp_sp, "Put")], ctx.cache[(stp_sc, "Call")], ctx.cache[(lp, "Put")], ctx.cache[(lc, "Call")]
    legs = [
        TradeLeg("Put", "Sell", stp_sp, qty, sp.best_bid_price or sp.ltp),
        TradeLeg("Put", "Buy", lp, qty, lpq.best_offer_price or lpq.ltp),
        TradeLeg("Call", "Sell", stp_sc, qty, sc.best_bid_price or sc.ltp),
        TradeLeg("Call", "Buy", lc, qty, lcq.best_offer_price or lcq.ltp),
    ]
    max_loss = max_loss_u * qty
    net_collected = credit * qty
    return StrategyResult(
        sid, name, "ok", legs=legs, net_premium=net_collected, max_loss=max_loss,
        risk_reward_ratio=f"{max_loss:.0f} : {net_collected:.0f}",
        structure_modified=ctx.structure_modified,
        margin_key=_margin_key(legs, ctx.stock_code, ctx.expiry_display, ctx.exchange_code),
    )


def calc_iron_butterfly(ctx: EngineContext) -> StrategyResult:
    sid, name = "iron_butterfly", "Iron Butterfly"
    if ctx.halted:
        return _skip(sid, name, ctx.halt_reason or "Market halted")
    stp = _atm_with_liquidity(ctx)
    if stp is None:
        return _skip(sid, name, "No liquid ATM for iron butterfly.")
    wings = _iron_wings(ctx, stp, stp, True)
    if not wings:
        return _skip(sid, name, "No symmetric wings within risk limits.")
    lp, lc, credit, max_loss_u = wings
    L = ctx.lot_size
    qty = _floor_lots(ctx.max_loss_rupees, max_loss_u * L, L)
    if qty < L:
        qty = L
    ce, pe, lpq, lcq = ctx.cache[(stp, "Call")], ctx.cache[(stp, "Put")], ctx.cache[(lp, "Put")], ctx.cache[(lc, "Call")]
    legs = [
        TradeLeg("Put", "Sell", stp, qty, pe.best_bid_price or pe.ltp),
        TradeLeg("Put", "Buy", lp, qty, lpq.best_offer_price or lpq.ltp),
        TradeLeg("Call", "Sell", stp, qty, ce.best_bid_price or ce.ltp),
        TradeLeg("Call", "Buy", lc, qty, lcq.best_offer_price or lcq.ltp),
    ]
    max_loss = max_loss_u * qty
    net_collected = credit * qty
    return StrategyResult(
        sid, name, "ok", legs=legs, net_premium=net_collected, max_loss=max_loss,
        risk_reward_ratio=f"{max_loss:.0f} : {net_collected:.0f}",
        structure_modified=ctx.structure_modified,
        margin_key=_margin_key(legs, ctx.stock_code, ctx.expiry_display, ctx.exchange_code),
    )


CALCULATORS: list[Callable[[EngineContext], StrategyResult]] = [
    calc_naked_ce_short,
    calc_naked_pe_short,
    calc_bull_call_spread,
    calc_bear_put_spread,
    calc_bear_call_spread,
    calc_bull_put_spread,
    calc_long_straddle,
    calc_short_straddle,
    calc_short_strangle,
    calc_long_call_butterfly,
    calc_iron_condor,
    calc_iron_butterfly,
]


def _attach_margins_and_returns(
    processor: processor,
    user_id: str,
    exchange_code: str,
    stock_code: str,
    expiry_display: str,
    results: list[StrategyResult],
) -> None:
    """Batch margin_calculator: one call per unique leg structure."""
    unique: dict[tuple, list[TradeLeg]] = {}
    for r in results:
        if r.status != "ok" or not r.legs:
            continue
        key = _margin_key(r.legs, stock_code, expiry_display, exchange_code)
        r.margin_key = key
        unique.setdefault(key, r.legs)

    span_by_key: dict[tuple, float] = {}
    for key, legs in unique.items():
        res = processor.strategy_builder_margin(
            user_id,
            exchange_code,
            _legs_to_margin_input(legs, stock_code, exchange_code, expiry_display),
        )
        span = _parse_float((res.get("Success") or {}).get("span_margin_required"))
        span_by_key[key] = span

    dte = _days_to_expiry(expiry_display)
    for r in results:
        if r.status != "ok" or r.margin_key is None:
            continue
        span = span_by_key.get(r.margin_key, 0.0)
        r.span_margin = span if span > 0 else None
        if r.net_premium and r.net_premium > 0 and span > 0:
            r.annualized_return_pct = round(
                _annualized_carry_percent_on_span(r.net_premium, dte, span), 2
            )


def run_propose_trades(
    processor: processor,
    user_id: str,
    *,
    exchange_code: str,
    stock_code: str,
    expiry_date: str,
    range_lower: float,
    range_upper: float,
    margin_lacs: float,
    max_loss_lacs: float,
    provision_elm: bool,
) -> dict[str, Any]:
    if range_lower >= range_upper:
        return {"Status": 400, "Error": "range_lower must be less than range_upper.", "Success": None}

    expiry_display = _normalize_expiry_display(expiry_date)
    lot_size = processor.fetch_lot_size(stock_code, expiry_display, exchange_code=exchange_code)
    if not lot_size or lot_size <= 0:
        return {"Status": 400, "Error": "Could not resolve lot size from scrip master.", "Success": None}

    strikes = processor.list_option_strikes(stock_code, expiry_display, exchange_code=exchange_code)
    if not strikes:
        return {"Status": 400, "Error": "No strikes in scrip master for this expiry.", "Success": None}

    step = processor.strike_interval(strikes)
    mid = (range_lower + range_upper) / 2

    ctx = EngineContext(
        processor=processor,
        user_id=user_id,
        stock_code=stock_code.strip(),
        exchange_code=exchange_code,
        expiry_display=expiry_display,
        range_lower=range_lower,
        range_upper=range_upper,
        margin_rupees=margin_lacs * 100_000,
        max_loss_rupees=max_loss_lacs * 100_000,
        provision_elm=provision_elm,
        lot_size=int(lot_size),
        strikes=strikes,
        strike_step=step,
        spot=mid,
        atm_strike=min(strikes, key=lambda s: abs(s - mid)),
    )

    _build_liquidity_cache(ctx)

    if ctx.halted:
        return {
            "Status": 400,
            "Error": ctx.halt_reason or "Insufficient market depth.",
            "Success": None,
        }

    results = [calc(ctx) for calc in CALCULATORS]
    _attach_margins_and_returns(
        processor, user_id, exchange_code, ctx.stock_code, expiry_display, results
    )

    atm_iv = _compute_atm_iv(ctx)
    trades_out = []
    for r in results:
        trades_out.append(
            {
                "strategy_id": r.strategy_id,
                "strategy_name": r.strategy_name,
                "status": r.status,
                "skip_reason": r.skip_reason,
                "structure_modified": r.structure_modified or ctx.structure_modified,
                "net_premium": r.net_premium,
                "max_loss": r.max_loss,
                "annualized_return_pct": r.annualized_return_pct,
                "risk_reward_ratio": r.risk_reward_ratio,
                "span_margin": getattr(r, "span_margin", None),
                "legs": [leg.to_out(ctx.cache) for leg in r.legs],
            }
        )

    return {
        "Status": 200,
        "Error": None,
        "Success": {
            "spot_price": round(ctx.spot, 2),
            "lot_size": ctx.lot_size,
            "expiry_display": expiry_display,
            "atm_iv": atm_iv,
            "structure_modified": ctx.structure_modified,
            "trades": trades_out,
        },
    }
