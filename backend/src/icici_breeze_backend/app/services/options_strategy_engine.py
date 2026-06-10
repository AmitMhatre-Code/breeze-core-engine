"""Batch options strategy engine per docs/options-strategies.md."""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Optional

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.audit.strategy_builder_audit import (
    StrategyBuilderAuditSession,
    quote_row_to_audit,
)
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
    search_interval: int
    spot: float
    atm_strike: int
    cache: dict[tuple[int, Right], QuoteRow] = field(default_factory=dict)
    structure_modified: bool = False
    halted: bool = False
    halt_reason: str | None = None
    audit: StrategyBuilderAuditSession | None = None

    @property
    def liquid_ce_strikes(self) -> list[int]:
        return sorted(s for s in self.strikes if (s, "Call") in self.cache and self.cache[(s, "Call")].liquid)

    @property
    def liquid_pe_strikes(self) -> list[int]:
        return sorted(s for s in self.strikes if (s, "Put") in self.cache and self.cache[(s, "Put")].liquid)


def _audit_decision(
    ctx: EngineContext,
    decision: str,
    outcome: str,
    rationale: str,
    details: dict[str, Any] | None = None,
) -> None:
    if ctx.audit:
        ctx.audit.record_decision(decision, outcome, rationale=rationale, details=details)


