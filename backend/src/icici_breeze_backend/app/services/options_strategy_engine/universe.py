"""Data ingestion and liquidity filtering (Gemini §2, OpenAI §4)."""
from __future__ import annotations

from typing import Any

from icici_breeze_backend.app.core.strike import Strike, parse_strike
from icici_breeze_backend.audit.strategy_builder_audit import quote_row_to_audit
from icici_breeze_backend.app.services.options_strategy_engine.audit_helpers import audit_calc
from icici_breeze_backend.app.services.options_strategy_engine.helpers import (
    nearest_atm,
    quote_from_api,
)
from icici_breeze_backend.app.services.options_strategy_engine.types import EngineContext, QuoteRow, Right


def ingest_chain_rows(rows: list[Any], right: Right) -> dict[tuple[Strike, Right], QuoteRow]:
    cache: dict[tuple[Strike, Right], QuoteRow] = {}
    for row in rows:
        strike = parse_strike(row.get("strike_price"))
        if strike is None:
            continue
        cache[(strike, right)] = quote_from_api(strike, right, row)
    return cache


def record_ingested_strikes(
    audit: Any | None,
    ingested: dict[tuple[Strike, Right], QuoteRow],
    *,
    context: str | None = None,
) -> None:
    if not audit:
        return
    for (strike, right), parsed in sorted(ingested.items()):
        audit.record_strike(
            strike,
            right,
            included=parsed.liquid,
            reason="Two-sided depth (buy_qty>0 and sell_qty>0)" if parsed.liquid else "Missing bid or ask quantity",
            quote=quote_row_to_audit(parsed),
            context=context,
        )


def _resolve_spot_and_atm(ctx: EngineContext, all_strikes: list[Strike], mid: float) -> None:
    spot = ctx.spot
    for q in ctx.cache.values():
        if q.spot_price and q.spot_price > 0:
            spot = q.spot_price
            break
    if spot <= 0:
        spot = mid
    ctx.spot = spot
    ctx.atm_strike = nearest_atm(all_strikes, spot)
    audit_calc(
        ctx,
        "Spot and ATM resolution",
        {"initial_guess": mid},
        {"spot": spot, "atm_strike": ctx.atm_strike},
        rationale="Spot from chain quote payload when available, else scrip midpoint.",
    )


def strikes_from_chain_payload(success: dict[str, Any]) -> list[Strike]:
    """Strike prices present in a built option chain with at least one quoted side."""
    out: list[Strike] = []
    for row in success.get("chain_rows") or []:
        if not isinstance(row, dict):
            continue
        strike = parse_strike(row.get("strike_price"))
        if strike is None:
            continue
        call = row.get("call")
        put = row.get("put")
        if call or put:
            out.append(strike)
    return sorted(set(out))


def build_bulk_chain_cache(ctx: EngineContext) -> None:
    """Primary fetch: routed full chain (websocket / bhavcopy only)."""
    if ctx.audit:
        ctx.audit.record(
            "liquidity_protocol",
            "Fetch full CE and PE option chains",
            {"initial_spot_guess": ctx.spot},
            rationale="Cache-first routed chain populates the bulk strike cache.",
        )

    chain = ctx.processor.get_full_option_chain(
        ctx.user_id,
        ctx.stock_code,
        ctx.exchange_code,
        ctx.expiry_display,
    )
    if chain.get("Status") != 200:
        ctx.halted = True
        ctx.halt_reason = chain.get("Error") or "Unable to fetch option chain."
        if ctx.audit:
            ctx.audit.record("halt", ctx.halt_reason, {"phase": "liquidity_cache"})
        return

    success = chain.get("Success") or {}
    quote_source = success.get("quote_source", "unknown")
    if ctx.audit:
        ctx.audit.record(
            "liquidity_protocol",
            "Ingest routed option chain",
            {
                "quote_source": quote_source,
                "chain_row_count": len(success.get("chain_rows") or []),
            },
            rationale="Bulk cache from websocket or bhavcopy.",
        )

    call_rows: list[Any] = []
    put_rows: list[Any] = []
    for row in success.get("chain_rows") or []:
        strike = row.get("strike_price")
        if row.get("call"):
            call_rows.append({**row["call"], "strike_price": strike})
        if row.get("put"):
            put_rows.append({**row["put"], "strike_price": strike})

    ingested_ce = ingest_chain_rows(call_rows, "Call")
    ingested_pe = ingest_chain_rows(put_rows, "Put")
    ctx.cache.update(ingested_ce)
    ctx.cache.update(ingested_pe)
    record_ingested_strikes(ctx.audit, ingested_ce, context="Fetch full CE chain")
    record_ingested_strikes(ctx.audit, ingested_pe, context="Fetch full PE chain")
    if ctx.progress is not None:
        ctx.progress.tick(phase="fetch_chain", message="Fetching option chain…")

    chain_strikes = strikes_from_chain_payload(success)
    if not chain_strikes:
        ctx.halted = True
        ctx.halt_reason = "No strikes available in the built option chain."
        if ctx.audit:
            ctx.audit.record("halt", ctx.halt_reason, {"phase": "liquidity_cache"})
        return

    ctx.strikes = chain_strikes
    ctx.strike_step = ctx.processor.strike_interval(chain_strikes)
    mid = float(chain_strikes[len(chain_strikes) // 2])
    ctx.search_interval = ctx.processor.search_interval(chain_strikes, mid)

    if success.get("spot_price") is not None:
        try:
            ctx.spot = float(success["spot_price"])
        except (TypeError, ValueError):
            pass
    if success.get("atm_strike") is not None:
        try:
            ctx.atm_strike = parse_strike(success["atm_strike"]) or ctx.atm_strike
        except (TypeError, ValueError):
            pass
    _resolve_spot_and_atm(ctx, chain_strikes, mid)
    range_pad = 3 * ctx.search_interval
    ctx.range_lower = float(ctx.atm_strike) - range_pad
    ctx.range_upper = float(ctx.atm_strike) + range_pad


def finalize_liquidity_cache(ctx: EngineContext) -> None:
    """Halt when no liquid strikes remain after bulk chain ingestion."""
    if not ctx.liquid_ce_strikes and not ctx.liquid_pe_strikes:
        ctx.halted = True
        ctx.halt_reason = "Insufficient market depth: no liquid strikes found."
        if ctx.audit:
            ctx.audit.record("halt", ctx.halt_reason, {"phase": "liquidity_cache"})
        return

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
            rationale="Bulk chain cache drives delta-anchored strategy picks.",
        )


def build_liquidity_cache(ctx: EngineContext) -> None:
    """Bulk chain ingest only; targeted fetches run in orchestrator."""
    build_bulk_chain_cache(ctx)
