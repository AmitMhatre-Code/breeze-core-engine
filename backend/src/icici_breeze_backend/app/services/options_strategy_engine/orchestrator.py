"""Strategy engine orchestration and delivery."""
from __future__ import annotations

import inspect
import time
from typing import Any

from icici_breeze_backend.audit.strategy_builder_audit import StrategyBuilderAuditSession, quote_row_to_audit
from icici_breeze_backend.audit.user_explainability import build_user_explainability_report
from icici_breeze_backend.app.services.processor import (
    _annualized_carry_percent_on_span,
    _days_to_expiry,
    processor,
)
from icici_breeze_backend.app.services.options_strategy_engine.build_progress import BuildProgress
from icici_breeze_backend.app.services.options_strategy_engine.greeks import compute_atm_iv, enrich_greeks
from icici_breeze_backend.app.services.options_strategy_engine.helpers import (
    elm_for_legs,
    legs_to_margin_input,
    margin_key,
    normalize_expiry_display,
)
from icici_breeze_backend.app.services.options_strategy_engine.budget_resize import resize_results_to_budgets
from icici_breeze_backend.app.services.options_strategy_engine.strategies.directional._common import (
    refresh_directional_tile_metrics,
)
from icici_breeze_backend.app.services.options_strategy_engine.margin_async_fetch import (
    MarginFetchRequest,
    fetch_margins_concurrent,
)
from icici_breeze_backend.app.services.options_strategy_engine.registry import CATEGORY_CALCULATORS
from icici_breeze_backend.app.services.options_strategy_engine.compliance_split import (
    split_and_segregate_results,
)
from icici_breeze_backend.app.services.options_strategy_engine.types import (
    STRATEGY_CATALOG,
    EngineContext,
    RiskRewardProfile,
    StrategyCategory,
    StrategyResult,
    TradeLeg,
)
from icici_breeze_backend.app.services.options_strategy_engine.universe import (
    build_bulk_chain_cache,
    finalize_liquidity_cache,
)
from icici_breeze_backend.audit.strategy_evaluation_audit import AuditDetailLevel
from icici_breeze_backend.app.services.options_strategy_engine.audit_helpers import (
    StrategyTiming,
    begin_strategy_audit,
    end_strategy_audit,
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


def _audit_status_from_result(status: str) -> str:
    return "success" if status == "ok" else status


async def attach_margins_and_returns(
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

    margin_requests: list[MarginFetchRequest] = []
    for key, legs in unique.items():
        margin_input = legs_to_margin_input(legs, stock_code, exchange_code, expiry_display)
        margin_requests.append(
            MarginFetchRequest(
                cache_key=key,
                margin_input=margin_input,
                strategy_id=strategy_by_key.get(key, ""),
                phase=None,
                audit_rationale="Batch SPAN margin for unique proposed leg structure.",
            )
        )

    if margin_requests and ctx.progress is not None:
        ctx.progress.add_units(
            len(margin_requests),
            phase="margins",
            message=f"Calculating margins (0/{len(margin_requests)})…",
        )

    span_by_key = await fetch_margins_concurrent(
        proc,
        user_id,
        exchange_code,
        margin_requests,
        audit=audit,
        progress=ctx.progress,
    )

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


def _result_to_trade_dict(r: StrategyResult, ctx: EngineContext) -> dict[str, Any]:
    hero = None
    if r.hero_metric is not None:
        hero = {"label": r.hero_metric.label, "value": r.hero_metric.value}
    secondary = [{"label": m.label, "value": m.value} for m in (r.secondary_metrics or [])]
    return {
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
        "variant_rank": r.variant_rank,
        "engine_score": r.engine_score,
        "ranking_summary": r.ranking_summary,
        "score_breakdown": r.score_breakdown,
        "conviction_profile": r.conviction_profile,
        "hero_metric": hero,
        "secondary_metrics": secondary,
        "badges": r.badges or [],
        "compliance": r.compliance,
        "constraint_violations": list(r.constraint_violations or []),
    }


async def run_propose_trades(
    proc: processor,
    user_id: str,
    *,
    exchange_code: str,
    stock_code: str,
    expiry_date: str,
    margin_lacs: float,
    max_loss_lacs: float | None = None,
    allow_infinite_loss: bool = False,
    min_pop_pct: float = 65.0,
    min_ann_return_pct: float = 5.0,
    provision_elm: bool,
    strategy_category: StrategyCategory,
    risk_reward_profile: RiskRewardProfile | None = None,
    request_id: str | None = None,
    enable_audit: bool = True,
    audit_detail_level: AuditDetailLevel = "summary",
    progress: BuildProgress | None = None,
) -> dict[str, Any]:
    started_at = time.perf_counter()
    if progress is not None:
        progress.mark_running()

    min_pop_pct = min(99.0, max(1.0, min_pop_pct))
    min_ann_return_pct = min(100.0, max(0.0, min_ann_return_pct))
    if strategy_category not in CATEGORY_CALCULATORS:
        return {"Status": 400, "Error": f"Unknown strategy category: {strategy_category}", "Success": None}

    audit: StrategyBuilderAuditSession | None = None
    calculators = CATEGORY_CALCULATORS[strategy_category]
    strategy_ids = [calc.__name__.replace("calc_", "") for calc in calculators]
    strategy_names = dict(STRATEGY_CATALOG)
    if progress is not None:
        progress.register_base_units(strategy_count=len(calculators))
        progress.tick(phase="setup", message="Loading scrip master…")
    max_loss_rupees = None if allow_infinite_loss else (max_loss_lacs or 0) * 100_000
    if enable_audit:
        audit = StrategyBuilderAuditSession(
            user_id=user_id,
            request_id=request_id,
            audit_detail_level=audit_detail_level,
            strategy_ids=strategy_ids,
            request={
                "exchange_code": exchange_code,
                "stock_code": stock_code.strip(),
                "expiry_date": expiry_date.strip(),
                "margin_lacs": margin_lacs,
                "max_loss_lacs": max_loss_lacs,
                "allow_infinite_loss": allow_infinite_loss,
                "min_pop_pct": min_pop_pct,
                "min_ann_return_pct": min_ann_return_pct,
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

    ctx = EngineContext(
        processor=proc,
        user_id=user_id,
        stock_code=stock_code.strip(),
        exchange_code=exchange_code,
        expiry_display=expiry_display,
        margin_rupees=margin_lacs * 100_000,
        max_loss_rupees=max_loss_rupees,
        allow_infinite_loss=allow_infinite_loss,
        min_pop_pct=min_pop_pct,
        min_ann_return_pct=min_ann_return_pct,
        provision_elm=provision_elm,
        strategy_category=strategy_category,
        risk_reward_profile=risk_reward_profile or "moderate",
        lot_size=int(lot_size),
        strikes=[],
        strike_step=50,
        search_interval=50,
        spot=0.0,
        atm_strike=0.0,
        range_lower=0.0,
        range_upper=0.0,
        audit=audit,
        progress=progress,
    )

    build_bulk_chain_cache(ctx)
    if ctx.halted:
        return _fail(400, ctx.halt_reason or "Insufficient market depth.")

    strikes = ctx.strikes
    if audit:
        audit.record(
            "scrip_master",
            "chain_strikes",
            {
                "strike_count": len(strikes) if strikes else 0,
                "strike_min": strikes[0] if strikes else None,
                "strike_max": strikes[-1] if strikes else None,
            },
            rationale="Strikes derived from built option chain only.",
        )

    step = ctx.strike_step
    mid = float(strikes[len(strikes) // 2]) if strikes else 0.0
    search_step = ctx.search_interval
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
                "max_loss_rupees": max_loss_rupees,
                "allow_infinite_loss": allow_infinite_loss,
                "lot_size": int(lot_size),
            },
            rationale="Delta-anchored template parameters from chain strikes.",
        )

    ctx.atm_iv = compute_atm_iv(ctx)
    enrich_greeks(ctx)

    finalize_liquidity_cache(ctx)
    if ctx.halted:
        return _fail(400, ctx.halt_reason or "Insufficient market depth.")

    results: list[StrategyResult] = []
    eval_total = len(calculators)
    for idx, calc in enumerate(calculators, start=1):
        sid = calc.__name__.replace("calc_", "")
        display_name = strategy_names.get(sid, sid.replace("_", " ").title())
        if progress is not None:
            progress.tick(
                phase="evaluate",
                message=f"Evaluating {display_name} ({idx}/{eval_total})…",
            )
        if audit:
            audit.record("strategy_eval_start", calc.__name__, {"strategy_id": sid})
        collector = begin_strategy_audit(ctx, sid) if audit else None
        with StrategyTiming(ctx, sid):
            outcome = calc(ctx)
            res = await outcome if inspect.isawaitable(outcome) else outcome
        if isinstance(res, list):
            strategy_results = res
            results.extend(strategy_results)
            if collector:
                ok_results = [r for r in strategy_results if r.status == "ok"]
                if ok_results:
                    end_strategy_audit(ctx, collector, status="success")
                else:
                    skip_r = next((r.skip_reason for r in strategy_results if r.skip_reason), None)
                    end_strategy_audit(ctx, collector, status="skipped", skip_reason=skip_r)
        else:
            results.append(res)
            if collector:
                end_strategy_audit(
                    ctx,
                    collector,
                    status=_audit_status_from_result(res.status),
                    skip_reason=res.skip_reason,
                )

    recommended_results, relaxed_results = split_and_segregate_results(results, ctx)
    all_ok = [
        r for r in recommended_results + relaxed_results if r.status == "ok" and r.legs
    ]

    await resize_results_to_budgets(
        proc, user_id, exchange_code, ctx.stock_code, expiry_display, all_ok, ctx, audit
    )
    for res in recommended_results + relaxed_results:
        log_strategy_result(ctx, res)

    await attach_margins_and_returns(
        proc, user_id, exchange_code, ctx.stock_code, expiry_display, all_ok, ctx, audit
    )

    for res in all_ok:
        refresh_directional_tile_metrics(res)

    atm_iv = compute_atm_iv(ctx)
    if audit:
        audit.record_calculation(
            "ATM implied volatility",
            {"atm_strike": ctx.atm_strike, "spot": ctx.spot},
            {"atm_iv": atm_iv},
            rationale="Average IV from ATM call and put when both quoted.",
        )

    trades_out = [_result_to_trade_dict(r, ctx) for r in recommended_results]
    relaxed_trades_out = [
        _result_to_trade_dict(r, ctx)
        for r in relaxed_results
        if r.status == "ok" and r.legs
    ]
    all_trades_out = trades_out + relaxed_trades_out

    success_payload: dict[str, Any] = {
        "spot_price": round(ctx.spot, 2),
        "lot_size": ctx.lot_size,
        "expiry_display": expiry_display,
        "atm_iv": atm_iv,
        "structure_modified": ctx.structure_modified,
        "trades": trades_out,
        "relaxed_trades": relaxed_trades_out,
    }

    audit_summary = {
        "status": "ok",
        "spot_price": success_payload["spot_price"],
        "atm_strike": ctx.atm_strike,
        "atm_iv": atm_iv,
        "structure_modified": ctx.structure_modified,
        "liquid_ce_strikes": ctx.liquid_ce_strikes,
        "liquid_pe_strikes": ctx.liquid_pe_strikes,
        "icici_api_calls": audit.icici_api_call_stats if audit else {},
        "strategies_ok": [t["strategy_id"] for t in all_trades_out if t["status"] == "ok"],
        "strategies_relaxed": [t["strategy_id"] for t in relaxed_trades_out],
        "strategies_skipped": [
            {"strategy_id": t["strategy_id"], "skip_reason": t["skip_reason"]}
            for t in trades_out
            if t["status"] == "skipped"
        ],
        "generation_duration_seconds": round(time.perf_counter() - started_at, 1),
    }

    if progress is not None:
        progress.tick(phase="finalize", message="Finalizing results…")

    user_report = build_user_explainability_report(
        request=audit.request if audit else {
            "margin_lacs": margin_lacs,
            "max_loss_lacs": max_loss_lacs,
            "allow_infinite_loss": allow_infinite_loss,
            "min_pop_pct": min_pop_pct,
            "min_ann_return_pct": min_ann_return_pct,
            "strategy_category": strategy_category,
        },
        strategy_evaluations=audit.strategy_evaluations if audit else {},
        trades=all_trades_out,
        summary=audit_summary,
    )
    success_payload["user_report"] = user_report

    if audit:
        audit.set_temp_liquid_cache(temp_liquid_cache_snapshot(ctx))
        audit.finalize(audit_summary, user_explainability=user_report)
        success_payload["audit_session_id"] = audit.session_id

    return {
        "Status": 200,
        "Error": None,
        "Success": success_payload,
    }