def _audit_calc(
    ctx: EngineContext,
    name: str,
    inputs: dict[str, Any],
    outputs: dict[str, Any],
    *,
    formula: str | None = None,
    rationale: str | None = None,
) -> None:
    if ctx.audit:
        ctx.audit.record_calculation(name, inputs, outputs, formula=formula, rationale=rationale)


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
    audit: StrategyBuilderAuditSession | None = None,
    *,
    fetch_reason: str | None = None,
) -> dict[tuple[int, Right], QuoteRow]:
    cache: dict[tuple[int, Right], QuoteRow] = {}
    expiry_api = _expiry_display_to_api(expiry_display)
    if audit and pairs:
        audit.record(
            "quote_fetch_batch",
            f"Fetching {len(pairs)} option quote(s)",
            {
                "reason": fetch_reason,
                "pairs": [{"strike": s, "right": r} for s, r in sorted(pairs)],
            },
            rationale=fetch_reason or "Populate quote cache for strike selection.",
        )
    for strike, right in sorted(pairs):
        req = {
            "stock_code": stock_code,
            "exchange_code": exchange_code,
            "expiry_date": expiry_api,
            "product_type": cfg.OPTIONS,
            "right": right,
            "strike_price": str(strike),
        }
        quote = processor.get_quote(
            user_id,
            stock_code,
            expiry_api,
            cfg.OPTIONS,
            right,
            str(strike),
            exchange_code=exchange_code,
        )
        if audit:
            row = (quote.get("Success") or [None])[0]
            parsed = _quote_from_api(strike, right, row) if row else None
            audit.record_api_call(
                "get_quote",
                req,
                quote,
                rationale=fetch_reason or "Live option quote for liquidity and premium.",
            )
            if parsed:
                audit.record_strike(
                    strike,
                    right,
                    included=parsed.liquid,
                    reason="Two-sided depth (buy_qty>0 and sell_qty>0)" if parsed.liquid else "Missing bid or ask quantity",
                    quote=quote_row_to_audit(parsed),
                    context=fetch_reason,
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


def _strategy_boundary_strikes(
    all_strikes: list[int],
    range_lower: float,
    range_upper: float,
    spot: float,
    atm: int,
) -> set[int]:
    """Candidate strikes from scrip master for strategy boundary selection."""
    needed: set[int] = set()
    if atm in all_strikes:
        needed.add(atm)
    needed.add(min(all_strikes, key=lambda s: abs(s - spot)))
    needed.add(min(all_strikes, key=lambda s: abs(s - range_lower)))
    needed.add(min(all_strikes, key=lambda s: abs(s - range_upper)))
    ce_above = [s for s in all_strikes if s > range_upper]
    if ce_above:
        needed.add(ce_above[0])
    pe_below = [s for s in all_strikes if s < range_lower]
    if pe_below:
        needed.add(pe_below[-1])
    return needed


def _fetch_pairs_for_strikes(
    ctx: EngineContext,
    strikes: set[int] | list[int],
    *,
    fetch_reason: str | None = None,
) -> None:
    pairs: set[tuple[int, Right]] = set()
    for s in strikes:
        pairs.add((s, "Call"))
        pairs.add((s, "Put"))
    new_pairs = pairs - set(ctx.cache.keys())
    if new_pairs:
        ctx.cache.update(
            _fetch_quotes(
                ctx.processor,
                ctx.user_id,
                ctx.stock_code,
                ctx.exchange_code,
                ctx.expiry_display,
                new_pairs,
                ctx.audit,
                fetch_reason=fetch_reason,
            )
        )


def _ensure_liquid_above(
    ctx: EngineContext,
    level: float,
    right: Right,
    max_attempts: int = 3,
    *,
    purpose: str | None = None,
) -> int | None:
    liquid = ctx.liquid_ce_strikes if right == "Call" else ctx.liquid_pe_strikes
    hit = _first_liquid_above(liquid, level)
    if hit is not None:
        _audit_decision(
            ctx,
            f"Select liquid {right} above {level}",
            f"strike {hit}",
            f"First liquid {right} strictly above {level} from cached liquid set.",
            {"level": level, "liquid_pool": liquid, "purpose": purpose},
        )
        return hit
    candidates = [s for s in ctx.strikes if s > level]
    attempts = 0
    for s in candidates:
        q = ctx.cache.get((s, right))
        if q and q.liquid:
            _audit_decision(
                ctx,
                f"Select liquid {right} above {level}",
                f"strike {s}",
                f"Found liquid {right} at {s} in cache after scanning candidates.",
                {"level": level, "purpose": purpose},
            )
            return s
        _fetch_pairs_for_strikes(
            ctx,
            {s},
            fetch_reason=purpose or f"On-demand quote for {right} {s} (first liquid above {level})",
        )
        attempts += 1
        q = ctx.cache.get((s, right))
        if q and q.liquid:
            _audit_decision(
                ctx,
                f"Select liquid {right} above {level}",
                f"strike {s}",
                f"Fetched on-demand quote; {right} {s} became liquid.",
                {"level": level, "attempts": attempts, "purpose": purpose},
            )
            return s
        if ctx.audit:
            ctx.audit.record_strike(
                s,
                right,
                included=False,
                reason="Still illiquid after on-demand fetch",
                quote=quote_row_to_audit(q) if q else None,
                context=purpose,
            )
        if attempts >= max_attempts:
            break
    _audit_decision(
        ctx,
        f"Select liquid {right} above {level}",
        "none",
        f"No liquid {right} found within {max_attempts} fetch attempt(s) above {level}.",
        {"level": level, "candidates_tried": candidates[:max_attempts], "purpose": purpose},
    )
    return None


def _ensure_liquid_below(
    ctx: EngineContext,
    level: float,
    right: Right,
    max_attempts: int = 3,
    *,
    purpose: str | None = None,
) -> int | None:
    liquid = ctx.liquid_ce_strikes if right == "Call" else ctx.liquid_pe_strikes
    hit = _first_liquid_below(liquid, level)
    if hit is not None:
        _audit_decision(
            ctx,
            f"Select liquid {right} below {level}",
            f"strike {hit}",
            f"First liquid {right} strictly below {level} from cached liquid set.",
            {"level": level, "liquid_pool": liquid, "purpose": purpose},
        )
        return hit
    candidates = [s for s in reversed(ctx.strikes) if s < level]
    attempts = 0
    for s in candidates:
        q = ctx.cache.get((s, right))
        if q and q.liquid:
            _audit_decision(
                ctx,
                f"Select liquid {right} below {level}",
                f"strike {s}",
                f"Found liquid {right} at {s} in cache after scanning candidates.",
                {"level": level, "purpose": purpose},
            )
            return s
        _fetch_pairs_for_strikes(
            ctx,
            {s},
            fetch_reason=purpose or f"On-demand quote for {right} {s} (first liquid below {level})",
        )
        attempts += 1
        q = ctx.cache.get((s, right))
        if q and q.liquid:
            _audit_decision(
                ctx,
                f"Select liquid {right} below {level}",
                f"strike {s}",
                f"Fetched on-demand quote; {right} {s} became liquid.",
                {"level": level, "attempts": attempts, "purpose": purpose},
            )
            return s
        if ctx.audit:
            ctx.audit.record_strike(
                s,
                right,
                included=False,
                reason="Still illiquid after on-demand fetch",
                quote=quote_row_to_audit(q) if q else None,
                context=purpose,
            )
        if attempts >= max_attempts:
            break
    _audit_decision(
        ctx,
        f"Select liquid {right} below {level}",
        "none",
        f"No liquid {right} found within {max_attempts} fetch attempt(s) below {level}.",
        {"level": level, "candidates_tried": candidates[:max_attempts], "purpose": purpose},
    )
    return None


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
        if ctx.audit:
            ctx.audit.record("halt", ctx.halt_reason, {"phase": "liquidity_cache"})
        return

    mid = (ctx.range_lower + ctx.range_upper) / 2
    seed_strike = min(all_strikes, key=lambda s: abs(s - mid))
    seed_pairs = {(seed_strike, "Call"), (seed_strike, "Put")}
    if ctx.audit:
        ctx.audit.record(
            "liquidity_protocol",
            "Step A: seed quote at range midpoint",
            {
                "range_mid": mid,
                "seed_strike": seed_strike,
                "strike_count_master": len(all_strikes),
            },
            rationale="Seed spot/ATM from quote nearest to range midpoint (docs §4).",
        )
    ctx.cache.update(
        _fetch_quotes(
            ctx.processor,
            ctx.user_id,
            ctx.stock_code,
            ctx.exchange_code,
            ctx.expiry_display,
            seed_pairs,
            ctx.audit,
            fetch_reason="Liquidity protocol step A: seed at range midpoint",
        )
    )

    spot = ctx.spot
    for q in ctx.cache.values():
        if q.spot_price and q.spot_price > 0:
            spot = q.spot_price
            break
    if spot <= 0:
        spot = mid
    ctx.spot = spot
    ctx.atm_strike = _nearest_atm(all_strikes, spot)
    _audit_calc(
        ctx,
        "Spot and ATM resolution",
        {"range_mid_fallback": mid, "seed_strike": seed_strike},
        {"spot": spot, "atm_strike": ctx.atm_strike},
        rationale="Spot from quote payload when available, else range midpoint.",
    )

    def window_strikes(pad: int) -> list[int]:
        ws = _strike_window(
            all_strikes,
            ctx.range_lower,
            ctx.range_upper,
            ctx.atm_strike,
            ctx.search_interval,
            pad,
        )
        lo = ctx.range_lower - pad * ctx.search_interval
        hi = ctx.range_upper + pad * ctx.search_interval
        if ctx.audit:
            ctx.audit.record_calculation(
                f"Strike window (pad={pad})",
                {
                    "range_lower": ctx.range_lower,
                    "range_upper": ctx.range_upper,
                    "atm_strike": ctx.atm_strike,
                    "search_interval": ctx.search_interval,
                    "pad_intervals": pad,
                },
                {"window_lo": lo, "window_hi": hi, "strikes": ws},
                formula="[range_lower - pad*step, range_upper + pad*step] ∩ scrip master (+ ATM if outside)",
            )
        return ws

    def fetch_window(pad: int) -> None:
        _fetch_pairs_for_strikes(
            ctx,
            window_strikes(pad),
            fetch_reason=f"Liquidity protocol: fetch padded window (pad={pad})",
        )

    fetch_window(3)
    boundaries = _strategy_boundary_strikes(
        all_strikes, ctx.range_lower, ctx.range_upper, ctx.spot, ctx.atm_strike
    )
    if ctx.audit:
        ctx.audit.record(
            "liquidity_protocol",
            "Fetch strategy boundary strikes",
            {"boundary_strikes": sorted(boundaries)},
            rationale="Ensure first CE above range and first PE below range are quoted.",
        )
    _fetch_pairs_for_strikes(
        ctx,
        boundaries,
        fetch_reason="Strategy boundary strikes (first CE above / PE below range)",
    )

    def _has_liquid(ws: list[int]) -> list[int]:
        out: list[int] = []
        for s in ws:
            ce = ctx.cache.get((s, "Call"))
            pe = ctx.cache.get((s, "Put"))
            if (ce and ce.liquid) or (pe and pe.liquid):
                out.append(s)
            elif ctx.audit:
                for right, q in (("Call", ce), ("Put", pe)):
                    if q and not q.liquid:
                        ctx.audit.record_strike(
                            s,
                            right,
                            included=False,
                            reason="Inside window but illiquid (missing bid or ask qty)",
                            quote=quote_row_to_audit(q),
                            context="liquidity_window_scan",
                        )
        return out

    liquid = _has_liquid(window_strikes(3))

    if not liquid:
        if ctx.audit:
            ctx.audit.record(
                "liquidity_protocol",
                "Expand window pad 3 → 6",
                {"liquid_strikes_pad3": liquid},
                rationale="No liquid strikes in pad=3 window; widen per docs §4 fallback.",
            )
        fetch_window(6)
        ctx.structure_modified = True
        liquid = _has_liquid(window_strikes(6))

    if not liquid:
        if ctx.audit:
            ctx.audit.record(
                "liquidity_protocol",
                "Step B: compress toward ATM",
                {},
                rationale="Still no liquid strikes in window; fetch rings around ATM.",
            )
        for pad in [1, 2, 3, 4, 5, 6]:
            near = [s for s in all_strikes if abs(s - ctx.atm_strike) <= pad * ctx.strike_step]
            pairs = {(s, r) for s in near for r in ("Call", "Put")}
            new_pairs = pairs - set(ctx.cache.keys())
            if new_pairs:
                ctx.cache.update(
                    _fetch_quotes(
                        ctx.processor,
                        ctx.user_id,
                        ctx.stock_code,
                        ctx.exchange_code,
                        ctx.expiry_display,
                        new_pairs,
                        ctx.audit,
                        fetch_reason=f"Liquidity protocol step B: ATM ring pad={pad}",
                    )
                )
            if ctx.liquid_ce_strikes or ctx.liquid_pe_strikes:
                ctx.structure_modified = True
                if ctx.audit:
                    ctx.audit.record(
                        "liquidity_protocol",
                        f"Step B succeeded at ATM ring pad={pad}",
                        {
                            "liquid_ce": ctx.liquid_ce_strikes,
                            "liquid_pe": ctx.liquid_pe_strikes,
                        },
                    )
                break

    if not ctx.liquid_ce_strikes and not ctx.liquid_pe_strikes:
        if ctx.audit:
            ctx.audit.record(
                "liquidity_protocol",
                "Step C: ATM straddle only",
                {"atm_strike": ctx.atm_strike},
                rationale="Last resort — quote ATM straddle for metrics only.",
            )
        pairs = {(ctx.atm_strike, "Call"), (ctx.atm_strike, "Put")}
        ctx.cache.update(
            _fetch_quotes(
                ctx.processor,
                ctx.user_id,
                ctx.stock_code,
                ctx.exchange_code,
                ctx.expiry_display,
                pairs,
                ctx.audit,
                fetch_reason="Liquidity protocol step C: ATM straddle",
            )
        )
        if not any(ctx.cache.get((ctx.atm_strike, r)) and ctx.cache[(ctx.atm_strike, r)].liquid for r in ("Call", "Put")):
            ctx.halted = True
            ctx.halt_reason = "Insufficient market depth: no liquid strikes found."
            if ctx.audit:
                ctx.audit.record("halt", ctx.halt_reason, {"phase": "liquidity_cache_step_c"})
            return
        ctx.structure_modified = True

    if ctx.audit:
        ctx.audit.record(
            "liquidity_cache_complete",
            "Quote cache ready for strategy evaluation",
            {
                "structure_modified": ctx.structure_modified,
                "liquid_ce_strikes": ctx.liquid_ce_strikes,
                "liquid_pe_strikes": ctx.liquid_pe_strikes,
                "cached_pairs": len(ctx.cache),
                "spot": ctx.spot,
                "atm_strike": ctx.atm_strike,
            },
            rationale="Liquid strike pools drive all downstream strategy strike picks.",
        )


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
    stp = _ensure_liquid_above(ctx, ctx.range_upper, "Call")
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
    stp = _ensure_liquid_below(ctx, ctx.range_lower, "Put")
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
        _audit_decision(
            ctx,
            "Bull call spread strikes",
            "skip",
            "Need liquid long CE at/above spot and short CE at/above range upper with long < short.",
            {"stp_l": stp_l, "stp_h": stp_h, "spot": ctx.spot, "range_upper": ctx.range_upper},
        )
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
    _audit_calc(
        ctx,
        "Bull call spread sizing",
        {
            "long_strike": stp_l,
            "short_strike": stp_h,
            "net_debit_per_unit": net_per,
            "margin_rupees": ctx.margin_rupees,
            "max_loss_rupees": ctx.max_loss_rupees,
        },
        {"qty_from_margin": qty_m, "qty_from_max_loss": qty_l, "final_qty": qty},
        formula="qty = min(floor(margin/debit_lot), floor(max_loss/max_loss_lot)) * L",
        rationale="Long CE nearest liquid >= spot; short CE nearest liquid >= range upper.",
    )
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
    *,
    strategy_id: str | None = None,
) -> tuple[int, float, float] | None:
    """Return (wing_stp, net_credit_per_unit, max_loss_per_unit) or None."""
    L = ctx.lot_size
    qs = ctx.cache.get((short_stp, short_right))
    if not qs:
        if ctx.audit:
            ctx.audit.record_strategy_phase(
                strategy_id or "credit_spread",
                strategy_id or "credit_spread",
                "wing_search_failed",
                reason="Short leg quote missing from cache",
                short_strike=short_stp,
                right=short_right,
            )
        return None
    candidates = [s for s in wing_strikes if (s > short_stp if wing_is_higher else s < short_stp)]
    if wing_is_higher:
        candidates.sort()
    else:
        candidates.sort(reverse=True)
    qty_margin = _floor_lots(ctx.margin_rupees, max(qs.best_bid_price, 0.05) * L * 2, L) or L
    if ctx.audit:
        ctx.audit.record(
            "wing_search",
            f"Credit spread wing search ({short_right} short @ {short_stp})",
            {
                "strategy_id": strategy_id,
                "short_strike": short_stp,
                "right": short_right,
                "candidates": candidates,
                "qty_margin": qty_margin,
                "max_loss_rupees": ctx.max_loss_rupees,
            },
            rationale="Walk liquid strikes away from short; pick widest wing within max-loss budget.",
        )
    for wing in candidates:
        qw = ctx.cache.get((wing, short_right))
        if not qw:
            if ctx.audit:
                ctx.audit.record_strike(
                    wing,
                    short_right,
                    included=False,
                    reason="Candidate wing not in quote cache",
                    context=strategy_id,
                )
            continue
        credit = (qs.best_bid_price or qs.ltp) - (qw.best_offer_price or qw.ltp)
        width = abs(wing - short_stp)
        max_loss_u = width - credit
        risk_total = max_loss_u * qty_margin
        accepted = max_loss_u > 0 and risk_total <= ctx.max_loss_rupees
        if ctx.audit:
            ctx.audit.record_calculation(
                f"Wing candidate {short_right} {wing}",
                {
                    "short_bid": qs.best_bid_price or qs.ltp,
                    "wing_ask": qw.best_offer_price or qw.ltp,
                    "width": width,
                    "qty_margin": qty_margin,
                },
                {
                    "credit_per_unit": credit,
                    "max_loss_per_unit": max_loss_u,
                    "risk_at_qty_margin": risk_total,
                    "accepted": accepted,
                },
                formula="credit = short_bid - wing_ask; max_loss_u = width - credit",
                rationale="Rejected if max_loss_u<=0 or risk exceeds max_loss_rupees.",
            )
        if max_loss_u <= 0:
            continue
        if max_loss_u * qty_margin <= ctx.max_loss_rupees:
            if ctx.audit:
                ctx.audit.record_decision(
                    "Credit spread wing",
                    f"select {wing}",
                    f"Widest liquid wing within risk budget ({risk_total:.2f} <= {ctx.max_loss_rupees:.2f}).",
                    {"wing": wing, "credit": credit, "max_loss_u": max_loss_u},
                )
            return wing, credit, max_loss_u
    if candidates:
        wing = candidates[0]
        qw = ctx.cache.get((wing, short_right))
        if qw:
            credit = (qs.best_bid_price or qs.ltp) - (qw.best_offer_price or qw.ltp)
            width = abs(wing - short_stp)
            max_loss_u = width - credit
            if ctx.audit:
                ctx.audit.record_decision(
                    "Credit spread wing",
                    f"fallback narrowest {wing}",
                    "No wing met risk budget; using narrowest liquid wing with downscaled qty.",
                    {"credit": credit, "max_loss_u": max_loss_u},
                )
            return wing, credit, max_loss_u
    if ctx.audit:
        ctx.audit.record_decision(
            "Credit spread wing",
            "none",
            f"No liquid {short_right} wing candidates above/below short strike {short_stp}.",
            {"candidates": candidates, "liquid_pool": wing_strikes},
        )
    return None


