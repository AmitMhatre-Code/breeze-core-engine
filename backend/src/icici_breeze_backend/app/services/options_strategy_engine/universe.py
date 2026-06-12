"""Data ingestion and liquidity filtering (Gemini §2, OpenAI §4)."""
from __future__ import annotations

from typing import Any

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.audit.strategy_builder_audit import quote_row_to_audit
from icici_breeze_backend.app.services.processor import OptionChainBackoff, _expiry_display_to_api, processor
from icici_breeze_backend.app.services.user_rate_limit_prefs import get_icici_rate_limit_pause_seconds
from icici_breeze_backend.app.services.options_strategy_engine.audit_helpers import audit_calc
from icici_breeze_backend.app.services.options_strategy_engine.helpers import (
    nearest_atm,
    quote_from_api,
    tail_strikes_needed,
)
from icici_breeze_backend.app.services.options_strategy_engine.types import EngineContext, QuoteRow, Right


def ingest_chain_rows(rows: list[Any], right: Right) -> dict[tuple[int, Right], QuoteRow]:
    cache: dict[tuple[int, Right], QuoteRow] = {}
    for row in rows:
        try:
            strike = int(float(row.get("strike_price", 0)))
        except (TypeError, ValueError):
            continue
        cache[(strike, right)] = quote_from_api(strike, right, row)
    return cache


def chain_strikes_for_right(cache: dict[tuple[int, Right], QuoteRow], right: Right) -> set[int]:
    return {s for (s, r) in cache if r == right}


def missing_tail_pairs(ctx: EngineContext, needed_strikes: list[int]) -> set[tuple[int, Right]]:
    pairs: set[tuple[int, Right]] = set()
    for right in ("Call", "Put"):
        chain = chain_strikes_for_right(ctx.cache, right)
        for s in tail_strikes_needed(needed_strikes, chain):
            if (s, right) not in ctx.cache:
                pairs.add((s, right))
    return pairs


