"""Strategy engine orchestration and delivery."""
from __future__ import annotations

from typing import Any

from icici_breeze_backend.audit.strategy_builder_audit import StrategyBuilderAuditSession, quote_row_to_audit
from icici_breeze_backend.app.services.processor import (
    _annualized_carry_percent_on_span,
    _days_to_expiry,
    processor,
)
from icici_breeze_backend.app.services.options_strategy_engine.greeks import compute_atm_iv, enrich_greeks
from icici_breeze_backend.app.services.options_strategy_engine.helpers import (
    elm_for_legs,
    legs_to_margin_input,
    margin_key,
    normalize_expiry_display,
    parse_float,
)
from icici_breeze_backend.app.services.options_strategy_engine.budget_resize import resize_results_to_budgets
from icici_breeze_backend.app.services.options_strategy_engine.registry import CATEGORY_CALCULATORS
from icici_breeze_backend.app.services.options_strategy_engine.types import (
    EngineContext,
    RiskRewardProfile,
    StrategyCategory,
    StrategyResult,
    TradeLeg,
)
from icici_breeze_backend.app.services.options_strategy_engine.icici_async_fetch import fetch_strike_pairs_async
from icici_breeze_backend.app.services.options_strategy_engine.strike_planner import plan_targeted_fetches
from icici_breeze_backend.app.services.options_strategy_engine.universe import (
    build_bulk_chain_cache,
    finalize_liquidity_cache,
)


def temp_liquid_cache_snapshot(ctx: EngineContext) -> dict[str, Any]:
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


def log_strategy_result(ctx: EngineContext, res: StrategyResult) -> None:
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


def attach_margins_and_returns(
    proc: processor,
    user_id: str,
    exchange_code: str,
    stock_code: str,
    expiry_display: str,
    results: list[StrategyResult],
    ctx: EngineContext,
    audit: StrategyBuilderAuditSession | None = None,
) -> None:
    unique: dict[tuple, list[TradeLeg]] = {}
    strategy_by_key: dict[tuple, str] = {}
    for r in results:
        if r.status != "ok" or not r.legs:
            continue
        key = margin_key(r.legs, stock_code, expiry_display, exchange_code)
        r.margin_key = key
        unique.setdefault(key, r.legs)
        strategy_by_key.setdefault(key, r.strategy_id)

    span_by_key: dict[tuple, float] = {}
    for key, legs in unique.items():
        margin_input = legs_to_margin_input(legs, stock_code, exchange_code, expiry_display)
        res = proc.strategy_builder_margin(
            user_id,
            exchange_code,
            margin_input,
            audit=audit,
            audit_context={
                "strategy_id": strategy_by_key.get(key),
                "legs": margin_input,
            },
        )
        span = parse_float((res.get("Success") or {}).get("span_margin_required"))
        span_by_key[key] = span

    dte = _days_to_expiry(expiry_display)
    for r in results:
        if r.status != "ok" or not r.legs:
            continue
        r.elm_requirement = elm_for_legs(ctx, r.legs)
        if r.margin_key is None:
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


async def run_propose_trades(
    proc: processor,
    user_id: str,
    *,
    exchange_code: str,
    stock_code: str,
    expiry_date: str,
    margin_lacs: float,
    max_loss_lacs: float,
    min_pop_pct: float = 65.0,
    provision_elm: bool,
    strategy_category: StrategyCategory,
    risk_reward_profile: RiskRewardProfile = "moderate",
    request_id: str | None = None,
    enable_audit: bool = True,
) -> dict[str, Any]:
    min_pop_pct = min(99.0, max(1.0, min_pop_pct))
    if strategy_category not in CATEGORY_CALCULATORS:
        return {"Status": 400, "Error": f"Unknown strategy category: {strategy_category}", "Success": None}

    audit: StrategyBuilderAuditSession | None = None
    if enable_audit:
        audit = StrategyBuilderAuditSession(
            user_id=user_id,
            request_id=request_id,
            request={
                "exchange_code": exchange_code,
                "stock_code": stock_code.strip(),
                "expiry_date": expiry_date.strip(),
                "margin_lacs": margin_lacs,
                "max_loss_lacs": max_loss_lacs,
                "min_pop_pct": min_pop_pct,
                "provision_elm": provision_elm,
                "strategy_category": strategy_category,
                "risk_reward_profile": risk_reward_profile,
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

    expiry_display = normalize_expiry_display(expiry_date)
    lot_size = proc.fetch_lot_size(stock_code, expiry_display, exchange_code=exchange_code)
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

    strikes = proc.list_option_strikes(stock_code, expiry_display, exchange_code=exchange_code)
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

    step = proc.strike_interval(strikes)
    mid = float(strikes[len(strikes) // 2])
    search_step = proc.search_interval(strikes, mid)
    if audit:
        audit.record_calculation(
            "Engine parameters",
            {
                "strike_mid": mid,
                "min_pop_pct": min_pop_pct,
                "strategy_category": strategy_category,
                "risk_reward_profile": risk_reward_profile,
            },
            {
                "expiry_display": expiry_display,
                "strike_step": step,
                "search_interval": search_step,
                "margin_rupees": margin_lacs * 100_000,
                "max_loss_rupees": max_loss_lacs * 100_000,
                "lot_size": int(lot_size),
            },
            rationale="Delta-anchored template parameters (no user strike range).",
        )

    ctx = EngineContext(
        processor=proc,
        user_id=user_id,
        stock_code=stock_code.strip(),
        exchange_code=exchange_code,
        expiry_display=expiry_display,
        margin_rupees=margin_lacs * 100_000,
        max_loss_rupees=max_loss_lacs * 100_000,
        min_pop_pct=min_pop_pct,
        provision_elm=provision_elm,
        strategy_category=strategy_category,
        risk_reward_profile=risk_reward_profile,
        lot_size=int(lot_size),
        strikes=strikes,
        strike_step=step,
        search_interval=search_step,
        spot=mid,
        atm_strike=min(strikes, key=lambda s: abs(s - mid)),
        audit=audit,
    )

    build_bulk_chain_cache(ctx)
    if ctx.halted:
        return _fail(400, ctx.halt_reason or "Insufficient market depth.")

    ctx.atm_iv = compute_atm_iv(ctx)
    enrich_greeks(ctx)

    to_fetch = plan_targeted_fetches(ctx)
    if to_fetch:
        ctx.cache.update(await fetch_strike_pairs_async(ctx, to_fetch))
        ctx.atm_iv = compute_atm_iv(ctx) or ctx.atm_iv
        enrich_greeks(ctx)

    finalize_liquidity_cache(ctx)
    if ctx.halted:
        return _fail(400, ctx.halt_reason or "Insufficient market depth.")

    results: list[StrategyResult] = []
    calculators = CATEGORY_CALCULATORS[strategy_category]
    for calc in calculators:
        if audit:
            sid = calc.__name__.replace("calc_", "")
            audit.record("strategy_eval_start", calc.__name__, {"strategy_id": sid})
        res = calc(ctx)
        results.append(res)

    resize_results_to_budgets(
        proc, user_id, exchange_code, ctx.stock_code, expiry_display, results, ctx, audit
    )
    for res in results:
        log_strategy_result(ctx, res)

    attach_margins_and_returns(
        proc, user_id, exchange_code, ctx.stock_code, expiry_display, results, ctx, audit
    )

    atm_iv = compute_atm_iv(ctx)
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
                "elm_requirement": getattr(r, "elm_requirement", None),
                "pop_pct": r.pop_pct,
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
        audit.set_temp_liquid_cache(temp_liquid_cache_snapshot(ctx))
        audit.finalize(
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