def calc_bear_call_spread(ctx: EngineContext) -> StrategyResult:
    sid, name = "bear_call_spread", "Bear Call Spread"
    if ctx.halted:
        return _skip(sid, name, ctx.halt_reason or "Market halted")
    stp_s = _ensure_liquid_above(
        ctx, ctx.range_upper, "Call", purpose="bear_call_spread: short CE above range upper"
    )
    if stp_s is None:
        return _skip(sid, name, "No liquid short CE above range.")
    wing = _credit_spread_wing(ctx, stp_s, "Call", ctx.liquid_ce_strikes, True, strategy_id=sid)
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
    stp_s = _ensure_liquid_below(
        ctx, ctx.range_lower, "Put", purpose="bull_put_spread: short PE below range lower"
    )
    if stp_s is None:
        return _skip(sid, name, "No liquid short PE below range.")
    wing = _credit_spread_wing(ctx, stp_s, "Put", ctx.liquid_pe_strikes, False, strategy_id=sid)
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
    stp_c = _ensure_liquid_above(ctx, ctx.range_upper, "Call")
    stp_p = _ensure_liquid_below(ctx, ctx.range_lower, "Put")
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
    stp_l = (
        _ensure_liquid_below(ctx, ctx.range_lower, "Call")
        or _nearest_liquid_le(ctx.liquid_ce_strikes, ctx.range_lower)
    )
    stp_h = (
        _ensure_liquid_above(ctx, ctx.range_upper, "Call")
        or _nearest_liquid_ge(ctx.liquid_ce_strikes, ctx.range_upper)
    )
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
    *,
    strategy_id: str | None = None,
) -> tuple[int, int, float, float] | None:
    """Return (long_put, long_call, credit_per_unit, max_loss_per_unit)."""
    L = ctx.lot_size
    qty_margin = _floor_lots(ctx.margin_rupees, ctx.margin_rupees / 4, L) or L
    steps = sorted({s - short_put for s in ctx.liquid_pe_strikes if s < short_put} | {s - short_call for s in ctx.liquid_ce_strikes if s > short_call})
    steps = [abs(x) for x in steps if x != 0]
    if not steps:
        steps = [ctx.strike_step]
    if ctx.audit:
        ctx.audit.record(
            "wing_search",
            f"Iron symmetric wing search (PE short {short_put}, CE short {short_call})",
            {
                "strategy_id": strategy_id,
                "short_put": short_put,
                "short_call": short_call,
                "wing_width_candidates": sorted(set(steps), reverse=True),
                "qty_margin": qty_margin,
                "max_loss_rupees": ctx.max_loss_rupees,
            },
            rationale="Both long PE and long CE must be liquid at the same wing width W.",
        )
    for w in sorted(set(steps), reverse=True):
        lp = short_put - w
        lc = short_call + w
        lp_liq = lp in ctx.liquid_pe_strikes
        lc_liq = lc in ctx.liquid_ce_strikes
        if not lp_liq or not lc_liq:
            if ctx.audit:
                ctx.audit.record(
                    "iron_wing_rejected",
                    f"W={w}: LP {lp} / LC {lc}",
                    {
                        "wing_width": w,
                        "long_put": lp,
                        "long_call": lc,
                        "long_put_liquid": lp_liq,
                        "long_call_liquid": lc_liq,
                    },
                    rationale="Symmetric iron condor/butterfly requires BOTH wings liquid at same W.",
                )
            continue
        sp, sc = ctx.cache[(short_put, "Put")], ctx.cache[(short_call, "Call")]
        lpq, lcq = ctx.cache[(lp, "Put")], ctx.cache[(lc, "Call")]
        credit = (sp.best_bid_price or sp.ltp) + (sc.best_bid_price or sc.ltp) - (lpq.best_offer_price or lpq.ltp) - (lcq.best_offer_price or lcq.ltp)
        max_loss_u = w - credit
        risk_total = max_loss_u * qty_margin
        accepted = max_loss_u > 0 and risk_total <= ctx.max_loss_rupees
        if ctx.audit:
            ctx.audit.record_calculation(
                f"Iron wing W={w}",
                {
                    "short_put_bid": sp.best_bid_price or sp.ltp,
                    "short_call_bid": sc.best_bid_price or sc.ltp,
                    "long_put_ask": lpq.best_offer_price or lpq.ltp,
                    "long_call_ask": lcq.best_offer_price or lcq.ltp,
                    "qty_margin": qty_margin,
                },
                {
                    "long_put": lp,
                    "long_call": lc,
                    "credit_per_unit": credit,
                    "max_loss_per_unit": max_loss_u,
                    "risk_at_qty_margin": risk_total,
                    "accepted": accepted,
                },
                formula="credit = short_put_bid + short_call_bid - long_put_ask - long_call_ask; max_loss_u = W - credit",
            )
        if max_loss_u <= 0:
            continue
        if max_loss_u * qty_margin <= ctx.max_loss_rupees:
            if ctx.audit:
                ctx.audit.record_decision(
                    "Iron symmetric wing",
                    f"W={w} (LP {lp}, LC {lc})",
                    f"Widest symmetric wing within risk budget.",
                    {"credit": credit, "max_loss_u": max_loss_u},
                )
            return lp, lc, credit, max_loss_u
    w = min(steps)
    lp, lc = short_put - w, short_call + w
    if lp in ctx.liquid_pe_strikes and lc in ctx.liquid_ce_strikes:
        sp, sc = ctx.cache[(short_put, "Put")], ctx.cache[(short_call, "Call")]
        lpq, lcq = ctx.cache[(lp, "Put")], ctx.cache[(lc, "Call")]
        credit = (sp.best_bid_price or sp.ltp) + (sc.best_bid_price or sc.ltp) - (lpq.best_offer_price or lpq.ltp) - (lcq.best_offer_price or lcq.ltp)
        max_loss_u = w - credit
        if ctx.audit:
            ctx.audit.record_decision(
                "Iron symmetric wing",
                f"fallback W={w} (LP {lp}, LC {lc})",
                "No wing met risk budget; narrowest symmetric liquid pair with downscaled qty.",
                {"credit": credit, "max_loss_u": max_loss_u},
            )
        return lp, lc, credit, max_loss_u
    if ctx.audit:
        ctx.audit.record_decision(
            "Iron symmetric wing",
            "none",
            f"No symmetric liquid wing pair for PE short {short_put} / CE short {short_call}.",
            {
                "narrowest_w": w,
                "long_put": lp,
                "long_call": lc,
                "long_put_liquid": lp in ctx.liquid_pe_strikes,
                "long_call_liquid": lc in ctx.liquid_ce_strikes,
            },
        )
    return None