def record_ingested_strikes(
    audit: Any | None,
    ingested: dict[tuple[int, Right], QuoteRow],
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


def fetch_quotes(
    proc: processor,
    user_id: str,
    stock_code: str,
    exchange_code: str,
    expiry_display: str,
    pairs: set[tuple[int, Right]],
    audit: Any | None = None,
    *,
    fetch_reason: str | None = None,
    backoff: OptionChainBackoff | None = None,
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
        if backoff is not None:
            quote = proc.fetch_option_chain_quotes_sb(
                user_id,
                stock_code,
                exchange_code,
                expiry_api,
                right,
                strike_price=str(strike),
                audit=audit,
                audit_rationale=fetch_reason or "Live option quote for liquidity and premium.",
                backoff=backoff,
            )
        else:
            quote = proc.get_quote(
                user_id,
                stock_code,
                expiry_api,
                cfg.OPTIONS,
                right,
                str(strike),
                exchange_code=exchange_code,
                audit=audit,
                audit_rationale=fetch_reason or "Live option quote for liquidity and premium.",
            )
        if audit:
            row = (quote.get("Success") or [None])[0]
            parsed = quote_from_api(strike, right, row) if row else None
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
        cache[(strike, right)] = quote_from_api(strike, right, rows[0])
    return cache


def fetch_full_chain_side(ctx: EngineContext, right: Right, *, fetch_reason: str) -> None:
    if ctx.chain_backoff is None:
        return
    expiry_api = _expiry_display_to_api(ctx.expiry_display)
    quote = ctx.processor.fetch_option_chain_quotes_sb(
        ctx.user_id,
        ctx.stock_code,
        ctx.exchange_code,
        expiry_api,
        right,
        audit=ctx.audit,
        audit_rationale=fetch_reason,
        backoff=ctx.chain_backoff,
    )
    if quote.get("Status") != 200:
        return
    ingested = ingest_chain_rows(quote.get("Success") or [], right)
    ctx.cache.update(ingested)
    record_ingested_strikes(ctx.audit, ingested, context=fetch_reason)


def fetch_missing_tails(ctx: EngineContext, needed_strikes: list[int], *, fetch_reason: str) -> None:
    pairs = missing_tail_pairs(ctx, needed_strikes)
    if not pairs:
        return
    if ctx.audit:
        ctx.audit.record(
            "liquidity_protocol",
            "Fetch missing tail strikes",
            {
                "needed_strikes": needed_strikes,
                "tail_pairs": [{"strike": s, "right": r} for s, r in sorted(pairs)],
            },
            rationale=fetch_reason,
        )
    ctx.cache.update(
        fetch_quotes(
            ctx.processor,
            ctx.user_id,
            ctx.stock_code,
            ctx.exchange_code,
            ctx.expiry_display,
            pairs,
            ctx.audit,
            fetch_reason=fetch_reason,
            backoff=ctx.chain_backoff,
        )
    )


def fetch_pairs_for_strikes(
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
            fetch_quotes(
                ctx.processor,
                ctx.user_id,
                ctx.stock_code,
                ctx.exchange_code,
                ctx.expiry_display,
                new_pairs,
                ctx.audit,
                fetch_reason=fetch_reason,
                backoff=ctx.chain_backoff,
            )
        )


def ensure_quote(ctx: EngineContext, strike: int, right: Right, *, fetch_reason: str) -> QuoteRow | None:
    key = (strike, right)
    if key not in ctx.cache:
        ctx.cache.update(
            fetch_quotes(
                ctx.processor,
                ctx.user_id,
                ctx.stock_code,
                ctx.exchange_code,
                ctx.expiry_display,
                {key},
                ctx.audit,
                fetch_reason=fetch_reason,
                backoff=ctx.chain_backoff,
            )
        )
    return ctx.cache.get(key)


def expand_chain_to_liquidity_boundary(ctx: EngineContext) -> None:
    step = ctx.search_interval
    for right in ("Call", "Put"):
        chain_strikes = sorted(chain_strikes_for_right(ctx.cache, right))
        if not chain_strikes:
            continue
        for start, direction in ((max(chain_strikes), 1), (min(chain_strikes), -1)):
            s = start
            while True:
                next_s = s + direction * step
                if next_s not in ctx.strikes:
                    break
                q = ensure_quote(
                    ctx,
                    next_s,
                    right,
                    fetch_reason=f"Expand {right} chain {'up' if direction > 0 else 'down'} from {s}",
                )
                if q is None or not q.liquid:
                    break
                s = next_s
    if ctx.audit:
        ctx.audit.record(
            "liquidity_protocol",
            "Chain expanded to liquidity boundaries",
            {
                "liquid_ce_strikes": ctx.liquid_ce_strikes,
                "liquid_pe_strikes": ctx.liquid_pe_strikes,
            },
            rationale="Incremental per-strike fetches until first illiquid strike on each side.",
        )


def build_bulk_chain_cache(ctx: EngineContext) -> None:
    """Primary core fetch: two chain-wide quotes (CE + PE) without strike_price."""
    all_strikes = ctx.strikes
    if not all_strikes:
        ctx.halted = True
        ctx.halt_reason = "No strikes found in scrip master for this expiry."
        if ctx.audit:
            ctx.audit.record("halt", ctx.halt_reason, {"phase": "liquidity_cache"})
        return

    mid = float(all_strikes[len(all_strikes) // 2])
    ctx.chain_backoff = OptionChainBackoff(
        pause_seconds=get_icici_rate_limit_pause_seconds(ctx.user_id),
    )

    if ctx.audit:
        ctx.audit.record(
            "liquidity_protocol",
            "Fetch full CE and PE option chains",
            {"strike_count_master": len(all_strikes), "initial_spot_guess": mid},
            rationale="Two chain-wide quotes populate the bulk strike cache (~60 strikes per side).",
        )

    fetch_full_chain_side(ctx, "Call", fetch_reason="Fetch full CE chain")
    fetch_full_chain_side(ctx, "Put", fetch_reason="Fetch full PE chain")

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


def finalize_liquidity_cache(ctx: EngineContext) -> None:
    """Halt when no liquid strikes remain after bulk + targeted ingestion."""
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
            rationale="Bulk cache plus targeted wing fetches drive delta-anchored strategy picks.",
        )


def build_liquidity_cache(ctx: EngineContext) -> None:
    """Bulk chain ingest only; targeted fetches run in orchestrator."""
    build_bulk_chain_cache(ctx)