def calc_iron_condor(ctx: EngineContext) -> StrategyResult:
    sid, name = "iron_condor", "Iron Condor"
    if ctx.halted:
        return _skip(sid, name, ctx.halt_reason or "Market halted")
    stp_sp = _ensure_liquid_below(
        ctx, ctx.range_lower, "Put", purpose="iron_condor: short PE below range lower"
    )
    stp_sc = _ensure_liquid_above(
        ctx, ctx.range_upper, "Call", purpose="iron_condor: short CE above range upper"
    )
    if stp_sp is None or stp_sc is None:
        return _skip(sid, name, "Could not resolve iron condor short strikes.")
    wings = _iron_wings(ctx, stp_sp, stp_sc, True, strategy_id=sid)
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
    wings = _iron_wings(ctx, stp, stp, True, strategy_id=sid)
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


def _temp_liquid_cache_snapshot(ctx: EngineContext) -> dict[str, Any]:
    """In-memory quote set built for strategy evaluation (post-fetch, pre-response)."""
    options: list[dict[str, Any]] = []
    for (_strike, _right), q in sorted(ctx.cache.items()):
        options.append(quote_row_to_audit(q))
    return {
        "description": "Temporary liquid quote cache used by Strategy Builder (New) for strike selection.",
        "structure_modified": ctx.structure_modified,
        "spot": ctx.spot,
        "atm_strike": ctx.atm_strike,
        "liquid_ce_strikes": ctx.liquid_ce_strikes,
        "liquid_pe_strikes": ctx.liquid_pe_strikes,
        "cached_pair_count": len(ctx.cache),
        "liquid_pair_count": sum(1 for q in ctx.cache.values() if q.liquid),
        "options": options,
    }


def _log_strategy_result(ctx: EngineContext, res: StrategyResult) -> None:
    if not ctx.audit:
        return
    ctx.audit.record_strategy_phase(
        res.strategy_id,
        res.strategy_name,
        res.status,
        skip_reason=res.skip_reason,
        structure_modified=res.structure_modified,
        net_premium=res.net_premium,
        max_loss=res.max_loss,
        risk_reward_ratio=res.risk_reward_ratio,
        legs=[
            {
                "side": leg.side,
                "right": leg.right,
                "strike": leg.strike,
                "quantity": leg.quantity,
                "premium_per_unit": leg.premium_per_unit,
            }
            for leg in res.legs
        ],
    )


def _attach_margins_and_returns(
    processor: processor,
    user_id: str,
    exchange_code: str,
    stock_code: str,
    expiry_display: str,
    results: list[StrategyResult],
    audit: StrategyBuilderAuditSession | None = None,
) -> None:
    """Batch margin_calculator: one call per unique leg structure."""
    unique: dict[tuple, list[TradeLeg]] = {}
    strategy_by_key: dict[tuple, str] = {}
    for r in results:
        if r.status != "ok" or not r.legs:
            continue
        key = _margin_key(r.legs, stock_code, expiry_display, exchange_code)
        r.margin_key = key
        unique.setdefault(key, r.legs)
        strategy_by_key.setdefault(key, r.strategy_id)

    span_by_key: dict[tuple, float] = {}
    for key, legs in unique.items():
        margin_input = _legs_to_margin_input(legs, stock_code, exchange_code, expiry_display)
        res = processor.strategy_builder_margin(user_id, exchange_code, margin_input)
        if audit:
            audit.record_api_call(
                "strategy_builder_margin",
                {
                    "exchange_code": exchange_code,
                    "strategy_id": strategy_by_key.get(key),
                    "legs": margin_input,
                },
                res,
                rationale="Batch SPAN margin for unique proposed leg structure.",
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
            if audit:
                audit.record_calculation(
                    f"Annualized return ({r.strategy_id})",
                    {"net_premium": r.net_premium, "span_margin": span, "dte": dte},
                    {"annualized_return_pct": r.annualized_return_pct},
                    rationale="Carry return on SPAN for credit strategies.",
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
    request_id: str | None = None,
    enable_audit: bool = True,
) -> dict[str, Any]:
    audit: StrategyBuilderAuditSession | None = None
    if enable_audit:
        audit = StrategyBuilderAuditSession(
            user_id=user_id,
            request_id=request_id,
            request={
                "exchange_code": exchange_code,
                "stock_code": stock_code.strip(),
                "expiry_date": expiry_date.strip(),
                "range_lower": range_lower,
                "range_upper": range_upper,
                "margin_lacs": margin_lacs,
                "max_loss_lacs": max_loss_lacs,
                "provision_elm": provision_elm,
            },
        )
        audit.record(
            "session_start",
            "Strategy Builder (New) propose-trades",
            {"user_id": user_id, "request_id": request_id},
            rationale="Full decision audit for this build session.",
        )

    def _fail(status: int, error: str) -> dict[str, Any]:
        if audit:
            audit.record("session_error", error, {"status": status})
            audit.finalize({"status": "error", "error": error})
        return {"Status": status, "Error": error, "Success": None}

    if range_lower >= range_upper:
        return _fail(400, "range_lower must be less than range_upper.")

    expiry_display = _normalize_expiry_display(expiry_date)
    lot_size = processor.fetch_lot_size(stock_code, expiry_display, exchange_code=exchange_code)
    if audit:
        audit.record(
            "scrip_master",
            "fetch_lot_size",
            {
                "stock_code": stock_code,
                "expiry_display": expiry_display,
                "exchange_code": exchange_code,
                "lot_size": lot_size,
            },
            rationale="Lot size from scrip master drives quantity snapping.",
        )
    if not lot_size or lot_size <= 0:
        return _fail(400, "Could not resolve lot size from scrip master.")

    strikes = processor.list_option_strikes(stock_code, expiry_display, exchange_code=exchange_code)
    if audit:
        audit.record(
            "scrip_master",
            "list_option_strikes",
            {
                "strike_count": len(strikes) if strikes else 0,
                "strike_min": strikes[0] if strikes else None,
                "strike_max": strikes[-1] if strikes else None,
            },
            rationale="Available strikes bound all strategy construction.",
        )
    if not strikes:
        return _fail(400, "No strikes in scrip master for this expiry.")

    step = processor.strike_interval(strikes)
    mid = (range_lower + range_upper) / 2
    search_step = processor.search_interval(strikes, mid)
    if audit:
        audit.record_calculation(
            "Engine parameters",
            {"range_lower": range_lower, "range_upper": range_upper, "mid": mid},
            {
                "expiry_display": expiry_display,
                "strike_step": step,
                "search_interval": search_step,
                "margin_rupees": margin_lacs * 100_000,
                "max_loss_rupees": max_loss_lacs * 100_000,
                "lot_size": int(lot_size),
            },
            rationale="Strike step and search interval from scrip master spacing.",
        )

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
        search_interval=search_step,
        spot=mid,
        atm_strike=min(strikes, key=lambda s: abs(s - mid)),
        audit=audit,
    )

    _build_liquidity_cache(ctx)

    if ctx.halted:
        return _fail(400, ctx.halt_reason or "Insufficient market depth.")

    results: list[StrategyResult] = []
    for calc in CALCULATORS:
        if audit:
            sid = calc.__name__.replace("calc_", "")
            audit.record("strategy_eval_start", calc.__name__, {"strategy_id": sid})
        res = calc(ctx)
        _log_strategy_result(ctx, res)
        results.append(res)

    _attach_margins_and_returns(
        processor, user_id, exchange_code, ctx.stock_code, expiry_display, results, audit
    )

    atm_iv = _compute_atm_iv(ctx)
    if audit:
        audit.record_calculation(
            "ATM implied volatility",
            {"atm_strike": ctx.atm_strike, "spot": ctx.spot},
            {"atm_iv": atm_iv},
            rationale="Average IV from ATM call and put when both quoted.",
        )

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

    success_payload: dict[str, Any] = {
        "spot_price": round(ctx.spot, 2),
        "lot_size": ctx.lot_size,
        "expiry_display": expiry_display,
        "atm_iv": atm_iv,
        "structure_modified": ctx.structure_modified,
        "trades": trades_out,
    }

    if audit:
        audit.set_temp_liquid_cache(_temp_liquid_cache_snapshot(ctx))
        audit_path = audit.finalize(
            {
                "status": "ok",
                "spot_price": success_payload["spot_price"],
                "atm_strike": ctx.atm_strike,
                "atm_iv": atm_iv,
                "structure_modified": ctx.structure_modified,
                "liquid_ce_strikes": ctx.liquid_ce_strikes,
                "liquid_pe_strikes": ctx.liquid_pe_strikes,
                "icici_api_calls": audit.icici_api_call_stats,
                "strategies_ok": [t["strategy_id"] for t in trades_out if t["status"] == "ok"],
                "strategies_skipped": [
                    {"strategy_id": t["strategy_id"], "skip_reason": t["skip_reason"]}
                    for t in trades_out
                    if t["status"] == "skipped"
                ],
            }
        )
        success_payload["audit_session_id"] = audit.session_id

    return {
        "Status": 200,
        "Error": None,
        "Success": success_payload,
    }
